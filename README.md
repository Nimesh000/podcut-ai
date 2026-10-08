# PodCut AI 🎬

**Paste a podcast or interview link, get back a short highlight video.**

PodCut AI turns a long podcast or interview (YouTube link, Google Drive link, direct URL or a local file) into a
**2–3 minute YouTube-ready video**. It handles the editing steps automatically:

- transcribes the speech with **faster-whisper** (word-level timestamps)
- has an **LLM** (Groq Llama 3.3 70B or xAI Grok) score every moment and pick the strongest ones
- cleans the audio (high-pass, FFT denoise, levelling, normalisation to -14 LUFS)
- opens with a **cold-open hook**, then adds a branded **intro card**, a **guest name tag**, **burned-in subtitles**,
  **background music that ducks under speech** and an **outro**
- delivers the video, a separate final audio track, subtitles, a thumbnail, AI-written YouTube metadata and a
  report explaining every editing decision

It runs as a local **web app** (FastAPI): submit a link, watch each stage progress live, then preview and download.

<!-- Add a demo GIF / screenshot here: drag it into the GitHub editor, e.g. docs/demo.gif -->

---

## How it works

```mermaid
flowchart LR
    A[Link / upload] --> B[1. Fetch<br/>yt-dlp / gdown]
    B --> C[2. Clean audio<br/>FFmpeg highpass + afftdn + dynaudnorm]
    C --> D[3. Transcribe<br/>faster-whisper, word timestamps]
    D --> E[4. AI selection<br/>LLM scores every moment 1-10]
    E --> F[5. Render<br/>cuts + cards + subtitles + ducked music]
    F --> G[6. Package<br/>video, audio, SRT, thumbnail, report]
```

Each stage starts as soon as the previous one finishes. Progress is written to `job.json` after every step, so a
failed job can be **resumed from the step that failed** (Retry button, or `python -m podcut resume <job_id>`).

### How the AI picks moments

1. The transcript is grouped into **candidate moments** of 8–40 seconds, broken at sentence ends and natural pauses.
2. The LLM gets the moments in rate-limit-friendly chunks. It returns strict JSON for each one: a `score` (1–10), a `hook`
   flag, a `topic` and a one-line `reason`. It's told to give low scores to ads, greetings, small talk and fragments that
   need missing context.
3. The output is **validated**: unknown ids are dropped, scores are clamped, and malformed JSON is retried. Any moment
   the LLM skipped gets a rule-based fallback score, so the pipeline still runs with no API key at all.
4. A **deterministic** selector builds the edit. It takes the best short "hook" moment as a cold open, then fills the
   target length with the highest-scored moments, puts them back in chronological order and merges back-to-back moments
   into single cuts. The LLM decides *what* is good, and the code guarantees the length and order rules.

### How the render stays in sync

- Every cut is rendered with frame-accurate seeking as its own clip. Each clip is rounded to a whole number of frames,
  with 40–80 ms audio fades so cuts don't click.
- Clips are joined with uncompressed PCM audio inside MKV. This avoids the small AAC padding gaps that add up and drift
  over many cuts.
- A single final encode adds the lower third, the burned ASS subtitles (word timestamps re-timed onto the new timeline),
  the music bed (louder under the cards, side-chain ducked under speech) and loudness normalisation.

---

## Quick start (Windows)

1. Install **Python 3.10+** and **FFmpeg** (`winget install Gyan.FFmpeg`). For YouTube links also install Deno
   (`winget install DenoLand.Deno`). Open a new terminal afterwards.
2. Copy `.env.example` to `.env` and paste a free **Groq** key (`GROQ_API_KEY=gsk_...`, from https://console.groq.com/keys).
3. Double-click **`run.bat`**. It creates a virtual environment, installs the requirements and opens
   http://127.0.0.1:8000.

macOS / Linux: `bash run.sh`

The first run downloads the whisper model (~480 MB for `small`). A 15-minute episode takes roughly 5–10 minutes on a
laptop CPU, and much less on an NVIDIA GPU (detected automatically).

### Command line

```bash
python -m podcut run "https://www.youtube.com/watch?v=..." --guest "Jane Doe" --role "Founder, Acme" --seconds 150
python -m podcut run my_episode.mp4 --title "Building in public" --no-music
python -m podcut resume 20261008-104059-abe25d --from select     # re-do the AI pick and render only
python -m podcut serve --port 8000
```

### Free GPU (Kaggle)

`notebooks/podcut_kaggle.ipynb` runs the same pipeline on a free Kaggle T4. Turn on GPU and Internet, add `GROQ_API_KEY`
under Secrets, attach a video dataset or paste a link, then Run All.

---

## Output

Every job gets its own folder with a fixed layout:

```
jobs/<job_id>/
├── job.json                 live status + settings (what the web app polls)
├── pipeline.log             full log including every FFmpeg command
├── input/                   downloaded / uploaded source
├── work/                    intermediates: voice_clean.wav, transcript.json, blocks.json,
│                            selection.json, timeline.json, clips/, subs.ass, cards ...
└── output/
    ├── final_video.mp4      1080p H.264 / AAC, -14 LUFS, faststart
    ├── final_audio.wav      final mix (voice + music)
    ├── subtitles.srt        captions for the final edit
    ├── thumbnail.jpg        1280x720 suggested thumbnail
    ├── youtube_metadata.txt AI title, description, tags
    ├── edit_report.md       every cut: source time, score, topic, reason, dialogue
    ├── edit_report.json     same, machine-readable
    └── transcript_full.txt  full timestamped transcript of the source
```

## Configuration

All defaults are in [`config.yaml`](config.yaml), and the web form overrides the common ones per job. These are the
main settings:

| Setting | Default | Meaning |
|---|---|---|
| `output.target_seconds` | 150 | length of hook + body (intro/outro not included) |
| `transcribe.model` | small | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `transcribe.device` | auto | uses CUDA if it works, otherwise CPU int8 |
| `llm.provider` | auto | detected from the key prefix: `gsk_` = Groq, `xai-` = xAI |
| `audio.music_volume_db` | -26 | music bed level under speech |
| `cards.accent_color` | #F5B700 | colour used on cards, name tag and thumbnail |

**Assets are created on first run** (see `podcut/assets.py`). The Inter font is downloaded once from its official release,
with a system-font fallback if that fails. The background music loop is synthesised with numpy, so it is royalty-free.
To use a different track, drop it into `assets/music/`.

## Project structure

```
podcut/
├── pipeline.py        stage runner (auto-advance, resume, error capture)
├── job.py             job folder layout + status/progress bookkeeping
├── editing.py         pure logic: moments, heuristic scores, cut selection
├── llm.py             OpenAI-compatible client for Groq / xAI / OpenAI, JSON parsing
├── subtitles.py       word re-timing, cue building, SRT + styled ASS
├── cards.py           intro/outro cards, lower third, thumbnail (Pillow)
├── assets.py          first-run font download + procedural music generation
├── ffmpeg.py          ffmpeg/ffprobe wrappers with progress + readable errors
└── stages/            fetch, audio, transcribe, select, render, package
app/
├── server.py          FastAPI: job queue, REST API, file downloads
└── static/            single-page UI (HTML/CSS/JS, no build step)
tests/                 pytest unit tests for the editing logic
notebooks/             Kaggle GPU notebook
```

Run the tests with `pip install -r requirements-dev.txt && pytest -q`.

## Troubleshooting

| Problem | Fix |
|---|---|
| "ffmpeg was not found" | `winget install Gyan.FFmpeg`, then open a **new** terminal |
| YouTube download fails | `pip install -U "yt-dlp[default]"` and install Deno (`winget install DenoLand.Deno`), which yt-dlp now needs for YouTube. Or download the file and use **Upload** |
| Google Drive download fails | share the file as "Anyone with the link" |
| Header pill says "No LLM key" | add `GROQ_API_KEY` to `.env` and restart. Without a key it still runs, using rule-based selection |
| Groq `429 rate limit` | handled automatically with retries. Long episodes just take a little longer |
| CUDA / cuDNN errors | set `transcribe.device: cpu` in `config.yaml` |
| Hindi / Hinglish audio | pick the language in Edit settings. Subtitles keep the spoken script |

## Tech stack

Python · faster-whisper (CTranslate2) · Groq Llama 3.3 70B / xAI Grok · FFmpeg (libass, afftdn, sidechaincompress,
loudnorm) · FastAPI · Pillow · yt-dlp · vanilla JS

Built with Claude as an agentic coding assistant.

## License

MIT. Only process content you have the rights to edit and publish.
