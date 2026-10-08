"""Stage 4 - the LLM reads the transcript and scores every candidate moment; Python then
assembles the edit deterministically so the result always fits the target length."""
from __future__ import annotations

from ..editing import choose_cuts, heuristic_score, make_blocks
from ..job import Job
from ..llm import LLM, LLMUnavailable

SYSTEM_PROMPT = """You are a senior video editor who cuts long podcasts and interviews into
short, gripping YouTube edits (2-3 minutes). You will receive numbered transcript blocks.
Score EVERY block for how much it deserves a place in the short edit.

Scoring (1-10):
- 9-10: a quotable insight, strong opinion, emotional or funny story, surprising fact - works on its own
- 6-8: solid, clear, on-topic content that a viewer would enjoy
- 3-5: filler, small talk, setup that needs missing context, rambling
- 1-2: ads / sponsor reads / promotions, greetings and goodbyes, housekeeping, cross-talk, broken sentences

Also set "hook": true only if the block contains a punchy, self-contained line that would make a viewer
keep watching if it were the first 10 seconds of the video.

Return ONLY JSON of the form:
{"blocks": [{"id": <int>, "score": <int 1-10>, "hook": <bool>, "topic": "<3-6 words>", "reason": "<max 15 words>"}]}
Include every block id you were given, exactly once."""

META_PROMPT = """You write YouTube metadata for short podcast highlight videos.
Given the transcript of the final edit, return ONLY JSON:
{"title": "<catchy but honest title, max 70 chars>", "description": "<2-3 sentence description>",
 "tags": ["<5-8 short tags>"]}"""


def _fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _chunks(blocks: list[dict], max_tokens: int) -> list[list[dict]]:
    chunks, cur, size = [], [], 0
    for b in blocks:
        cost = len(b["text"]) // 4 + 20
        if cur and size + cost > max_tokens:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += cost
    if cur:
        chunks.append(cur)
    return chunks


def _context(job: Job) -> str:
    m = job.meta
    parts = []
    if m.get("title"):
        parts.append(f"Episode title: {m['title']}")
    if m.get("show"):
        parts.append(f"Show: {m['show']}")
    if m.get("guest"):
        parts.append(f"Guest: {m['guest']}" + (f" ({m['guest_role']})" if m.get("guest_role") else ""))
    if m.get("host"):
        parts.append(f"Host: {m['host']}")
    return "\n".join(parts)


def score_with_llm(job: Job, blocks: list[dict], llm: LLM) -> dict[int, dict]:
    cfg = job.config["llm"]
    chunks = _chunks(blocks, int(cfg.get("chunk_tokens", 3500)))
    scores: dict[int, dict] = {}
    for n, chunk in enumerate(chunks, 1):
        job.message(f"AI is reviewing transcript part {n}/{len(chunks)} ({llm.provider}: {llm.model})")
        lines = [f"[{b['id']}] ({_fmt(b['start'])}, {b['duration']:.0f}s) {b['text']}" for b in chunk]
        user = (_context(job) + "\n\n" if _context(job) else "") + "Transcript blocks:\n" + "\n".join(lines)
        try:
            data = llm.json_chat(SYSTEM_PROMPT, user)
        except Exception as exc:
            job.log(f"LLM request failed for part {n}: {exc}")
            continue
        valid_ids = {b["id"] for b in chunk}
        for item in data.get("blocks", []) if isinstance(data.get("blocks"), list) else []:
            try:
                bid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if bid not in valid_ids:
                continue
            try:
                score = max(1, min(10, int(round(float(item.get("score", 5))))))
            except (TypeError, ValueError):
                score = 5
            scores[bid] = {
                "id": bid,
                "score": score,
                "hook": bool(item.get("hook", False)),
                "topic": str(item.get("topic", ""))[:60],
                "reason": str(item.get("reason", ""))[:160],
                "source": "llm",
            }
        job.stage_progress(0.85 * n / len(chunks))
    return scores


def run(job: Job) -> None:
    cfg = job.config
    transcript = job.read_json("transcript.json")
    segments = transcript["segments"]
    blocks = make_blocks(segments, cfg["editing"]["block_min_seconds"], cfg["editing"]["block_max_seconds"])
    job.log(f"Built {len(blocks)} candidate moments from {len(segments)} transcript segments")

    llm = None
    scores: dict[int, dict] = {}
    try:
        llm = LLM(cfg["llm"], log=job.log)
        scores = score_with_llm(job, blocks, llm)
    except LLMUnavailable as exc:
        job.message(f"LLM not available ({exc}) - using rule-based selection")

    missing = [b for b in blocks if b["id"] not in scores]
    for b in missing:
        scores[b["id"]] = heuristic_score(b, b["id"])
    llm_count = len(blocks) - len(missing)
    if missing:
        job.log(f"{len(missing)} moments scored by fallback heuristic")

    plan = choose_cuts(blocks, scores, segments, cfg)
    if not plan["body"]:
        raise RuntimeError("Could not choose any segments - transcript may be empty or too short.")

    # YouTube metadata from the final edit text
    meta = {}
    chosen_ids = [i for r in plan["body"] for i in r["block_ids"]]
    edit_text = " ".join(b["text"] for b in blocks if b["id"] in chosen_ids)
    if llm is not None and llm_count:
        try:
            job.message("AI is writing a title & description")
            meta = llm.json_chat(META_PROMPT, (_context(job) + "\n\n" + edit_text)[:12000])
        except Exception as exc:
            job.log(f"Metadata generation failed: {exc}")
    meta = {
        "title": str(meta.get("title") or job.meta.get("title") or "Podcast Highlights")[:100],
        "description": str(meta.get("description") or ""),
        "tags": [str(t) for t in meta.get("tags", [])][:10] if isinstance(meta.get("tags"), list) else [],
    }

    job.write_json("blocks.json", blocks)
    job.write_json("selection.json", {
        "llm": {"provider": llm.provider, "model": llm.model} if llm else None,
        "scored_by_llm": llm_count,
        "scored_by_heuristic": len(missing),
        "scores": [scores[b["id"]] for b in blocks],
        "plan": plan,
        "youtube": meta,
    })
    job.state["summary"].update({
        "moments": len(blocks),
        "moments_scored_by_llm": llm_count,
        "edit_seconds": plan["total_seconds"],
        "cuts": len(plan["body"]) + (1 if plan["hook"] else 0),
        "ai_title": meta["title"],
    })
    job.message(
        f"Selected {len(plan['body'])} cuts" + (" + cold-open hook" if plan["hook"] else "")
        + f" = {plan['total_seconds']:.0f}s of content"
    )
