"""The card path must not re-measure the same pixels.

`make_block` measures a box, then measures it again after each extension of a
name plate's edges - four measurements of one paragraph on a name plate, the
most expensive thing in the draw path at ~10-19 ms each. The answer cannot differ
inside one frame, so it is kept per frame.

The cache is keyed on the identity of the image object. A new frame is a new
object and therefore a new key: it cannot read an answer measured against
different pixels, and the cache only has to be bounded, not invalidated.

    uv run pytest tests/test_measure_cache.py
"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from kizurium_translator.typography import metrics as M
from kizurium_translator.typography.metrics import (
    clear_measurement_cache,
    ink_is_light,
    measure_glyph_metrics,
)

# One of the committed corpus pictures. It used to be a screenshot of a game on
# the machine this was written on, at an absolute path, which made the test pass
# here and fail everywhere else - and put a game screenshot in a repository whose
# own documentation forbids shipping copyrighted game images.
SHOT = Path(__file__).parent / "fixtures" / "type" / "Play-Regular-48-dark.png"
BOX = (0, 0, 300, 40)


def _img() -> Image.Image:
    if not SHOT.is_file():
        raise AssertionError(f"нет фикстуры {SHOT}")
    return Image.open(SHOT).convert("RGB")


def test_the_same_box_measures_once_per_frame():
    img = _img()
    clear_measurement_cache()
    first = measure_glyph_metrics(img, BOX)
    for _ in range(50):
        assert measure_glyph_metrics(img, BOX) == first


def test_a_second_frame_is_measured_again():
    """New pixels, new answer - the cache cannot outlive its frame."""
    img = _img()
    clear_measurement_cache()
    before = measure_glyph_metrics(img, BOX)
    # Same object identity would be a lie here: a copy is a different frame.
    other = measure_glyph_metrics(img.copy(), BOX)
    assert other.height == before.height, "одинаковые пиксели - одинаковый ответ"
    assert len(M._MEASURE_CACHE) == 2, "два кадра - две записи"


def test_the_cache_is_bounded():
    clear_measurement_cache()
    img = _img()
    for i in range(M._MEASURE_CACHE_MAX + 40):
        measure_glyph_metrics(img, (100 + i % 7, 100, 400 + i % 5, 200))
    assert len(M._MEASURE_CACHE) <= M._MEASURE_CACHE_MAX


def test_clearing_throws_the_frame_away():
    img = _img()
    clear_measurement_cache()
    measure_glyph_metrics(img, BOX)
    assert len(M._MEASURE_CACHE) == 1
    clear_measurement_cache()
    assert len(M._MEASURE_CACHE) == 0
    assert len(M._LIGHT_CACHE) == 0


def test_light_ink_is_cached_too():
    img = _img()
    clear_measurement_cache()
    first = ink_is_light(img, BOX)
    assert ink_is_light(img, BOX) is first


def test_ink_light_still_answers_correctly():
    """Caching must not change the answer, only how often it is computed."""
    img = _img()
    clear_measurement_cache()
    cached = ink_is_light(img, BOX)
    clear_measurement_cache()
    direct = M._ink_is_light_uncached(img, BOX)
    assert cached == direct


def test_glyph_metrics_answer_is_unchanged_by_caching():
    img = _img()
    clear_measurement_cache()
    cached = measure_glyph_metrics(img, BOX)
    clear_measurement_cache()
    direct = M._measure_glyph_metrics_uncached(img, BOX)
    assert cached == direct


def test_repeat_measurement_is_cheap():
    """The point of the cache, measured rather than asserted."""
    img = _img()
    clear_measurement_cache()
    measure_glyph_metrics(img, BOX)
    t0 = time.perf_counter()
    for _ in range(200):
        measure_glyph_metrics(img, BOX)
    per_call_ms = (time.perf_counter() - t0) / 200 * 1000
    assert per_call_ms < 0.5, f"повторное измерение {per_call_ms:.3f} мс - кэш не работает"


def test_missing_image_and_empty_box_are_still_safe():
    assert measure_glyph_metrics(None, BOX) == M.GlyphMetrics()
    img = _img()
    assert measure_glyph_metrics(img, (0, 0, 0, 0)) == M.GlyphMetrics()
    assert ink_is_light(None, BOX) is False


def test_the_typography_module_owns_the_measurement():
    """names the five symbols; they must all live in typography."""
    for name in (
        "measure_glyph_metrics",
        "measure_word_colors",
        "detect_overlay_typeface",
        "GlyphMetrics",
        "ColorSpan",
    ):
        assert hasattr(M, name), f"{name} не в typography"
        import kizurium_translator.typography as pkg

        assert hasattr(pkg, name), f"{name} не реэкспортирован"


def test_render_imports_measurement_from_typography():
    """render measures through typography, not through a private path."""
    src = Path("src/kizurium_translator/render/cards.py").read_text(encoding="utf-8")
    assert "from ..typography.metrics import" in src
    assert "def measure_glyph_metrics" not in src
    assert "def detect_overlay_typeface" not in src
