#!/usr/bin/env bash
# PodCut AI - launcher for macOS / Linux
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "Install FFmpeg first (brew install ffmpeg / sudo apt install ffmpeg)"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
(sleep 2; command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000 || open http://127.0.0.1:8000 || true) &
python -m podcut serve
