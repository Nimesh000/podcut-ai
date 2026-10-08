"""A Job is one podcast -> final cut run. It owns a folder with a fixed, documented layout:

jobs/<job_id>/
    job.json              settings + live status (what the web app polls)
    pipeline.log          full log incl. every ffmpeg command
    input/                the downloaded / uploaded source file
    work/                 intermediate files (audio, transcript, segments, cards ...)
    output/               the deliverables (final_video.mp4, final_audio.wav, ...)
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import JOBS_DIR, load_config

STAGES = [
    # key, label, weight in the overall progress bar
    ("fetch", "Fetching media", 6),
    ("audio", "Cleaning audio", 6),
    ("transcribe", "Transcribing (faster-whisper)", 38),
    ("select", "AI picking the best moments", 10),
    ("render", "Rendering the final cut", 36),
    ("package", "Packaging outputs", 4),
]
STAGE_KEYS = [s[0] for s in STAGES]


class Job:
    def __init__(self, job_dir: Path):
        self.dir = Path(job_dir)
        self.id = self.dir.name
        self.input_dir = self.dir / "input"
        self.work = self.dir / "work"
        self.output = self.dir / "output"
        for d in (self.input_dir, self.work, self.output):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_save = 0.0
        self.state: dict[str, Any] = self._read()

    # ------------------------------------------------------------------ creation
    @classmethod
    def create(cls, source: dict, meta: dict, overrides: dict | None = None) -> "Job":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        job_dir = JOBS_DIR / f"{stamp}-{secrets.token_hex(3)}"
        job = cls(job_dir)
        job.state = {
            "id": job.id,
            "created_at": time.time(),
            "status": "queued",
            "source": source,           # {"type": "url"|"file", "value": ...}
            "meta": meta,               # title, show, guest, guest_role, host
            "overrides": overrides or {},
            "stage": None,
            "stages": {k: {"status": "pending", "progress": 0.0, "seconds": None} for k in STAGE_KEYS},
            "progress": 0.0,
            "message": "Waiting in queue",
            "error": None,
            "outputs": [],
            "summary": {},
        }
        job.save(force=True)
        return job

    @classmethod
    def load(cls, job_id: str) -> "Job":
        job_dir = JOBS_DIR / job_id
        if not (job_dir / "job.json").exists():
            raise FileNotFoundError(job_id)
        return cls(job_dir)

    # ------------------------------------------------------------------ state
    def _read(self) -> dict:
        path = self.dir / "job.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {}

    def save(self, force: bool = False) -> None:
        with self._lock:
            now = time.time()
            if not force and now - self._last_save < 0.5:
                return
            self._last_save = now
            tmp = self.dir / "job.json.tmp"
            tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
            # On Windows the replace can briefly fail while the web server is reading the file
            for attempt in range(20):
                try:
                    tmp.replace(self.dir / "job.json")
                    break
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))

    @property
    def config(self) -> dict:
        return load_config(self.state.get("overrides"))

    @property
    def meta(self) -> dict:
        return self.state.get("meta", {})

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        with self._lock, open(self.dir / "pipeline.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            print(f"[{self.id}] {msg}", flush=True)
        except (UnicodeEncodeError, OSError):  # e.g. Windows console code pages
            print(f"[{self.id}] {msg}".encode("ascii", "replace").decode(), flush=True)

    def message(self, msg: str) -> None:
        self.state["message"] = msg
        self.log(msg)
        self.save()

    def stage_progress(self, frac: float) -> None:
        stage = self.state.get("stage")
        if not stage:
            return
        self.state["stages"][stage]["progress"] = round(max(0.0, min(1.0, frac)), 4)
        self._recompute()
        self.save()

    def _recompute(self) -> None:
        total = sum(w for _, _, w in STAGES)
        done = 0.0
        for key, _, weight in STAGES:
            st = self.state["stages"][key]
            if st["status"] in ("done", "skipped"):
                done += weight
            elif st["status"] == "running":
                done += weight * st["progress"]
        self.state["progress"] = round(done / total, 4)

    def stage_done(self, key: str) -> bool:
        return self.state["stages"][key]["status"] == "done"

    def set_stage_status(self, key: str, status: str, seconds: float | None = None) -> None:
        st = self.state["stages"][key]
        st["status"] = status
        if status == "running":
            self.state["stage"] = key
            st["progress"] = 0.0
        if status == "done":
            st["progress"] = 1.0
        if seconds is not None:
            st["seconds"] = round(seconds, 1)
        self._recompute()
        self.save(force=True)

    # ------------------------------------------------------------------ files
    def write_json(self, rel: str, data: Any, base: Path | None = None) -> Path:
        path = (base or self.work) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def read_json(self, rel: str, base: Path | None = None) -> Any:
        return json.loads(((base or self.work) / rel).read_text(encoding="utf-8"))
