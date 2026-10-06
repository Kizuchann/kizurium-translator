"""The committed type corpus: does the measurement read the face back?

`measure_glyph_metrics` and `estimate_italic_slant` decide which face the
translation is drawn in. A number that is 15% out does not fail anything - it
produces a card that is quietly the wrong size in the wrong weight, which is the
kind of defect nobody finds by reading the code.

The fixtures in `tests/fixtures/type` are drawn by `scripts/render_fixtures.py`
in a face whose properties are known from the file name and the design, and the
manifest states those properties. This test asserts the measurement agrees with
the manifest. It is committed rather than generated inside the test, so the
numbers are checked on a machine that did not draw them.

Two rules keep it honest:

- A face the manifest says nothing about is not asserted. Adding a line to
  `FACES` is a deliberate claim, not a side effect of dropping a TTF into the
  directory.
- The thresholds live in the code under test, and this test reads them from
  there. A threshold rewritten to make a fixture pass fails here, because the
  fixture and the threshold would then agree about nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from kizurium_translator.core import text as core_text
from kizurium_translator.typography import estimate_italic_slant, measure_glyph_metrics
from kizurium_translator.typography.metrics import _SLANT_ITALIC, _mask_from_array

FIXTURES = Path(__file__).parent / "fixtures" / "type"
MANIFEST = FIXTURES / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def measured(manifest) -> list[dict]:
    """Every fixture with its ink box and the metrics read off it."""

    out = []
    for entry in manifest["faces"]:
        img = Image.open(FIXTURES / f"{entry['name']}.png")
        mask, _ = _mask_from_array(np.asarray(img.convert("L"), dtype=np.float32))
        assert mask is not None, f"{entry['name']}: no ink mask"
        ys, xs = np.nonzero(mask)
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        out.append(
            {
                "entry": entry,
                "img": img,
                "box": box,
                "metrics": measure_glyph_metrics(img, box),
                "slant": estimate_italic_slant(img, box),
                "ink_h": int(ys.max() - ys.min()),
            }
        )
    return out


def _names(measured, **match) -> list[str]:
    return [m["entry"]["name"] for m in measured if all(m["entry"][k] == v for k, v in match.items())]


class TestTheCorpusExists:
    def test_every_manifest_entry_has_its_picture(self, manifest):
        missing = [
            e["name"]
            for e in manifest["faces"]
            if not (FIXTURES / f"{e['name']}.png").is_file()
        ]
        assert not missing, f"в манифесте есть, на диске нет: {missing}"

    def test_every_picture_is_in_the_manifest(self, manifest):
        named = {f"{e['name']}.png" for e in manifest["faces"]}
        on_disk = {p.name for p in FIXTURES.glob("*.png")}
        assert named == on_disk, f"расхождение: {named ^ on_disk}"

    def test_both_backgrounds_are_drawn_for_every_face(self, manifest):
        by_face: dict[str, set[str]] = {}
        for e in manifest["faces"]:
            by_face.setdefault(e["file"], set()).add(e["background"])
        thin = {f: b for f, b in by_face.items() if b != {"dark", "light"}}
        assert not thin, f"нарисован только один фон: {thin}"


class TestInkHeight:
    """The height the card is sized from, against the size that was drawn."""

    def test_ink_height_is_within_a_quarter_of_the_size(self, measured):
        # A sample with an ascender and a descender measures close to the em
        # size. The tolerance covers faces whose cap height differs: Rajdhani
        # draws 41px of ink at 48, AlumniSans 37. What it must exclude is a
        # measurement off by half, which is what reading the box instead of the
        # ink would give.
        for m in measured:
            ratio = m["ink_h"] / m["entry"]["size"]
            assert 0.72 <= ratio <= 1.06, (
                f"{m['entry']['name']}: чернила {m['ink_h']}px при кегле "
                f"{m['entry']['size']} ({ratio:.2f})"
            )

    def test_the_sample_is_what_the_manifest_says(self, manifest):
        sample = manifest["sample"]
        assert any(ch.islower() for ch in sample), "нужны строчные"
        assert any(ch.isupper() for ch in sample), "нужны заглавные"
        assert any(ch.isdigit() for ch in sample), "нужны цифры"


class TestWeight:
    def test_bold_measures_heavier_than_regular(self, measured):
        bold = [m["metrics"].stroke for m in measured if m["entry"]["weight"] == "bold"]
        regular = [
            m["metrics"].stroke for m in measured if m["entry"]["weight"] == "regular"
        ]
        assert bold and regular, "в корпусе нет пары для сравнения"
        assert min(bold) > max(regular), (
            f"жирные {min(bold):.3f}…{max(bold):.3f} не отделены от "
            f"обычных {min(regular):.3f}…{max(regular):.3f}"
        )

    def test_the_display_threshold_separates_bold_from_regular(self, measured):
        """`core.text._role_for` calls a face "display" at stroke >= 0.16.

        Only for boxes at least 30px of ink tall, which is where that line is
        read. Below it the number is not comparable across sizes - the same
        upright face reads 0.167 at 18px and 0.109 at 64px, because a stroke two
        pixels wide is a fifth of an 18px line and a twentieth of a 64px one, and
        because the ink mask takes in the anti-aliased halo, which at small sizes
        is the same width as the stroke itself. A threshold read at a fixed pixel
        height is only meaningful inside the range it was fitted to.
        """

        tall = [m for m in measured if m["ink_h"] >= 30]
        bold = [m["metrics"].stroke for m in tall if m["entry"]["weight"] == "bold"]
        regular = [m["metrics"].stroke for m in tall if m["entry"]["weight"] == "regular"]
        assert bold and regular, "в корпусе нет крупной пары для сравнения"
        assert min(bold) >= 0.16, f"жирный не достигает 0.16: {min(bold):.3f}"
        assert max(regular) < 0.16, f"обычный дотягивает до 0.16: {max(regular):.3f}"


def _tall(measured):
    """Fixtures where the slant is supposed to mean something."""

    from kizurium_translator.typography.metrics import _SLANT_MIN_INK_H

    return [m for m in measured if m["ink_h"] >= _SLANT_MIN_INK_H]


class TestSlant:
    def test_italic_reads_above_the_threshold_and_upright_below(self, measured):
        tall = _tall(measured)
        italic = [m["slant"] for m in tall if m["entry"]["slant"] == "italic"]
        upright = [m["slant"] for m in tall if m["entry"]["slant"] == "upright"]
        assert italic and upright, "в корпусе нет курсива для сравнения"
        assert min(italic) >= _SLANT_ITALIC, (
            f"курсив ниже порога {_SLANT_ITALIC}: {min(italic):+.3f}"
        )
        assert max(abs(s) for s in upright) < _SLANT_ITALIC, (
            f"прямой тянется до порога: {max(abs(s) for s in upright):.3f}"
        )

    def test_the_threshold_is_not_a_measurement_in_disguise(self, measured):
        """It has to sit in the gap, not against one end of it."""

        tall = _tall(measured)
        italic = [m["slant"] for m in tall if m["entry"]["slant"] == "italic"]
        upright = [m["slant"] for m in tall if m["entry"]["slant"] == "upright"]
        low, high = max(abs(s) for s in upright), min(italic)
        assert low < _SLANT_ITALIC < high, (
            f"порог {_SLANT_ITALIC} вне зазора {low:.3f}…{high:.3f}"
        )

    def test_slant_does_not_depend_on_the_background(self, measured):
        pairs: dict[str, dict[str, float]] = {}
        for m in _tall(measured):
            stem = m["entry"]["name"].rsplit("-", 1)[0]
            pairs.setdefault(stem, {})[m["entry"]["background"]] = m["slant"]
        assert pairs, "в корпусе нет ни одной измеримой фикстуры"
        for stem, v in pairs.items():
            assert abs(v["dark"] - v["light"]) < 0.02, (
                f"{stem}: тёмный фон {v['dark']:+.3f}, светлый {v['light']:+.3f}"
            )


class TestPolarity:
    """The same measurement on both kinds of background.

    `estimate_italic_slant` once picked its ink threshold the wrong way round and
    returned 0.0 for every input on a dark background - light ink, and a test for
    pixels darker than the median finds none of them. `detect_overlay_typeface`
    then decided no text was ever slanted, and the thresholds beside it could
    not have been checked against a real face.
    """

    def test_metrics_match_across_backgrounds(self, measured):
        pairs: dict[str, dict[str, object]] = {}
        for m in measured:
            stem = m["entry"]["name"].rsplit("-", 1)[0]
            pairs.setdefault(stem, {})[m["entry"]["background"]] = m["metrics"]
        for stem, v in pairs.items():
            dark, light = v["dark"], v["light"]
            assert dark.height == light.height, f"{stem}: высота {dark.height} vs {light.height}"
            assert dark.width == light.width, f"{stem}: ширина {dark.width} vs {light.width}"
            assert abs(dark.stroke - light.stroke) < 0.02, (
                f"{stem}: толщина {dark.stroke:.3f} vs {light.stroke:.3f}"
            )

    def test_the_ink_colour_is_read_off_the_picture(self, measured):
        for m in measured:
            light_ink = m["entry"]["background"] == "dark"
            r, g, b = m["metrics"].color
            if light_ink:
                assert r > 0.6 and g > 0.6 and b > 0.6, f"{m['entry']['name']}: {m['metrics'].color}"
            else:
                assert r < 0.4 and g < 0.4 and b < 0.4, f"{m['entry']['name']}: {m['metrics'].color}"

    def test_ink_is_light_agrees_with_the_picture(self, measured):
        """Light ink on a dark field, on every fixture.

        It used to read the top quartile of brightness and call that the text,
        which only works if the text covers more than a quarter of the box. Real
        glyphs do not: "Handgloves 0123" in NotoSans at 28px covers 26% of its
        own box, so the quartile fell in the field and every light line on a
        dark panel was reported as dark text. The box had been resized to 48x16
        first as well, and that filter averaged a two-pixel stroke away.
        """

        from kizurium_translator.typography.metrics import _ink_is_light_uncached

        checked = 0
        for m in measured:
            got = _ink_is_light_uncached(m["img"], m["box"])
            assert got is (m["entry"]["background"] == "dark"), (
                f"{m['entry']['name']}: фон {m['entry']['background']}, "
                f"чернила прочитаны как {'светлые' if got else 'тёмные'}"
            )
            checked += 1
        assert checked >= 40, f"проверено только {checked} фикстур"

    def test_ink_is_light_holds_when_the_box_is_larger_than_the_text(self, measured):
        """A block box is padded, and the answer must not depend on the padding."""

        from kizurium_translator.typography.metrics import _ink_is_light_uncached

        sample = next(m for m in measured if m["entry"]["name"] == "NotoSans-48-dark")
        x1, y1, x2, y2 = sample["box"]
        for pad in (0, 8, 32):
            box = (max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad)
            assert _ink_is_light_uncached(sample["img"], box) is True, f"поле {pad}px"

    def test_a_flat_box_has_no_ink_to_measure(self):
        from kizurium_translator.typography.metrics import _ink_is_light_uncached

        assert _ink_is_light_uncached(Image.new("RGB", (40, 20), (90, 90, 90)), (0, 0, 40, 20)) is False
        assert _ink_is_light_uncached(None, (0, 0, 40, 20)) is False


class TestWidth:
    def test_a_narrow_face_measures_narrower_per_height(self, measured):
        narrow = [m["metrics"].aspect for m in measured if m["entry"]["width"] == "narrow"]
        rest = [m["metrics"].aspect for m in measured if m["entry"]["width"] != "narrow"]
        assert narrow and rest, "в корпусе нет узких гарнитур"
        assert max(narrow) < min(rest), (
            f"узкие {min(narrow):.2f}…{max(narrow):.2f} не отделены от "
            f"остальных {min(rest):.2f}…{max(rest):.2f}"
        )


class TestMonospace:
    def test_a_monospaced_face_gives_its_glyphs_the_same_width(self, manifest):
        mono, prop = [], []
        seen = set()
        for e in manifest["faces"]:
            if e["background"] != "dark" or e["file"] in seen:
                continue
            seen.add(e["file"])
            widths = e["glyph_widths"]
            if len(widths) < 6:
                continue
            spread = max(widths) - min(widths)
            (mono if e["mono"] else prop).append((e["file"], spread))
        assert mono and prop, "в корпусе нет моноширинной гарнитуры"
        worst_mono = max(s for _, s in mono)
        best_prop = min(s for _, s in prop)
        assert worst_mono < best_prop, (
            f"моно {mono} не отделены от пропорциональных: худший разброс "
            f"{worst_mono}, лучший {best_prop}"
        )


class TestSmallText:
    def test_slant_is_not_claimed_below_the_minimum_ink_height(self, measured):
        """Below the minimum the number is noise, and it says so by returning 0.

        Measured across 27 faces: from 35px of ink the italic reads +0.14 and
        the worst upright +0.01. At 18px the same italic reads -0.28, and
        NotoSans at 14px reads +0.05 - over the italic threshold, on a face that
        is not slanted at all. The groups overlap and the sign is inverted,
        which means a number here is not a small slant but a wrong one.

        0.0 is the honest answer and the caller already reads it as "no italic",
        which is the right reading of a line too small to measure.
        """

        from kizurium_translator.typography.metrics import _SLANT_MIN_INK_H

        small = [m for m in measured if m["ink_h"] < _SLANT_MIN_INK_H]
        assert small, (
            "в корпусе нет фикстур ниже минимума высоты, и снятие минимума "
            "ничего бы не сломало"
        )
        for m in small:
            assert m["slant"] == 0.0, (
                f"{m['entry']['name']}: чернила {m['ink_h']}px, "
                f"наклон {m['slant']:+.3f} при минимуме {_SLANT_MIN_INK_H}"
            )

    def test_a_tiny_upright_face_would_pass_the_threshold_without_the_minimum(
        self, measured
    ):
        """The specific failure the minimum exists to prevent.

        Held to the value the measurement produces when the minimum is removed,
        so that deleting `_SLANT_MIN_INK_H` breaks a test rather than quietly
        making every line of small text look italic.
        """

        from kizurium_translator.typography.metrics import _mask_from_array as mask_of

        noto14 = next(
            m
            for m in measured
            if m["entry"]["name"] == "NotoSans-14-dark"
        )
        # Re-measure without the floor by asking the module for its own numbers
        # on a crop that is tall enough to pass, then comparing. Simpler and just
        # as direct: assert the fixture exists and is below the floor.
        assert noto14["ink_h"] < 34, f"ink_h={noto14['ink_h']}"
        assert noto14["slant"] == 0.0
        # The same ink, measured with the floor removed.
        import kizurium_translator.typography.metrics as metrics_module

        original = metrics_module._SLANT_MIN_INK_H
        try:
            metrics_module._SLANT_MIN_INK_H = 0
            undecided = estimate_italic_slant(noto14["img"], noto14["box"])
        finally:
            metrics_module._SLANT_MIN_INK_H = original
        assert abs(undecided) >= _SLANT_ITALIC, (
            f"без минимума NotoSans-14 читается {undecided:+.3f}, и это уже не "
            "отличить от курсива - минимум высоты держит единственное, что их "
            "различает"
        )
