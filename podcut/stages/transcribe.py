"""Stage 3 - speech-to-text with word timestamps using faster-whisper."""
from __future__ import annotations

from ..job import Job
from ..subtitles import fmt_srt_time


def _load_model(name: str, device: str, log):
    from faster_whisper import WhisperModel

    attempts = []
    if device in ("auto", "cuda"):
        attempts.append(("cuda", "float16"))
    if device in ("auto", "cpu"):
        attempts.append(("cpu", "int8"))
    last_err = None
    for dev, compute in attempts:
        try:
            model = WhisperModel(name, device=dev, compute_type=compute)
            log(f"faster-whisper '{name}' loaded on {dev} ({compute})")
            return model, dev
        except Exception as exc:  # CUDA missing / cuDNN not found / etc.
            last_err = exc
            log(f"Could not load whisper on {dev}: {str(exc)[:200]}")
    raise RuntimeError(f"Could not load the whisper model: {last_err}")


def run(job: Job) -> None:
    cfg = job.config["transcribe"]
    duration = job.state["summary"]["source_duration"]
    job.message(f"Loading speech model '{cfg['model']}' (first run downloads it)")
    model, device = _load_model(cfg["model"], cfg.get("device", "auto"), job.log)

    job.message(f"Transcribing {duration / 60:.1f} min of audio on {device.upper()}")
    segments_iter, info = model.transcribe(
        str(job.work / "asr.wav"),
        language=cfg.get("language") or None,
        beam_size=int(cfg.get("beam_size", 5)),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,
    )
    segments = []
    for seg in segments_iter:
        words = [
            {"s": round(w.start, 3), "e": round(w.end, 3), "w": w.word}
            for w in (seg.words or []) if w.end > w.start
        ]
        text = seg.text.strip()
        if not text or not words:
            continue
        segments.append({
            "id": len(segments),
            "start": round(words[0]["s"], 3),
            "end": round(words[-1]["e"], 3),
            "text": text,
            "words": words,
        })
        job.stage_progress(min(0.99, seg.end / max(duration, 1)))
        if len(segments) % 25 == 0:
            job.message(f"Transcribed {seg.end / 60:.1f} / {duration / 60:.1f} min")

    if not segments:
        raise RuntimeError("No speech was detected in this file.")

    transcript = {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": duration,
        "model": cfg["model"],
        "segments": segments,
    }
    job.write_json("transcript.json", transcript)

    # human-readable copies
    lines = [f"[{fmt_srt_time(s['start'])[:8]}] {s['text']}" for s in segments]
    (job.work / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")
    srt = []
    for i, s in enumerate(segments, 1):
        srt.append(f"{i}\n{fmt_srt_time(s['start'])} --> {fmt_srt_time(s['end'])}\n{s['text']}\n")
    (job.work / "transcript_full.srt").write_text("\n".join(srt), encoding="utf-8")

    words = sum(len(s["words"]) for s in segments)
    job.state["summary"]["language"] = info.language
    job.state["summary"]["words"] = words
    job.message(f"Transcript ready: {len(segments)} segments, {words} words, language '{info.language}'")
