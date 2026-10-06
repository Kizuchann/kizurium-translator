"""A row of buttons is not one label.

Three navigation words in the corner of a game screen came back from the
recogniser as a single line, and the translation was drawn across all of them
at once. What stayed on screen was the tail of the first button, with the
Russian for the whole row laid over the other two.

The tell is the gaps, not the words. Between the letters of a word the empty
columns are four to seven pixels; between the buttons they are forty. A phrase
has one kind of gap, a row of controls has two, and the wide one is where the
translation of one item has to stop.

The rule is measured on the image, so it does not care that the text is light on
a dark panel here and dark on a light one elsewhere. It must also stay quiet on
prose: a sentence has wide gaps only between words, and cutting a sentence in
half would be worse than the defect it fixes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from kizurium_translator import live  # noqa: E402


def nav_row(texts: list[str], ink: int = 235, bg: int = 22) -> Image.Image:
    """A row of labels, spaced the way an interface spaces its controls."""
    img = Image.new("RGB", (900, 60), (bg, bg, bg))
    draw = ImageDraw.Draw(img)
    x = 20
    for t in texts:
        draw.text((x, 20), t, fill=ink)
        x += 9 * len(t) + 46
    return img


def block(text: str, img: Image.Image) -> dict:
    return {
        "text": text,
        "box": (20, 10, img.size[0] - 20, 50),
        "line_height": 40,
        "kind": "ui",
    }


class TestButtonRows:
    def test_a_row_of_controls_is_split(self):
        img = nav_row(["AUTO", "OFF", "SKIP"])
        out = live.split_button_rows([block("AUTO OFF SKIP", img)], img)
        assert [p["text"] for p in out] == ["AUTO", "OFF", "SKIP"]

    def test_each_piece_gets_its_own_geometry(self):
        """Pieces have to sit where their word is, not share the row's box."""
        img = nav_row(["AUTO", "OFF", "SKIP"])
        out = live.split_button_rows([block("AUTO OFF SKIP", img)], img)
        boxes = [p["box"] for p in out]
        assert len(set(boxes)) == 3
        # left to right, touching, and inside the original box
        assert boxes[0][0] < boxes[1][0] < boxes[2][0]
        assert boxes[0][2] <= boxes[1][2] <= boxes[2][2]
        src = block("AUTO OFF SKIP", img)["box"]
        for b in boxes:
            assert b[0] >= src[0] and b[2] <= src[2]

    def test_a_sentence_is_left_alone(self):
        """The gaps in prose are all letter-sized, so nothing should cut."""
        img = nav_row(["However", "you", "will", "always", "be", "here"])
        src = block("However you will always be here", img)
        out = live.split_button_rows([src], img)
        assert len(out) == 1
        assert out[0]["text"] == src["text"]

    def test_two_words_stay_one_label(self):
        """Two words read as a phrase; only a row of three or more is a row."""
        img = nav_row(["Start", "Quest"])
        out = live.split_button_rows([block("Start Quest", img)], img)
        assert len(out) == 1

    def test_dark_ink_on_light_background(self):
        """The profile reads deviation from the background, not darkness."""
        img = nav_row(["AUTO", "OFF", "SKIP"], ink=20, bg=240)
        out = live.split_button_rows([block("AUTO OFF SKIP", img)], img)
        assert [p["text"] for p in out] == ["AUTO", "OFF", "SKIP"]
