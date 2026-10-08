"""Unit tests for the pure editing logic (no ffmpeg, no network)."""
import random

import pytest

from podcut.config import load_config
from podcut.editing import choose_cuts, heuristic_score, make_blocks
from podcut.llm import detect_provider, parse_json
from podcut.stages.render import build_timeline
from podcut.subtitles import build_cues, fmt_ass_time, fmt_srt_time, remap_words


def fake_segments(total=900.0, seed=0):
    rng = random.Random(seed)
    segs, t = [], 1.0
    while t < total:
        n = rng.randint(5, 16)
        words = []
        for i in range(n):
            d = rng.uniform(0.2, 0.45)
            w = "word" + ("." if i == n - 1 else "")
            words.append({"s": round(t, 3), "e": round(t + d, 3), "w": " " + w})
            t += d + 0.05
        segs.append({"id": len(segs), "start": words[0]["s"], "end": words[-1]["e"],
                     "text": " ".join(x["w"].strip() for x in words), "words": words})
        t += rng.uniform(0.2, 1.5)
    return segs


def test_blocks_respect_bounds_and_cover_all_segments():
    segs = fake_segments()
    blocks = make_blocks(segs, 8, 40)
    assert sum(len(b["seg_ids"]) for b in blocks) == len(segs)
    assert all(b["duration"] <= 40.5 for b in blocks)
    assert [b["id"] for b in blocks] == list(range(len(blocks)))
    # chronological and non-overlapping
    assert all(a["end"] <= b["start"] for a, b in zip(blocks, blocks[1:]))


@pytest.mark.parametrize("target", [60, 150, 180])
def test_choose_cuts_hits_target_window(target):
    cfg = load_config({"output": {"target_seconds": target, "min_seconds": target * 0.75,
                                  "max_seconds": target * 1.25}})
    segs = fake_segments()
    blocks = make_blocks(segs)
    rng = random.Random(1)
    scores = {b["id"]: {"id": b["id"], "score": rng.randint(1, 10), "hook": rng.random() < 0.2} for b in blocks}
    plan = choose_cuts(blocks, scores, segs, cfg)
    assert target * 0.75 <= plan["total_seconds"] <= target * 1.25
    starts = [r["start"] for r in plan["body"]]
    assert starts == sorted(starts)
    if plan["hook"]:
        assert plan["hook_seconds"] <= cfg["editing"]["hook_max_seconds"] + 0.01
        hook_block = plan["hook"]["block_id"]
        assert all(hook_block not in r["block_ids"] for r in plan["body"])


def test_ads_are_penalised():
    ad = {"id": 3, "duration": 20, "text": "This episode is brought to you by Acme, use code PODCAST for 20% off"}
    normal = {"id": 4, "duration": 20, "text": " ".join(["talking about building products"] * 10)}
    assert heuristic_score(ad, 3)["score"] < heuristic_score(normal, 4)["score"]


def test_timeline_and_subtitle_remap():
    cfg = load_config()
    segs = fake_segments(200)
    plan = {"hook": {"start": segs[2]["start"], "end": segs[2]["end"], "block_id": 0},
            "body": [{"start": segs[5]["start"], "end": segs[8]["end"], "block_ids": [1]}]}
    tl = build_timeline(plan, cfg)
    assert [c["role"] for c in tl] == ["hook", "intro", "body", "outro"]
    for a, b in zip(tl, tl[1:]):
        assert b["out_start"] == pytest.approx(a["out_start"] + a["duration"], abs=1e-3)
    # durations are whole frames
    fps = cfg["output"]["fps"]
    assert all(abs(c["duration"] * fps - round(c["duration"] * fps)) < 1e-6 for c in tl)

    groups = remap_words({"segments": segs}, tl)
    cues = build_cues(groups, 7, 2.8)
    total = sum(c["duration"] for c in tl)
    assert cues and all(0 <= c["start"] < c["end"] <= total for c in cues)
    # no cue during the intro card
    intro = tl[1]
    assert not any(intro["out_start"] + 0.1 < c["start"] < intro["out_start"] + intro["duration"] - 0.5 for c in cues)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(cues, cues[1:]))


def test_time_formats():
    assert fmt_srt_time(3723.456) == "01:02:03,456"
    assert fmt_ass_time(3723.456) == "1:02:03.46"


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"blocks": []}\n```') == {"blocks": []}
    assert parse_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_provider_detection():
    assert detect_provider("gsk_abc") == "groq"
    assert detect_provider("xai-abc") == "xai"
    assert detect_provider("sk-abc") == "openai"
