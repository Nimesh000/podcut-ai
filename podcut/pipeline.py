"""Runs the stages in order. Each stage starts automatically when the previous one finishes;
finished stages are skipped on a re-run, so a failed job can be resumed from where it stopped."""
from __future__ import annotations

import time
import traceback

from .ffmpeg import require_ffmpeg
from .job import STAGES, Job
from .stages import audio, fetch, package, render, select, transcribe

RUNNERS = {
    "fetch": fetch.run,
    "audio": audio.run,
    "transcribe": transcribe.run,
    "select": select.run,
    "render": render.run,
    "package": package.run,
}


def run_job(job: Job, force_from: str | None = None) -> bool:
    """Run (or resume) a job. Returns True on success."""
    if force_from:
        reset = False
        for key, _, _ in STAGES:
            reset = reset or key == force_from
            if reset:
                job.state["stages"][key].update(status="pending", progress=0.0, seconds=None)

    job.state.update(status="running", error=None)
    job.save(force=True)
    started = time.time()
    try:
        require_ffmpeg()
        for key, label, _ in STAGES:
            if job.stage_done(key):
                continue
            job.set_stage_status(key, "running")
            job.message(label)
            t0 = time.time()
            RUNNERS[key](job)
            job.set_stage_status(key, "done", seconds=time.time() - t0)
        job.state["summary"]["processing_seconds"] = round(time.time() - started, 1)
        job.state.update(status="done", stage=None, progress=1.0, message="Your final cut is ready")
        job.save(force=True)
        job.log("Job finished")
        return True
    except Exception as exc:
        stage = job.state.get("stage")
        if stage:
            job.state["stages"][stage]["status"] = "error"
        job.state.update(status="error", error=f"{type(exc).__name__}: {exc}",
                         message=f"Failed during '{stage}'")
        job.log(traceback.format_exc())
        job.save(force=True)
        return False
