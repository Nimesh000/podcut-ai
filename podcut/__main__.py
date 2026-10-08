"""Command-line interface.

    python -m podcut run <url-or-file> [--title ..] [--guest ..] [--role ..] [--host ..] [--show ..]
                                       [--seconds 150] [--model small] [--no-music] [--no-subs]
    python -m podcut resume <job_id> [--from select]
    python -m podcut serve [--port 8000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .job import STAGE_KEYS, Job
from .pipeline import run_job


def build_overrides(seconds: int | None = None, model: str | None = None, language: str | None = None,
                    music: bool = True, subs: bool = True, hook: bool = True) -> dict:
    o: dict = {"audio": {"music": music}, "subtitles": {"burn_in": subs}, "editing": {"use_hook": hook},
               "transcribe": {}}
    if seconds:
        o["output"] = {"target_seconds": seconds, "min_seconds": round(seconds * 0.75),
                       "max_seconds": round(seconds * 1.25)}
    if model:
        o["transcribe"]["model"] = model
    if language:
        o["transcribe"]["language"] = language
    return o


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="podcut", description="AI podcast highlight editor")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="process a URL or local file")
    r.add_argument("source")
    r.add_argument("--title", default="")
    r.add_argument("--show", default="")
    r.add_argument("--guest", default="")
    r.add_argument("--role", default="")
    r.add_argument("--host", default="")
    r.add_argument("--logo", default="")
    r.add_argument("--seconds", type=int)
    r.add_argument("--model")
    r.add_argument("--language")
    r.add_argument("--no-music", action="store_true")
    r.add_argument("--no-subs", action="store_true")
    r.add_argument("--no-hook", action="store_true")

    rs = sub.add_parser("resume", help="resume / re-run an existing job")
    rs.add_argument("job_id")
    rs.add_argument("--from", dest="from_stage", choices=STAGE_KEYS)

    sv = sub.add_parser("serve", help="start the web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)

    a = p.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("app.server:app", host=a.host, port=a.port)
        return 0
    if a.cmd == "resume":
        job = Job.load(a.job_id)
        ok = run_job(job, force_from=a.from_stage)
    else:
        is_url = a.source.startswith(("http://", "https://"))
        if not is_url and not Path(a.source).exists():
            print(f"File not found: {a.source}", file=sys.stderr)
            return 2
        source = {"type": "url", "value": a.source} if is_url else \
            {"type": "file", "value": str(Path(a.source).resolve())}
        meta = {"title": a.title, "show": a.show, "guest": a.guest, "guest_role": a.role, "host": a.host,
                "logo": str(Path(a.logo).resolve()) if a.logo else ""}
        overrides = build_overrides(a.seconds, a.model, a.language, not a.no_music, not a.no_subs, not a.no_hook)
        job = Job.create(source, meta, overrides)
        print(f"Job {job.id} -> {job.dir}")
        ok = run_job(job)
    print("\nDONE ->", job.output if ok else f"FAILED: {job.state.get('error')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
