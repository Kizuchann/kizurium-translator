""": block tracking gate + composite score (greedy one-to-one)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.state import (  # noqa: E402
    MATCH_CENTER_HEIGHT,
    MATCH_IOU,
    MATCH_W_CENTER,
    MATCH_W_IOU,
    MATCH_W_LANG,
    MATCH_W_SIZE,
    MATCH_W_TEXT,
    TrackedBlock,
    block_match_score,
    same_visual_block,
)
from kizurium_translator.live.tracking import track_blocks  # noqa: E402


def test_phase39_gate_constants():
    assert MATCH_IOU == 0.15
    assert MATCH_CENTER_HEIGHT == 2.5
    assert abs(MATCH_W_IOU + MATCH_W_CENTER + MATCH_W_SIZE + MATCH_W_LANG + MATCH_W_TEXT - 1.0) < 1e-9


def test_candidate_gate_iou_or_center():
    a = (0, 0, 40, 20)
    # High IoU neighbour
    assert same_visual_block(a, (2, 1, 42, 21))
    # Far away — fail
    assert not same_visual_block(a, (400, 400, 440, 420))
    # Low IoU but centre within 2.5× avg height
    assert same_visual_block(a, (10, 15, 50, 35))


def test_composite_prefers_same_text_and_lang():
    box = (0, 0, 40, 20)
    base = block_match_score(box, box, prev_lang="en", lang="en", prev_text="Hello", text="Hello")
    worse = block_match_score(box, box, prev_lang="en", lang="ja", prev_text="Hello", text="違う")
    assert base > worse


def test_track_blocks_greedy_one_to_one():
    prev = [
        TrackedBlock(
        id=1,
        box=(0, 0, 40, 16),
            text="One",
            norm="one",
        script="en",
        lang="en",
            conf=0.9,
        engine="rapid",
    ),
        TrackedBlock(
            id=2,
            box=(0, 30, 40, 46),
            text="Two",
            norm="two",
        script="en",
        lang="en",
            conf=0.9,
        engine="rapid",
    ),
]
    lines = [
        {"text": "One", "box": (1, 1, 41, 17), "lang": "en", "conf": 0.9, "engine": "rapid"},
        {"text": "Two", "box": (1, 31, 41, 47), "lang": "en", "conf": 0.9, "engine": "rapid"},
]
    current, pairs, next_id = track_blocks(prev, lines, now=1.0, next_id=3)
    assert len(pairs) == 2
    ids = {b.id for b in current}
    assert ids == {1, 2}
    assert next_id == 3
