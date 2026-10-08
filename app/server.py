"""PodCut AI web app (FastAPI).

Start with:  python -m podcut serve      (or run.bat / run.sh)
Then open:   http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from podcut import __version__
from podcut.__main__ import build_overrides
from podcut.config import JOBS_DIR, get_api_key
from podcut.ffmpeg import FFmpegError, require_ffmpeg
from podcut.job import STAGES, Job
from podcut.llm import PROVIDERS, detect_provider
from podcut.pipeline import run_job
from podcut.stages.package import OUTPUT_DOCS

STATIC = Path(__file__).parent / "static"

# ----------------------------------------------------------------------------- worker
# One job at a time: transcription and rendering are CPU/GPU heavy, so a simple queue
# keeps the machine responsive and makes progress reporting predictable.
_queue: "queue.Queue[tuple[str, str | None]]" = queue.Queue()


def _worker() -> None:
    while True:
        job_id, from_stage = _queue.get()
        try:
            run_job(Job.load(job_id), force_from=from_stage)
        except Exception as exc:  # never let the worker thread die
            print(f"worker error for {job_id}: {exc}")
        finally:
            _queue.task_done()


def _startup() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # jobs that were running when the server stopped are marked so they can be resumed
    for d in JOBS_DIR.iterdir():
        if (d / "job.json").exists():
            job = Job(d)
            if job.state.get("status") in ("running", "queued"):
                job.state.update(status="error", error="Server was restarted while this job was running.",
                                 message="Interrupted - press Retry to resume")
                job.save(force=True)
    threading.Thread(target=_worker, daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    yield


app = FastAPI(title="PodCut AI", version=__version__, lifespan=lifespan)


# ----------------------------------------------------------------------------- api
def _public(job: Job) -> dict:
    s = dict(job.state)
    s["stage_labels"] = {k: label for k, label, _ in STAGES}
    s["queue_position"] = None
    return s


@app.get("/api/health")
def health() -> dict:
    try:
        require_ffmpeg()
        ff = True
        ff_msg = "ok"
    except FFmpegError as exc:
        ff, ff_msg = False, str(exc)
    key = get_api_key()
    provider = detect_provider(key) if key else None
    return {
        "version": __version__,
        "ffmpeg": ff,
        "ffmpeg_message": ff_msg,
        "llm_configured": bool(key),
        "llm_provider": provider,
        "llm_model": PROVIDERS[provider]["model"] if provider else None,
        "queue": _queue.qsize(),
    }


@app.post("/api/jobs")
async def create_job(
    url: str = Form(""),
    file: UploadFile | None = File(None),
    logo: UploadFile | None = File(None),
    title: str = Form(""),
    show: str = Form(""),
    guest: str = Form(""),
    guest_role: str = Form(""),
    host: str = Form(""),
    seconds: int = Form(150),
    model: str = Form("small"),
    language: str = Form(""),
    music: bool = Form(True),
    subtitles: bool = Form(True),
    hook: bool = Form(True),
) -> dict:
    url = url.strip()
    has_file = file is not None and file.filename
    if not url and not has_file:
        raise HTTPException(400, "Paste a link or upload a file.")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "The link must start with http:// or https://")
    if not 30 <= seconds <= 600:
        raise HTTPException(400, "Target length must be between 30 and 600 seconds.")
    if model not in ("tiny", "base", "small", "medium", "large-v3"):
        raise HTTPException(400, "Unknown whisper model.")

    meta = {"title": title.strip(), "show": show.strip(), "guest": guest.strip(),
            "guest_role": guest_role.strip(), "host": host.strip(), "logo": ""}
    overrides = build_overrides(seconds, model, language.strip() or None, music, subtitles, hook)
    source = {"type": "url", "value": url} if url else {"type": "file", "value": ""}
    job = Job.create(source, meta, overrides)

    if has_file:
        safe = Path(file.filename).name
        dest = job.input_dir / safe
        with open(dest, "wb") as fh:
            shutil.copyfileobj(file.file, fh, length=4 * 1024 * 1024)
        job.state["source"] = {"type": "file", "value": str(dest), "name": safe}
    if logo is not None and logo.filename:
        lp = job.work / ("logo" + Path(logo.filename).suffix.lower())
        with open(lp, "wb") as fh:
            shutil.copyfileobj(logo.file, fh)
        job.state["meta"]["logo"] = str(lp)
    job.save(force=True)
    _queue.put((job.id, None))
    return {"id": job.id}


@app.get("/api/jobs")
def list_jobs(limit: int = 20) -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    items = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True)[:limit]:
        p = d / "job.json"
        if p.exists():
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            items.append({"id": s["id"], "status": s["status"], "progress": s["progress"],
                          "title": s["summary"].get("ai_title") or s["meta"].get("title")
                          or s["source"].get("name") or s["source"].get("value"),
                          "created_at": s["created_at"]})
    return items


def _load(job_id: str) -> Job:
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "bad id")
    try:
        return Job.load(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Job not found")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return _public(_load(job_id))


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str) -> JSONResponse:
    job = _load(job_id)
    p = job.output / "edit_report.json"
    if not p.exists():
        raise HTTPException(404, "Report not ready")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
def get_log(job_id: str, lines: int = 80) -> str:
    job = _load(job_id)
    p = job.dir / "pipeline.log"
    if not p.exists():
        return ""
    out = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if "] $ ffmpeg" not in ln]
    return "\n".join(out[-lines:])


@app.get("/api/jobs/{job_id}/files/{name}")
def get_file(job_id: str, name: str, download: bool = False) -> FileResponse:
    job = _load(job_id)
    if name not in OUTPUT_DOCS:
        raise HTTPException(404, "Unknown file")
    p = job.output / name
    if not p.exists():
        raise HTTPException(404, "File not ready")
    return FileResponse(p, filename=name if download else None)


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str, from_stage: str | None = None) -> dict:
    job = _load(job_id)
    if job.state["status"] in ("running", "queued"):
        raise HTTPException(409, "Job is already running")
    job.state.update(status="queued", message="Waiting in queue", error=None)
    job.save(force=True)
    _queue.put((job.id, from_stage))
    return {"id": job.id}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
