"""choices and system labels stay off the dialogue body.

Family A plus a choice stack and a short system control. Multi-line body
stays one block; the choices do not join it or each other.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.prepare import arrange_lines  # noqa: E402


def _line(text, box, lh=22):
    return {
        "text": text,
        "box": box,
        "line_height": lh,
        "conf": 90,
        "engine": "rapidocr",
    }


def test_choices_and_system_label_stay_separate_from_body():
    img = Image.new("RGB", (1280, 720), (16, 16, 20))
    lines = [
        _line("Skip", (1100, 24, 1180, 52), 20),
        _line("Shake your head", (180, 300, 520, 336)),
        _line("Tell the truth", (180, 348, 500, 384)),
        _line("Sayori", (80, 480, 190, 508)),
        _line(
            "You look really happy today, so I wanted to ask you something before the bell.",
            (70, 528, 980, 640),
        ),
    ]
    out = arrange_lines(lines, img, 1280, 720, [], False, False, True, "eng-ui")
    texts = [str(p.get("text") or "").strip() for p in out]
    for expected in ("Skip", "Shake your head", "Tell the truth", "Sayori"):
        assert expected in texts, texts
    body = next(t for t in texts if "happy today" in t)
    assert "Shake" not in body
    assert "Skip" not in body
    assert "Tell the truth" not in body

"""one screen holds several text zones, each at its own size.

A paragraph in a panel is not a button. A HUD label, a name, a narration
block and a system line stay separate. Found on the dense panel (ws6) where
a sentence about items was drawn as one huge label, and on the long prose
screen (ws7) where the title, the passage and the status line already stayed
apart.
"""


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.layout.grouping import looks_like_panel_body_line  # noqa: E402
from kizurium_translator.ocr.engine import looks_like_dialogue_choice  # noqa: E402


def test_panel_sentence_is_not_a_choice_button():
    sentence = "Some items, like Helmets, can not be equipped more than once."
    assert not looks_like_dialogue_choice(sentence)
    assert looks_like_panel_body_line(sentence)
    assert looks_like_dialogue_choice("Shake your head")
    assert looks_like_dialogue_choice("I see... Pleased to meet you, Amiya")


def test_zones_on_one_screen_stay_separate():
    img = Image.new("RGB", (1280, 720), (18, 18, 22))
    lines = [
        {
            "text": "Skip",
            "box": (1100, 20, 1180, 48),
            "line_height": 18,
            "conf": 90,
            "engine": "rapidocr",
        },
        {
            "text": "Victorian Empire",
            "box": (40, 520, 280, 570),
            "line_height": 28,
            "conf": 90,
            "engine": "rapidocr",
        },
        {
            "text": (
                "A modern nation ruled by Draco and Aslan occupies the valleys "
                "and keeps its own order."
            ),
            "box": (320, 500, 1200, 640),
            "line_height": 20,
            "conf": 90,
            "engine": "rapidocr",
        },
        {
            "text": "Downloading update",
            "box": (500, 660, 780, 696),
            "line_height": 18,
            "conf": 90,
            "engine": "rapidocr",
        },
    ]
    out = arrange_lines(lines, img, 1280, 720, [], False, False, True, "eng-ui")
    texts = [str(p.get("text") or "") for p in out]
    assert any(t.strip() == "Skip" for t in texts)
    assert any(t.strip() == "Victorian Empire" for t in texts)
    assert any("Draco" in t and "Victorian Empire" not in t for t in texts)
    assert any("Downloading" in t and "Draco" not in t for t in texts)

"""dialogue, HUD, a quest line and a toast stay apart.

No title branch. The same frame shape as a quest tracker beside a fight:
a status chip, a quest line, a loot toast and a spoken line.
"""


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))



def _line(text, box):
    return {
        "text": text,
        "box": box,
        "line_height": 20,
        "conf": 90,
        "engine": "rapidocr",
    }


def test_quest_hud_toast_and_dialogue_stay_four_blocks():
    img = Image.new("RGB", (1280, 720), (20, 22, 26))
    lines = [
        _line("In Combat", (40, 40, 160, 68)),
        _line("Search for the gate.", (900, 80, 1180, 112)),
        _line("Obtained Bronze Rapier.", (900, 140, 1200, 172)),
        _line("Iori: That legendary weapon is ours!", (200, 620, 1000, 670)),
    ]
    out = arrange_lines(lines, img, 1280, 720, [], False, False, True, "eng-ui")
    texts = [str(p.get("text") or "") for p in out]
    assert any(t.strip() == "In Combat" for t in texts)
    assert any("gate" in t and "Rapier" not in t and "legendary" not in t for t in texts)
    assert any("Rapier" in t and "gate" not in t for t in texts)
    assert any("legendary" in t and "Rapier" not in t for t in texts)

"""two text systems on one screen stay independent.

A message widget in the upper area and a dialogue strip at the bottom are
not one paragraph just because both contain sentences.
"""


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))



def _line(text, box, lh=22):
    return {
        "text": text,
        "box": box,
        "line_height": lh,
        "conf": 90,
        "engine": "rapidocr",
    }


def test_upper_widget_does_not_join_bottom_dialogue():
    img = Image.new("RGB", (1280, 720), (16, 16, 20))
    lines = [
        _line("New message", (40, 48, 220, 78), 20),
        _line("Meet me at the station after class today.", (40, 86, 520, 140)),
        _line("Okabe", (80, 520, 180, 548)),
        _line(
            "The world line shifted again while we were looking at the phone.",
            (70, 560, 1000, 660),
        ),
    ]
    out = arrange_lines(lines, img, 1280, 720, [], False, False, True, "eng-ui")
    texts = [str(p.get("text") or "") for p in out]
    phone = next(t for t in texts if "station" in t)
    body = next(t for t in texts if "world line" in t)
    assert "world line" not in phone
    assert "station" not in body
    assert any(t.strip() == "Okabe" for t in texts)
    assert any(t.strip() == "New message" for t in texts)
