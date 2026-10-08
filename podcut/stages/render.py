"""Stage 5 - turn the edit plan into the final video.

1. draw intro / outro cards, lower third and thumbnail
2. render every cut as its own small clip (frame-accurate, same codec settings)
3. join clips (concat demuxer) -> one timeline
4. single final encode: lower third + burned subtitles + ducked music + loudness normalisation
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import cards, ffmpeg
from ..assets import ensure_music, fonts
from ..config import MUSIC_DIR, ROOT
from ..job import Job
from ..subtitles import build_cues, remap_words, write_ass, write_srt
from .audio import source_path

MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")


def _music_file(cfg: dict, log=print) -> Path | None:
    if not cfg["audio"].get("music", True):
        return None
    custom = cfg["audio"].get("music_file")
    if custom:
        p = Path(custom)
        p = p if p.is_absolute() else ROOT / p
        if p.exists():
            return p
    files = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in MUSIC_EXTS and not p.name.startswith("_"))
    return files[0] if files else ensure_music(log)


def _db(db: float) -> float:
    return round(10 ** (db / 20), 5)


def _frames(d: float, fps: int) -> float:
    return max(1, round(d * fps)) / fps


def build_timeline(plan: dict, cfg: dict) -> list[dict]:
    fps = int(cfg["output"]["fps"])
    ed = cfg["editing"]
    timeline: list[dict] = []
    t = 0.0

    def add_speech(start: float, end: float, role: str) -> None:
        nonlocal t
        s = max(0.0, start - float(ed["pad_before"]))
        d = _frames(end + float(ed["pad_after"]) - s, fps)
        timeline.append({"kind": "speech", "role": role, "src_start": round(s, 3), "src_end": round(s + d, 3),
                         "duration": d, "out_start": round(t, 3)})
        t += d

    def add_card(name: str, seconds: float) -> None:
        nonlocal t
        d = _frames(seconds, fps)
        timeline.append({"kind": "card", "role": name, "duration": d, "out_start": round(t, 3)})
        t += d

    if plan.get("hook"):
        add_speech(plan["hook"]["start"], plan["hook"]["end"], "hook")
    add_card("intro", float(cfg["cards"]["intro_seconds"]))
    for r in plan["body"]:
        add_speech(r["start"], r["end"], "body")
    add_card("outro", float(cfg["cards"]["outro_seconds"]))
    return timeline


def _volume_expr(timeline: list[dict], bed: float, card: float, ramp: float = 0.7) -> str:
    windows = []
    for c in timeline:
        if c["kind"] == "card":
            a, b = c["out_start"], c["out_start"] + c["duration"]
            windows.append(f"clip((t-{a:.3f}+{ramp})/{ramp},0,1)*clip(({b:.3f}+{ramp}-t)/{ramp},0,1)")
    if not windows:
        return f"{bed}"
    return f"{bed}+({card}-{bed})*min(1,{'+'.join(windows)})"


def run(job: Job) -> None:
    cfg = job.config
    out, au = cfg["output"], cfg["audio"]
    W, H, fps = int(out["width"]), int(out["height"]), int(out["fps"])
    work = job.work
    clips_dir = work / "clips"
    clips_dir.mkdir(exist_ok=True)
    selection = job.read_json("selection.json")
    plan = selection["plan"]
    transcript = job.read_json("transcript.json")
    src = source_path(job)
    has_video = job.state["summary"].get("source_has_video", True)
    meta = job.meta
    title = meta.get("title") or selection["youtube"]["title"]
    logo = Path(meta["logo"]) if meta.get("logo") else None

    # ---------------------------------------------------------------- 1. graphics
    job.message("Designing intro, outro and name tag")
    font_set = fonts(job.log)
    guest_line = ""
    if meta.get("guest"):
        guest_line = f"with {meta['guest']}" + (f", {meta['guest_role']}" if meta.get("guest_role") else "")
    cards.title_card(work / "intro.png", W, H, title, meta.get("show") or "Podcast highlights", guest_line,
                     cfg["cards"], logo)
    cards.outro_card(work / "outro.png", W, H, cfg["cards"]["outro_text"], cfg["cards"]["outro_subtext"],
                     cfg["cards"], logo)
    lower = None
    if meta.get("guest") and has_video:
        lower = work / "lower_third.png"
        cards.lower_third(lower, W, H, meta["guest"], meta.get("guest_role", ""), cfg["cards"])

    timeline = build_timeline(plan, cfg)
    total = sum(c["duration"] for c in timeline)
    job.write_json("timeline.json", timeline)

    # ---------------------------------------------------------------- 2. clips
    vf_fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
              f"fps={fps},setsar=1,format=yuv420p")
    venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-g", str(fps * 2), "-pix_fmt", "yuv420p"]
    aenc = ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1"]
    names = []
    done_time = 0.0
    for i, clip in enumerate(timeline):
        name = f"clip_{i:03d}.mkv"
        names.append(name)
        d = clip["duration"]
        dst = clips_dir / name
        if clip["kind"] == "card":
            img = work / f"{clip['role']}.png"
            fade = f"fade=t=in:st=0:d=0.5,fade=t=out:st={d - 0.5:.3f}:d=0.5"
            args = ["-loop", "1", "-framerate", str(fps), "-i", img,
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-t", f"{d:.3f}", "-vf", f"{vf_fit},{fade}", *venc, *aenc, dst]
        else:
            s = clip["src_start"]
            afade = f"afade=t=in:st=0:d=0.04,afade=t=out:st={max(0, d - 0.08):.3f}:d=0.08"
            if has_video:
                args = ["-ss", f"{s:.3f}", "-i", src, "-ss", f"{s:.3f}", "-i", work / "voice_clean.wav",
                        "-t", f"{d:.3f}", "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", vf_fit, "-af", afade, *venc, *aenc, dst]
            else:  # audio-only podcast -> branded background with live waveform
                wave_h = H // 4
                fc = (f"[1:a]{afade},asplit=2[a][w];[w]showwaves=s={W}x{wave_h}:mode=cline:rate={fps}:"
                      f"colors={cfg['cards']['accent_color'].replace('#', '0x')}[wv];"
                      f"[0:v]{vf_fit},eq=brightness=-0.15[bg];[bg][wv]overlay=0:{H - wave_h - H // 10},format=yuv420p[v]")
                args = ["-loop", "1", "-framerate", str(fps), "-i", work / "intro.png",
                        "-ss", f"{s:.3f}", "-i", work / "voice_clean.wav", "-t", f"{d:.3f}",
                        "-filter_complex", fc, "-map", "[v]", "-map", "[a]", *venc, *aenc, dst]
        ffmpeg.run(args, log=job.log)
        done_time += d
        job.stage_progress(0.45 * done_time / total)
        job.message(f"Rendered clip {i + 1}/{len(timeline)} ({clip['role']})")

    (work / "concat.txt").write_text("".join(f"file 'clips/{n}'\n" for n in names), encoding="utf-8")

    # ---------------------------------------------------------------- 3. subtitles
    groups = remap_words(transcript, timeline)
    cues = build_cues(groups, int(cfg["subtitles"]["max_words_per_line"]),
                      float(cfg["subtitles"]["max_line_seconds"]))
    write_srt(cues, work / "subtitles.srt")
    family = font_set["family"] if cfg["subtitles"]["font"] == "Inter" else cfg["subtitles"]["font"]
    write_ass(cues, work / "subs.ass", W, H, family, int(cfg["subtitles"]["font_size"]))
    fonts_dst = work / "fonts"
    fonts_dst.mkdir(exist_ok=True)
    for w in ("Bold", "SemiBold", "Regular"):
        f = Path(font_set.get(w) or "")
        if f.is_file():
            shutil.copy2(f, fonts_dst / f.name)

    # ---------------------------------------------------------------- 4. final encode
    music = _music_file(cfg, job.log)
    inputs = ["-f", "concat", "-safe", "0", "-i", "concat.txt"]
    idx = 1
    music_idx = lt_idx = None
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        music_idx, idx = idx, idx + 1
    if lower:
        inputs += ["-loop", "1", "-framerate", str(fps), "-i", lower.name]
        lt_idx, idx = idx, idx + 1

    vchain = "[0:v]setpts=PTS-STARTPTS"
    fc = []
    if lt_idx is not None:
        body_start = next((c["out_start"] for c in timeline if c["role"] == "body"), 0.0)
        a, b = body_start + 1.0, body_start + 6.0
        fc.append(f"[{lt_idx}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.4:alpha=1,"
                  f"fade=t=out:st={b:.2f}:d=0.4:alpha=1[lt]")
        fc.append(f"{vchain}[base]")
        vchain = "[base][lt]overlay=0:0:shortest=1"
    if cfg["subtitles"].get("burn_in", True) and cues:
        vchain += ",subtitles=subs.ass:fontsdir=fonts"
    fc.append(f"{vchain},format=yuv420p[v]")

    loud = f"loudnorm=I={float(au['loudness_lufs'])}:TP=-1.5:LRA=11,aresample=48000"
    if music_idx is not None:
        bed, card = _db(float(au["music_volume_db"])), _db(float(au["music_card_volume_db"]))
        fc.append("[0:a]asetpts=PTS-STARTPTS,asplit=2[vo][key]")
        fc.append("[vo]pan=stereo|c0=c0|c1=c0[vos]")
        fc.append("[key]pan=stereo|c0=c0|c1=c0[keys]")
        fc.append(f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                  f"asetpts=PTS-STARTPTS,volume='{_volume_expr(timeline, bed, card)}':eval=frame,"
                  f"afade=t=in:st=0:d=1,afade=t=out:st={max(0, total - 2.5):.3f}:d=2.5[mus]")
        fc.append("[mus][keys]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[duck]")
        fc.append(f"[vos][duck]amix=inputs=2:duration=first:normalize=0,{loud},asplit=2[a1][a2]")
    else:
        fc.append(f"[0:a]asetpts=PTS-STARTPTS,pan=stereo|c0=c0|c1=c0,{loud},asplit=2[a1][a2]")

    job.message(f"Final render: {total:.0f}s at {W}x{H} with subtitles and music")
    ffmpeg.run(
        [*inputs, "-filter_complex", ";".join(fc),
         "-map", "[v]", "-map", "[a1]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", out["preset"], "-crf", str(out["crf"]), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(job.output / "final_video.mp4"),
         "-map", "[a2]", "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(job.output / "final_audio.wav")],
        cwd=work, total_seconds=total, on_progress=lambda f: job.stage_progress(0.45 + 0.5 * f), log=job.log,
    )

    # ---------------------------------------------------------------- thumbnail
    try:
        frame = work / "thumb_frame.jpg"
        if has_video:
            first = next(c for c in timeline if c["kind"] == "speech")
            ffmpeg.run(["-ss", f"{first['src_start'] + 1.0:.2f}", "-i", src, "-frames:v", "1", "-q:v", "2", frame],
                       log=job.log)
        else:
            shutil.copy2(work / "intro.png", frame)
        cards.thumbnail(frame, job.output / "thumbnail.jpg", selection["youtube"]["title"], cfg["cards"])
    except Exception as exc:  # thumbnail is a nice-to-have
        job.log(f"Thumbnail skipped: {exc}")

    job.state["summary"]["final_seconds"] = round(total, 2)
    job.message("Final video rendered")
