"""Draw text in a known face so the measurement can be checked against it.

A corpus has to be committed, not generated at test time. Generating it inside
the test proves only that the code runs; committing it proves the numbers still
hold on someone else's machine, with a different Pillow and a different hinting
setting. That is the whole reason the PNGs are here.

`scripts/screencheck.py` already checks the overlay against real screenshots.
That is a harness for one session on one machine. This is the other direction: a
picture of a line in a face whose properties are known, checked in, asserting
that the measurement reads them back.

    python scripts/render_fixtures.py     # rewrite tests/fixtures/type/
    uv run pytest tests/test_type_fixtures.py

The faces, their sizes and their categories are chosen by hand in `FACES` below.
The properties the test asserts - weight, slant, monospace - are the ones the
overlay decides by them, so a wrong number here is a card drawn in the wrong
face rather than a failed assertion.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kizurium_translator.typography.metrics import _mask_from_array

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "src" / "kizurium_translator" / "fonts"
OUT_DIR = ROOT / "tests" / "fixtures" / "type"

# Every face is drawn with the same words, and the words are chosen for their
# parts rather than for their meaning: an ascender ("d", "l"), a descender ("g"),
# round letters ("o", "e", "0"), and digits. A sample of only capitals measures
# short, and one of only lowercase measures short too, and the face size that
# comes out of either is not the size that was drawn.
SAMPLE = "Handgloves 0123"

# Background and ink are far apart on purpose. A mask built from a threshold too
# close to the background picks up the anti-aliased halo and reports a stroke
# weight that belongs to the renderer rather than to the face.
BG_DARK = (12, 14, 18)
BG_LIGHT = (240, 238, 232)
INK_DARK = (18, 16, 14)
INK_LIGHT = (232, 236, 240)

# (face, size, weight, slant, width, mono)
#
# `weight`, `slant` and `width` are the properties the test asserts, and `any`
# means this face is not a claim: a display face has a stroke weight of its own
# that has nothing to do with whether the family has a bold cut, and asserting it
# as regular would be asserting something false.
#
# "regular" here is a book weight. `PressStart2P` is listed as "any" although it
# is technically a regular: it measures 0.286, heavier than every real bold in
# the package, because a pixel font draws every stroke on the pixel grid and the
# measurement counts the squares. Putting it in the regular group would have made
# the weight separation look worse than it is.
FACES: list[tuple[str, int, str, str, str, bool]] = [
    # Small sizes, and only for the two faces needed to show why the slant has a
    # minimum ink height. These are the fixtures the minimum is checked against:
    # at 18px PTSerif-Italic reads -0.280 with no minimum, and NotoSans at 14px
    # reads +0.051, which is over the italic threshold. Without them the minimum
    # could be deleted and nothing in the suite would notice.
    ("PTSerif-Italic.ttf", 18, "regular", "italic", "normal", False),
    ("PTSerif-Regular.ttf", 18, "regular", "upright", "normal", False),
    ("NotoSans.ttf", 14, "regular", "upright", "normal", False),
    ("Orbitron.ttf", 18, "any", "upright", "normal", False),
    # upright sans, book weight
    ("NotoSans.ttf", 48, "regular", "upright", "normal", False),
    ("NotoSans.ttf", 64, "regular", "upright", "normal", False),
    ("NotoSerif.ttf", 48, "regular", "upright", "normal", False),
    ("NotoSerif.ttf", 64, "regular", "upright", "normal", False),
    ("Onest.ttf", 48, "regular", "upright", "normal", False),
    ("GolosText.ttf", 48, "regular", "upright", "normal", False),
    ("Play-Regular.ttf", 48, "regular", "upright", "normal", False),
    # weight pairs: the same family, one bold
    ("Play-Bold.ttf", 48, "bold", "upright", "normal", False),
    ("PTSerif-Bold.ttf", 48, "bold", "upright", "normal", False),
    ("Rajdhani-Bold.ttf", 48, "bold", "upright", "normal", False),
    # the only italic in the package
    ("PTSerif-Italic.ttf", 48, "regular", "italic", "normal", False),
    ("PTSerif-Italic.ttf", 64, "regular", "italic", "normal", False),
    # monospace, pixel-drawn
    ("PressStart2P.ttf", 28, "any", "upright", "normal", True),
    # display faces: wide, no weight claim
    ("Orbitron.ttf", 48, "any", "upright", "wide", False),
    ("Michroma.ttf", 48, "any", "upright", "wide", False),
    ("Unbounded.ttf", 48, "any", "upright", "wide", False),
    ("RussoOne.ttf", 48, "any", "upright", "wide", False),
    ("Tektur.ttf", 48, "any", "upright", "normal", False),
    # narrow faces
    ("Oswald.ttf", 48, "any", "upright", "narrow", False),
    ("AlumniSans.ttf", 48, "any", "upright", "narrow", False),
    ("Cuprum.ttf", 48, "any", "upright", "narrow", False),
    ("Rajdhani-Regular.ttf", 48, "any", "upright", "normal", False),
    # rounded
    ("Comfortaa.ttf", 48, "any", "upright", "normal", False),
    ("Rubik.ttf", 48, "any", "upright", "normal", False),
    ("Nunito.ttf", 48, "any", "upright", "normal", False),
    ("Exo2.ttf", 48, "any", "upright", "normal", False),
]

# Margin around the text. Measurement is relative to the box it is given, and a
# box that is exactly the ink says nothing about a box that is not - which is
# every box the overlay is actually given.
MARGIN = 16


def render(face: str, size: int, bg, ink) -> Image.Image:
    font = ImageFont.truetype(str(FONT_DIR / face), size)
    # Measure before drawing so the canvas is the text plus a margin, not a guess
    # that clips it.
    probe = Image.new("L", (8, 8), 0)
    tight = ImageDraw.Draw(probe).textbbox((0, 0), SAMPLE, font=font)
    w = (tight[2] - tight[0]) + MARGIN * 2
    h = (tight[3] - tight[1]) + MARGIN * 2
    img = Image.new("RGB", (w, h), bg)
    ImageDraw.Draw(img).text(
        (MARGIN - tight[0], MARGIN - tight[1]), SAMPLE, font=font, fill=ink
    )
    return img


def slug(face: str, size: int, bg_name: str) -> str:
    stem = face.rsplit(".", 1)[0]
    return f"{stem}-{size}-{bg_name}"


def glyph_spans(face: str, size: int) -> list[int]:
    """Width of every letter, in pixels.

    Monospace is the one property that cannot be read off a single number: a
    monospaced face gives every glyph the same advance, so its letter widths
    differ by a pixel or two at the edges and are otherwise identical. A
    proportional face gives a range instead. `GlyphMetrics.tracking` cannot
    answer this - it comes back quantised to whole pixels, and measured on the
    fixtures it reads 3…9 for faces whose letter widths span 19…27, so the two
    groups land on the same values.
    """

    font = ImageFont.truetype(str(FONT_DIR / face), size)
    img = Image.new("RGB", (size * 40, size * 3 + 40), BG_DARK)
    ImageDraw.Draw(img).text((30, 20), SAMPLE, font=font, fill=INK_LIGHT)
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    mask, _ = _mask_from_array(arr)
    if mask is None:
        return []
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = mask[rows[0] : rows[-1] + 1].any(axis=0)
    out: list[int] = []
    start: int | None = None
    for x, v in enumerate(cols):
        if v and start is None:
            start = x
        elif not v and start is not None:
            out.append(x - start)
            start = None
    if start is not None:
        out.append(len(cols) - start)
    # A run of one or two pixels is an antialiasing artefact on the edge of a
    # letter, not a letter. The serif on a "l" is that wide and would otherwise
    # dominate the spread of a face that is genuinely monospaced.
    return [w for w in out if w >= 4]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for face, size, weight, slant, width, mono in FACES:
        spans = glyph_spans(face, size)
        for bg_name, bg, ink in (
            ("dark", BG_DARK, INK_LIGHT),
            ("light", BG_LIGHT, INK_DARK),
        ):
            img = render(face, size, bg, ink)
            name = slug(face, size, bg_name)
            img.save(OUT_DIR / f"{name}.png")
            entries.append(
                {
                    "name": name,
                    "file": face,
                    "size": size,
                    "text": SAMPLE,
                    "background": bg_name,
                    "weight": weight,
                    "slant": slant,
                    "width": width,
                    "mono": mono,
                    "size_px": list(img.size),
                    "glyph_widths": spans,
                }
            )
    manifest = {"sample": SAMPLE, "margin": MARGIN, "faces": entries}
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(entries)} фикстур в {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
