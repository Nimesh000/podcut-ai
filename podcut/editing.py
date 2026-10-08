"""Pure editing logic (no I/O): transcript -> candidate moments -> chosen cut list.

Kept free of ffmpeg/LLM calls so it is easy to unit-test.
"""
from __future__ import annotations

import re

SENTENCE_END = (".", "?", "!", "।")
AD_WORDS = re.compile(
    r"\b(sponsor(ed)?|promo ?code|use code|discount|download the app|link in (the )?description|"
    r"subscribe|% off|percent off|order now|free delivery|brought to you by)\b",
    re.IGNORECASE,
)


def make_blocks(segments: list[dict], min_s: float = 8, max_s: float = 40) -> list[dict]:
    """Group consecutive transcript segments into 'moments' of roughly min_s..max_s seconds,
    preferring to break at sentence ends or natural pauses."""
    blocks: list[dict] = []
    cur: list[dict] = []

    def close() -> None:
        if cur:
            blocks.append({
                "id": len(blocks),
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "duration": round(cur[-1]["end"] - cur[0]["start"], 2),
                "seg_ids": [s["id"] for s in cur],
                "text": " ".join(s["text"].strip() for s in cur),
            })
            cur.clear()

    for i, seg in enumerate(segments):
        if cur:
            dur_with = seg["end"] - cur[0]["start"]
            gap = seg["start"] - cur[-1]["end"]
            dur_now = cur[-1]["end"] - cur[0]["start"]
            ends_sentence = cur[-1]["text"].strip().endswith(SENTENCE_END)
            if dur_with > max_s or gap > 2.5 or (dur_now >= min_s and (ends_sentence or gap > 0.8)):
                close()
        cur.append(seg)
    close()
    return blocks


def heuristic_score(block: dict, index: int) -> dict:
    """Rule-based fallback score used when the LLM is unavailable or skips a block."""
    words = len(block["text"].split())
    wps = words / max(block["duration"], 0.1)
    score = 5.0
    if 1.6 <= wps <= 3.6:
        score += 1
    if 14 <= block["duration"] <= 35:
        score += 1
    if block["duration"] < 6:
        score -= 2
    if AD_WORDS.search(block["text"]):
        score -= 4
    if index == 0:
        score -= 1
    score = max(1, min(10, round(score)))
    return {"id": block["id"], "score": score, "hook": False, "topic": "", "reason": "heuristic score",
            "source": "heuristic"}


def _hook_from_block(block: dict, segments_by_id: dict, max_s: float) -> dict | None:
    """Take the leading sentences of a block that fit inside max_s seconds."""
    if block["duration"] <= max_s:
        return {"start": block["start"], "end": block["end"], "block_id": block["id"]}
    start = segments_by_id[block["seg_ids"][0]]["start"]
    best = None
    for sid in block["seg_ids"]:
        seg = segments_by_id[sid]
        if seg["end"] - start > max_s:
            break
        if seg["text"].strip().endswith(SENTENCE_END) and seg["end"] - start >= 4:
            best = seg["end"]
    if best is None:
        return None
    return {"start": start, "end": best, "block_id": block["id"]}


def choose_cuts(blocks: list[dict], scores: dict[int, dict], segments: list[dict], cfg: dict) -> dict:
    """Pick a hook + chronological body that lands inside the target duration."""
    out_cfg, ed = cfg["output"], cfg["editing"]
    target, lo, hi = float(out_cfg["target_seconds"]), float(out_cfg["min_seconds"]), float(out_cfg["max_seconds"])
    segments_by_id = {s["id"]: s for s in segments}
    usable = [b for b in blocks if b["duration"] >= 4]

    hook = None
    if ed.get("use_hook", True):
        hook_candidates = sorted(
            (b for b in usable if scores[b["id"]].get("hook") and scores[b["id"]]["score"] >= 7),
            key=lambda b: (-scores[b["id"]]["score"], b["duration"]),
        )
        for b in hook_candidates:
            hook = _hook_from_block(b, segments_by_id, float(ed.get("hook_max_seconds", 20)))
            if hook:
                break

    hook_len = (hook["end"] - hook["start"]) if hook else 0.0
    body_target = target - hook_len
    body_max = hi - hook_len

    ranked = sorted(
        (b for b in usable if not (hook and b["id"] == hook["block_id"])),
        key=lambda b: (-scores[b["id"]]["score"], abs(b["duration"] - 25)),
    )
    chosen: list[dict] = []
    total = 0.0
    for b in ranked:
        if total >= body_target:
            break
        if scores[b["id"]]["score"] <= 3 and total >= lo - hook_len:
            break
        if total + b["duration"] <= body_max:
            chosen.append(b)
            total += b["duration"]
    chosen.sort(key=lambda b: b["start"])

    # merge back-to-back blocks into one continuous cut (fewer jump cuts)
    ranges: list[dict] = []
    for b in chosen:
        if ranges and ranges[-1]["block_ids"][-1] == b["id"] - 1:
            ranges[-1]["end"] = b["end"]
            ranges[-1]["block_ids"].append(b["id"])
        else:
            ranges.append({"start": b["start"], "end": b["end"], "block_ids": [b["id"]]})

    return {
        "hook": hook,
        "body": ranges,
        "hook_seconds": round(hook_len, 2),
        "body_seconds": round(total, 2),
        "total_seconds": round(hook_len + total, 2),
    }
