""": multi-signal speaker detection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr.speakers import (  # noqa: E402
    detect_speakers,
    score_speaker_candidate,
)


def test_short_word_alone_is_not_enough():
    lines = [{"text": "Hi", "box": (10, 10, 40, 28)}]
    score = score_speaker_candidate(lines[0], index=0, peers=lines)
    assert score.score < 2.2
    assert "short_alone_damped" in score.reasons


def test_name_above_dialogue_scores_high():
    lines = [
        {"text": "Amiya", "box": (40, 40, 120, 62), "kind": "name"},
    {
        "text": "We should keep moving toward the city gates now.",
            "box": (40, 70, 520, 120),
        "kind": "dialogue",
    },
]
    score = score_speaker_candidate(lines[0], index=0, peers=lines)
    assert score.score >= 2.2
    assert "above_dialogue" in score.reasons
    hits = detect_speakers(lines)
    assert hits and hits[0].line_index == 0


def test_known_name_helps_but_needs_context():
    lines = [
        {"text": "Kal'tsit", "box": (20, 20, 110, 44)},
    {
            "text": "The operation will begin at dawn tomorrow.",
            "box": (20, 52, 480, 100),
    },
]
    cold = score_speaker_candidate(lines[0], index=0, peers=lines)
    warm = score_speaker_candidate(
        lines[0], index=0, peers=lines, known_names={"Kal'tsit"}
)
    assert warm.score > cold.score
