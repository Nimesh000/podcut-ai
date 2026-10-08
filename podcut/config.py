"""Configuration loading: config.yaml + per-job overrides + .env secrets."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS_DIR = ASSETS / "fonts"
MUSIC_DIR = ASSETS / "music"
JOBS_DIR = Path(os.environ.get("PODCUT_JOBS_DIR", ROOT / "jobs"))


def _load_env() -> None:
    """Load .env from the project root (and a few friendly fallbacks)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    for candidate in (ROOT / ".env", Path.cwd() / ".env", ROOT / "clips" / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


_load_env()


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        elif value is not None and value != "":
            out[key] = value
    return out


def load_config(overrides: dict | None = None, path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return deep_merge(cfg, overrides)


def get_api_key() -> str | None:
    for name in ("LLM_API_KEY", "GROQ_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(name, "").strip().strip('"').strip("'")
        if value:
            return value
    return None
