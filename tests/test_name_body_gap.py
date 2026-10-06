"""wide left-name / right-body gap, including the edges.

Speaker-only and body-only frames must not invent the missing half.
A multi-line body across a wide gap must not merge back into the name.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.layout.name_gap import (  # noqa: E402
    is_name_dialogue_side_pair,
    should_refuse_merge_for_name_gap,
)
from kizurium_translator.live.prepare import arrange_lines  # noqa: E402


def _line(text, box, lh=20):
    return {
        "text": text,
        "box": box,
        "line_height": lh,
        "conf": 90,
        "engine": "rapidocr",
    }


def _arrange(lines, w=1280, h=720):
    img = Image.new("RGB", (w, h), (16, 16, 20))
    return arrange_lines(lines, img, w, h, [], False, False, True, "eng-ui")


def test_speaker_only_does_not_invent_a_body():
    out = _arrange([_line("Amiya", (40, 560, 140, 590))])
    assert len(out) == 1
    assert out[0]["text"].strip() == "Amiya"
    assert out[0].get("kind") != "dialogue"


def test_body_only_is_not_labeled_as_a_speaker():
    text = "We should keep moving toward the city gates before night falls."
    out = _arrange([_line(text, (80, 560, 900, 640))])
    assert len(out) == 1
    assert out[0].get("semantic_role") != "SPEAKER"
    assert out[0].get("kind") != "name"
    assert "city gates" in out[0]["text"]


def test_wide_gap_multiline_body_stays_off_the_name():
    name = _line("Distant Voice", (40, 540, 210, 572))
    body = _line(
        "For making you suffer again in this place.\nNo one else is coming.",
        (280, 536, 1100, 640),
    )
    assert is_name_dialogue_side_pair(name, body)
    assert should_refuse_merge_for_name_gap(name, body)
    out = _arrange([name, body])
    texts = [str(p.get("text") or "") for p in out]
    assert any(t.strip() == "Distant Voice" for t in texts)
    assert any("suffer again" in t and "Distant Voice" not in t for t in texts)

"""name plate above a body stays a separate speaker.

Family A. The plate can sit a few pixels above a multi-line or text-heavy
body. That closeness is not a reason to glue the name into the sentence.
"""


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.layout.name_gap import (  # noqa: E402
    is_name_above_body,
)


def _arrange(lines, w=1280, h=720):
    img = Image.new("RGB", (w, h), (16, 16, 20))
    return arrange_lines(
        lines, img, w, h, [], False, False, True, "eng-ui"
    )


def _line(text, box, lh=22):
    return {
        "text": text,
        "box": box,
        "line_height": lh,
        "conf": 90,
        "engine": "rapidocr",
    }


def test_close_name_plate_is_not_part_of_the_body():
    name = _line("Sayori", (80, 480, 190, 508))
    body = _line(
        "You look really happy today, so I wanted to ask you something.",
        (70, 528, 980, 610),
    )
    assert is_name_above_body(name, body)
    assert should_refuse_merge_for_name_gap(name, body)
    out = _arrange([name, body])
    texts = [str(p.get("text") or "") for p in out]
    assert any(t.strip() == "Sayori" for t in texts)
    assert any("happy today" in t for t in texts)
    assert not any("Sayori" in t and "happy" in t for t in texts)
    speakers = [p for p in out if p.get("semantic_role") == "SPEAKER" or p.get("kind") == "name"]
    assert speakers and speakers[0]["text"].strip() == "Sayori"


def test_tall_text_block_keeps_its_name_separate():
    name = _line("Monika", (90, 140, 210, 168))
    body = _line(
        "The classroom is quiet and the clock is loud. " * 4,
        (80, 188, 1040, 620),
    )
    assert is_name_above_body(name, body)
    out = _arrange([name, body])
    texts = [str(p.get("text") or "") for p in out]
    assert any(t.strip() == "Monika" for t in texts)
    assert any("classroom" in t for t in texts)
    assert not any("Monika" in t and "classroom" in t for t in texts)
    body_blocks = [p for p in out if "classroom" in str(p.get("text") or "")]
    assert body_blocks
    assert body_blocks[0].get("oneline") is not True


def test_far_heading_is_not_the_speaker():
    title = _line("Chapter", (80, 30, 220, 64))
    body = _line(
        "You look really happy today, so I wanted to ask you something.",
        (70, 520, 980, 600),
    )
    assert not is_name_above_body(title, body)

"""name/dialogue gap — separate boxes and refuse false merges."""


import sys
from pathlib import Path

from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.layout.grouping import split_vn_speaker_line  # noqa: E402
from kizurium_translator.layout.name_gap import (  # noqa: E402
    NAME_DIALOGUE_GAP_MIN_PX,
    annotate_side_by_side_name_body,
    horizontal_gap_px,
    name_dialogue_gap_threshold,
)
from kizurium_translator.typography.metrics import vn_lines_should_merge  # noqa: E402


def test_phase53_threshold_matches_plan():
    assert NAME_DIALOGUE_GAP_MIN_PX == 16
    assert name_dialogue_gap_threshold(10) == 16  # 1.5*10=15 → floor 16
    assert name_dialogue_gap_threshold(20) == 30  # 1.5*20


def test_arknights_style_separate_boxes_are_paired():
    name = {
        "text": "Amiya",
        "box": (40, 200, 110, 228),
        "line_height": 18,
        "kind": "ui",
    }
    body = {
        "text": "We should keep moving toward the city gates now.",
        "box": (160, 198, 620, 250),
        "line_height": 20,
        "kind": "ui",
    }
    assert horizontal_gap_px(name["box"], body["box"]) == 50
    assert is_name_dialogue_side_pair(name, body)
    assert should_refuse_merge_for_name_gap(name, body)
    assert not vn_lines_should_merge(name, body)

    marked = annotate_side_by_side_name_body([name, body])
    assert marked[0]["kind"] == "name"
    assert marked[0]["semantic_role"] == "SPEAKER"
    assert marked[1]["kind"] == "dialogue"


def test_multiword_name_still_counts():
    name = {
        "text": "Distant Voice",
        "box": (40, 100, 180, 124),
        "line_height": 16,
    }
    body = {
        "text": "For making you suffer again in this place.",
        "box": (260, 98, 700, 130),
        "line_height": 18,
    }
    assert is_name_dialogue_side_pair(name, body)


def test_narrow_gap_does_not_split_sentence():
    left = {"text": "Hello", "box": (10, 10, 60, 28), "line_height": 16}
    right = {
        "text": "there friend how are you doing today",
        "box": (68, 10, 400, 28),
        "line_height": 16,
    }
    # 8px gap < floor 16
    assert horizontal_gap_px(left["box"], right["box"]) == 8
    assert not is_name_dialogue_side_pair(left, right)


def test_ink_split_still_works_with_phase53_floor():
    """Existing same-baseline split must keep working (regression)."""
    img = Image.new("RGB", (1920, 1080), (14, 16, 22))
    d = ImageDraw.Draw(img)

    def draw_text(x: int, y: int, text: str, gap_before: int = 0) -> int:
        cx = x + gap_before
        for _ch in text:
            d.rectangle((cx, y, cx + 1, y + 7), fill=(235, 235, 240))
            cx += 11
        return cx - 2

    right = draw_text(80, 100, "Distant Voice")
    right = draw_text(right + 90, 100, "For making you suffer again")
    out = split_vn_speaker_line(
        {
            "text": "Distant Voice For making you suffer again",
            "box": (80, 100, right, 120),
            "kind": "dialogue",
            "line_height": 20,
        },
        img,
    )
    assert [p["text"] for p in out] == [
        "Distant Voice",
        "For making you suffer again",
    ]


def test_arrange_lines_wires_roles_and_name_gap():
    """The live path must call these — not leave them as dead imports."""
    img = Image.new("RGB", (800, 400), (20, 20, 24))
    lines = [
        {
            "text": "Kal'tsit",
            "box": (30, 250, 120, 276),
            "line_height": 18,
            "conf": 90,
            "engine": "rapidocr",
        },
        {
            "text": "The operation will begin at dawn tomorrow near the ridge.",
            "box": (180, 248, 720, 300),
            "line_height": 20,
            "conf": 90,
            "engine": "rapidocr",
        },
    ]
    out = arrange_lines(
        lines,
        img,
        800,
        400,
        [],
        False,
        False,
        True,  # eng_ui_mode — the path that owns speaker split
        "eng-ui",
    )
    kinds = {p.get("kind") for p in out}
    roles = {p.get("semantic_role") for p in out if p.get("semantic_role")}
    assert "name" in kinds or "SPEAKER" in roles
    assert any(
        "operation" in str(p.get("text") or "").lower()
        or p.get("kind") == "dialogue"
        or p.get("semantic_role") == "DIALOGUE"
        for p in out
    )
    # Must not collapse into one glued card.
    assert len(out) >= 2
