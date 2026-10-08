"""Stage 6 - copy deliverables to output/ and write a human-readable edit report."""
from __future__ import annotations

import shutil

from ..job import Job

OUTPUT_DOCS = {
    "final_video.mp4": "Final edited video (H.264/AAC, YouTube-ready)",
    "final_audio.wav": "Final mixed audio track (voice + music, -14 LUFS)",
    "subtitles.srt": "Subtitles for the final edit (upload to YouTube or use as captions)",
    "thumbnail.jpg": "Suggested thumbnail (1280x720)",
    "youtube_metadata.txt": "AI-written title, description and tags",
    "edit_report.md": "What the AI kept and why (human-readable)",
    "edit_report.json": "Same report, machine-readable",
    "transcript_full.txt": "Full transcript of the source with timestamps",
}


def _ts(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def run(job: Job) -> None:
    w, o = job.work, job.output
    shutil.copy2(w / "subtitles.srt", o / "subtitles.srt")
    shutil.copy2(w / "transcript.txt", o / "transcript_full.txt")

    sel = job.read_json("selection.json")
    blocks = {b["id"]: b for b in job.read_json("blocks.json")}
    scores = {s["id"]: s for s in sel["scores"]}
    plan, yt = sel["plan"], sel["youtube"]
    summary = job.state["summary"]

    cuts = []
    if plan["hook"]:
        b = plan["hook"]["block_id"]
        cuts.append({"role": "hook", "source_start": plan["hook"]["start"], "source_end": plan["hook"]["end"],
                     "score": scores[b]["score"], "topic": scores[b]["topic"], "reason": scores[b]["reason"],
                     "text": blocks[b]["text"]})
    for r in plan["body"]:
        ids = r["block_ids"]
        best = max(ids, key=lambda i: scores[i]["score"])
        cuts.append({"role": "body", "source_start": r["start"], "source_end": r["end"],
                     "score": scores[best]["score"], "topic": scores[best]["topic"],
                     "reason": scores[best]["reason"], "text": " ".join(blocks[i]["text"] for i in ids)})

    timings = {k: v["seconds"] for k, v in job.state["stages"].items()}
    report = {
        "job_id": job.id,
        "source": job.state["source"],
        "meta": job.meta,
        "summary": summary,
        "llm": sel["llm"],
        "youtube": yt,
        "cuts": cuts,
        "stage_seconds": timings,
    }
    job.write_json("edit_report.json", report, base=o)

    (o / "youtube_metadata.txt").write_text(
        f"TITLE\n{yt['title']}\n\nDESCRIPTION\n{yt['description']}\n\nTAGS\n{', '.join(yt['tags'])}\n",
        encoding="utf-8")

    src_min = summary.get("source_duration", 0) / 60
    lines = [
        f"# Edit report - {yt['title']}",
        "",
        f"- **Source:** {summary.get('source_file')} ({src_min:.1f} min, language: {summary.get('language')})",
        f"- **Final video:** {summary.get('final_seconds', 0):.0f} s "
        f"({summary.get('edit_seconds', 0):.0f} s of selected content + intro/outro)",
        f"- **Candidate moments:** {summary.get('moments')} "
        f"({summary.get('moments_scored_by_llm')} scored by "
        f"{(sel['llm'] or {}).get('model', 'heuristic')})",
        f"- **Cuts used:** {len(cuts)}",
        "",
        "| # | Role | Source time | Length | Score | Topic | Why it was kept |",
        "|---|------|-------------|--------|-------|-------|-----------------|",
    ]
    for i, c in enumerate(cuts, 1):
        lines.append(f"| {i} | {c['role']} | {_ts(c['source_start'])} - {_ts(c['source_end'])} | "
                     f"{c['source_end'] - c['source_start']:.1f}s | {c['score']}/10 | {c['topic']} | "
                     f"{c['reason'].replace('|', '/')} |")
    lines += ["", "## Kept dialogue", ""]
    for i, c in enumerate(cuts, 1):
        lines.append(f"**{i}. [{_ts(c['source_start'])}] {c['topic'] or c['role']}**  ")
        lines.append(f"> {c['text']}")
        lines.append("")
    lines += ["## Processing time", ""]
    lines += [f"- {k}: {v if v is not None else '-'} s" for k, v in timings.items()]
    (o / "edit_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    readme = ["PodCut AI - output files", ""] + [f"{k:24s} {v}" for k, v in OUTPUT_DOCS.items() if (o / k).exists()]
    (o / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
    job.state["outputs"] = [
        {"name": k, "description": v, "bytes": (o / k).stat().st_size} for k, v in OUTPUT_DOCS.items() if (o / k).exists()
    ]
    job.message("All outputs ready")
