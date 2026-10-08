"""Fonts and background music are fetched / generated on first use, so the repo stays text-only.

* Fonts: Inter (SIL OFL) is downloaded once from the official GitHub release. If that fails
  (offline, firewall) we fall back to a good system font, so rendering never breaks.
* Music: a royalty-free ambient loop is synthesised with numpy (no samples, no licence issues).
"""
from __future__ import annotations

import io
import platform
import subprocess
import urllib.request
import wave
import zipfile
from pathlib import Path

from .config import FONTS_DIR, MUSIC_DIR

INTER_ZIP = "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
WEIGHTS = ("Bold", "SemiBold", "Regular")

SYSTEM_FONTS = {
    "Windows": ("Segoe UI", {"Bold": r"C:\Windows\Fonts\segoeuib.ttf", "SemiBold": r"C:\Windows\Fonts\seguisb.ttf",
                             "Regular": r"C:\Windows\Fonts\segoeui.ttf"}),
    "Darwin": ("Helvetica Neue", {"Bold": "/System/Library/Fonts/HelveticaNeue.ttc",
                                  "SemiBold": "/System/Library/Fonts/HelveticaNeue.ttc",
                                  "Regular": "/System/Library/Fonts/HelveticaNeue.ttc"}),
    "Linux": ("DejaVu Sans", {"Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                              "SemiBold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                              "Regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}),
}

_cache: dict | None = None


def _inter_paths() -> dict[str, Path]:
    return {w: FONTS_DIR / f"Inter-{w}.otf" for w in WEIGHTS}


def _download_inter(log=print) -> bool:
    try:
        log("Downloading the Inter font (one time, ~30 MB)...")
        with urllib.request.urlopen(INTER_ZIP, timeout=60) as resp:
            data = resp.read()
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for w in WEIGHTS:
                (FONTS_DIR / f"Inter-{w}.otf").write_bytes(zf.read(f"extras/otf/Inter-{w}.otf"))
        return True
    except Exception as exc:
        log(f"Inter download failed ({exc}); using a system font instead")
        return False


def fonts(log=print) -> dict:
    """Return {"family": str, "Bold": path, "SemiBold": path, "Regular": path}."""
    global _cache
    if _cache:
        return _cache
    inter = _inter_paths()
    if all(p.exists() for p in inter.values()) or _download_inter(log):
        _cache = {"family": "Inter", **{w: str(p) for w, p in inter.items()}}
        return _cache
    family, paths = SYSTEM_FONTS.get(platform.system(), SYSTEM_FONTS["Linux"])
    _cache = {"family": family, **{w: p if Path(p).exists() else "" for w, p in paths.items()}}
    return _cache


# --------------------------------------------------------------------------- music
def _synth_loop(path: Path, seconds_per_bar: float = 60 / 72 * 4, bars: int = 16, sr: int = 44100) -> None:
    import numpy as np

    def hz(m: int) -> float:
        return 440 * 2 ** ((m - 69) / 12)

    prog = [[48, 55, 59, 64, 67], [45, 52, 55, 60, 64], [41, 48, 52, 57, 64], [43, 50, 55, 59, 64]]
    beat = seconds_per_bar / 4
    n = int(bars * seconds_per_bar * sr)
    out = np.zeros((n, 2))
    t_bar = np.arange(int(seconds_per_bar * sr)) / sr
    rng = np.random.default_rng(7)
    for b in range(bars):
        chord, i0 = prog[b % 4], int(b * seconds_per_bar * sr)
        env = np.clip(np.minimum(1, t_bar / 1.2) * np.minimum(1, (seconds_per_bar - t_bar) / 1.0 + 0.25), 0, 1)
        pad = sum(sum(np.sin(2 * np.pi * hz(m) * (1 + d) * t_bar + ph) for d, ph in
                      ((-0.003, 0), (0, 1.3), (0.003, 2.1))) + 0.25 * np.sin(2 * np.pi * 2 * hz(m) * t_bar)
                  for m in chord) * env * 0.05
        pan = np.array([0.9, 1.0]) if b % 2 else np.array([1.0, 0.9])
        end = min(n, i0 + len(t_bar))
        out[i0:end] += pad[: end - i0, None] * pan
        for k in range(8):  # soft plucked arpeggio
            m = chord[[1, 2, 3, 4, 3, 2, 3, 4][k]] + 12
            st = i0 + int(k * beat / 2 * sr)
            tt = np.arange(min(int(1.2 * sr), n - st)) / sr
            pl = np.sin(2 * np.pi * hz(m) * tt) * np.exp(-tt * 3.5) * 0.06 * (0.7 + 0.3 * rng.random())
            p = 0.3 + 0.4 * (k % 2)
            out[st:st + len(tt), 0] += pl * (1 - p)
            out[st:st + len(tt), 1] += pl * p
    d = int(0.33 * sr)
    for g, sh in ((0.35, d), (0.2, 2 * d), (0.1, 3 * d)):  # ping-pong echo
        out[sh:, 0] += g * out[:-sh, 1]
        out[sh:, 1] += g * out[:-sh, 0]
    xf = int(2 * sr)  # crossfade tail into head -> seamless loop
    tail = out[-xf:].copy()
    out = out[:-xf]
    w = np.linspace(0, 1, xf)[:, None]
    out[:xf] = out[:xf] * w + tail * (1 - w)
    out /= np.abs(out).max() * 1.12
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes((out * 32767).astype("<i2").tobytes())


def ensure_music(log=print) -> Path | None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    target = MUSIC_DIR / "soft_ambient_loop.mp3"
    if target.exists():
        return target
    try:
        log("Generating the royalty-free background music loop (one time)")
        wav = MUSIC_DIR / "_loop.wav"
        _synth_loop(wav)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af", "lowpass=f=9000",
                        "-c:a", "libmp3lame", "-b:a", "160k", str(target)], check=True)
        wav.unlink(missing_ok=True)
        return target
    except Exception as exc:
        log(f"Could not generate music ({exc}); continuing without it")
        return None
