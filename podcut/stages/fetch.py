"""Stage 1 - get the source media into job/input/ (URL download or uploaded file)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..ffmpeg import FFmpegError, media_info
from ..job import Job

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _find_media(folder: Path) -> Path | None:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS | AUDIO_EXTS]
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return files[0] if files else None


def _drive_id(url: str) -> str | None:
    for pattern in (r"drive\.google\.com/file/d/([\w-]+)", r"[?&]id=([\w-]+)"):
        m = re.search(pattern, url)
        if m and "google" in url:
            return m.group(1)
    return None


def _download_drive(job: Job, url: str) -> None:
    import gdown

    file_id = _drive_id(url)
    job.message("Downloading from Google Drive")
    out = gdown.download(id=file_id, output=str(job.input_dir) + "/", quiet=True) if file_id else \
        gdown.download(url=url, output=str(job.input_dir) + "/", quiet=True, fuzzy=True)
    if not out:
        raise RuntimeError("Google Drive download failed. Make sure the file is shared as 'Anyone with the link'.")


def _download_ytdlp(job: Job, url: str) -> None:
    import yt_dlp

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job.stage_progress(0.95 * d.get("downloaded_bytes", 0) / total)

    opts = {
        "outtmpl": str(job.input_dir / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 5,
    }
    job.message("Downloading with yt-dlp")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info and not job.meta.get("title"):
        job.state["meta"]["title"] = info.get("title") or ""
    if info and not job.meta.get("show"):
        job.state["meta"]["show"] = info.get("channel") or info.get("uploader") or ""


def run(job: Job) -> None:
    source = job.state["source"]
    existing = _find_media(job.input_dir)
    if existing is None:
        if source["type"] == "file":
            src = Path(source["value"])
            if not src.exists():
                raise FileNotFoundError(f"Input file not found: {src}")
            if src.parent != job.input_dir:
                shutil.copy2(src, job.input_dir / src.name)
        elif source["type"] == "url":
            url = source["value"].strip()
            if _drive_id(url):
                _download_drive(job, url)
            else:
                _download_ytdlp(job, url)
        else:
            raise ValueError(f"Unknown source type {source['type']}")
        existing = _find_media(job.input_dir)
    if existing is None:
        raise RuntimeError("Download finished but no audio/video file was found.")

    try:
        info = media_info(existing)
    except FFmpegError as exc:
        raise RuntimeError(f"The downloaded file is not readable media: {exc}") from exc
    if not info["has_audio"]:
        raise RuntimeError("The input has no audio track - nothing to transcribe.")
    if info["duration"] < 30:
        raise RuntimeError("The input is shorter than 30 seconds - too short to edit.")

    job.state["summary"]["source_file"] = existing.name
    job.state["summary"]["source_duration"] = round(info["duration"], 2)
    job.state["summary"]["source_has_video"] = info["has_video"]
    job.state["summary"]["source_resolution"] = f"{info['width']}x{info['height']}" if info["has_video"] else None
    job.message(f"Source ready: {existing.name} ({info['duration'] / 60:.1f} min)")
