"""Stage 2 - extract speech audio and clean it (rumble, hiss, level)."""
from __future__ import annotations

from .. import ffmpeg
from ..job import Job


def source_path(job: Job):
    return job.input_dir / job.state["summary"]["source_file"]


def run(job: Job) -> None:
    cfg = job.config["audio"]
    src = source_path(job)
    duration = job.state["summary"]["source_duration"]

    filters = [f"highpass=f={int(cfg['highpass_hz'])}", "lowpass=f=14000"]
    if cfg.get("denoise", True):
        # FFT denoiser with noise-floor tracking; gentle enough to keep voices natural
        filters.append(f"afftdn=nr={float(cfg['denoise_strength'])}:nf=-40:tn=1")
    # light leveling so quiet and loud speakers sit closer together
    filters.append("dynaudnorm=f=250:g=15:p=0.9:m=8")

    job.message("Cleaning speech audio (high-pass, denoise, leveling)")
    ffmpeg.run(
        ["-i", src, "-vn", "-ac", "1", "-ar", "48000", "-af", ",".join(filters), "-c:a", "pcm_s16le",
         job.work / "voice_clean.wav"],
        total_seconds=duration, on_progress=lambda f: job.stage_progress(0.85 * f), log=job.log,
    )
    # 16 kHz copy for the speech recogniser
    ffmpeg.run(["-i", job.work / "voice_clean.wav", "-ar", "16000", "-c:a", "pcm_s16le", job.work / "asr.wav"],
               log=job.log)
    job.message("Audio cleaned")
