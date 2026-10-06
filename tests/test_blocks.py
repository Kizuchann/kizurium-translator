"""Block model: language per block, spatial tracking, short-text changes.

These are the behaviours the live overlay depends on:

* a frame with English and Japanese next to each other keeps both languages
* an element that changes in place is detected as the same element with new
  content, including one-word and two-word changes
* an unchanged element is not reported as changed, so it is not re-translated
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import owner, patch_all

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import (  # noqa: E402
    SCRIPT_EN,
    SCRIPT_JA,
    SCRIPT_MIXED,
    SCRIPT_TARGET,
    TrackedBlock,
)


def blk(text: str, box=(10, 20, 200, 50), conf: float = 95.0) -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=box,
        text=text,
        norm=live.normalize_for_compare(text),
        script=live.block_script(text),
        lang=live.block_lang(text),
        conf=conf,
        engine="rapidocr",
    )


class TestScriptDetection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Press E to interact", SCRIPT_EN),
            ("Settings", SCRIPT_EN),
            ("これはテストです", SCRIPT_JA),
            ("閉じる", SCRIPT_JA),
            ("第3章", SCRIPT_JA),
            ("Story ストーリー", SCRIPT_MIXED),
            ("Глава 3", SCRIPT_TARGET),
            ("", "unknown"),
        ],
    )
    def test_script(self, text, expected):
        assert live.block_script(text) == expected

    def test_source_lang(self):
        assert live.source_lang_for(SCRIPT_EN) == "en"
        assert live.source_lang_for(SCRIPT_JA) == "ja"
        assert live.source_lang_for(SCRIPT_MIXED) == "auto"
        assert live.source_lang_for("unknown") == "auto"

    def test_mixed_frame_keeps_both_languages(self):
        """The point of the block model: language is not a frame-wide flag."""
        lines = [
            {"text": "Story 3", "box": (10, 10, 120, 34), "conf": 99.0},
            {"text": "ストーリー", "box": (300, 10, 460, 34), "conf": 99.0},
            {"text": "Raise your Tension", "box": (10, 50, 260, 74), "conf": 99.0},
            {"text": "タップして開始", "box": (300, 50, 470, 74), "conf": 99.0},
        ]
        blocks, _pairs, _next = live.track_blocks([], lines, 0.0, 1)
        assert [b.script for b in blocks].count(SCRIPT_JA) == 2
        assert [b.script for b in blocks].count(SCRIPT_EN) == 2
        langs = {live.source_lang_for(b.script) for b in blocks}
        assert langs == {"en", "ja"}


class TestSpatialTracking:
    def test_same_place_is_the_same_element(self):
        prev = [blk("One", (100, 100, 180, 130))]
        cur = [{"text": "Two", "box": (101, 101, 181, 131), "conf": 95.0}]
        blocks, pairs, _ = live.track_blocks(prev, cur, 1.0, 2)
        assert len(pairs) == 1
        old, new = pairs[0]
        assert old.id == new.id
        assert live.block_content_changed(old, new.text, new.script, new.conf)

    def test_moved_element_is_still_matched(self):
        prev = [blk("Health", (100, 100, 200, 130))]
        cur = [{"text": "Health", "box": (108, 104, 208, 134), "conf": 95.0}]
        _blocks, pairs, _ = live.track_blocks(prev, cur, 1.0, 2)
        assert len(pairs) == 1

    def test_different_place_is_a_new_element(self):
        prev = [blk("Health", (10, 10, 110, 40))]
        cur = [{"text": "Health", "box": (500, 400, 600, 430), "conf": 95.0}]
        _blocks, pairs, next_id = live.track_blocks(prev, cur, 1.0, 2)
        assert pairs == []
        assert next_id == 3

    def test_ids_are_stable_across_frames(self):
        first, _p, nxt = live.track_blocks([], [{"text": "A", "box": (0, 0, 50, 20), "conf": 90}], 0.0, 1)
        second, pairs, _n = live.track_blocks(
            first, [{"text": "B", "box": (0, 0, 50, 20), "conf": 90}], 1.0, nxt
        )
        assert first[0].id == second[0].id
        assert len(pairs) == 1


class TestShortTextChanges:
    """The bug this exists for: short words disappeared behind a global
    similarity threshold, so the stale translation stayed on screen."""

    @pytest.mark.parametrize(
        "old,new",
        [
            ("One", "Two"),
            ("is", "15"),
            ("is", "it"),
            ("to", "go"),
            ("ON", "OFF"),
            ("YES", "NO"),
            ("Go", "No"),
            ("A", "B"),
            ("1", "2"),
            ("10", "11"),
            ("Lv. 9", "Lv. 10"),
            ("1/2", "2/2"),
        ],
    )
    def test_short_change_is_detected(self, old, new):
        b = blk(old)
        assert live.block_content_changed(b, new, live.block_script(new), 95.0)

    def test_unchanged_text_is_not_a_change(self):
        b = blk("Counter: One")
        assert not live.block_content_changed(b, "Counter: One", SCRIPT_EN, 95.0)

    def test_jitter_in_a_long_line_is_tolerated(self):
        """Long text may wobble a little; that is OCR jitter, not a new value."""
        b = blk("Chapter 3 - The Frozen Harbour", conf=95.0)
        assert not live.block_content_changed(
            b, "Chapter 3 - The Frozen Harbur", SCRIPT_EN, 95.0
        )

    def test_low_confidence_forces_a_change(self):
        b = blk("Chapter 3 - The Frozen Harbour", conf=95.0)
        assert live.block_content_changed(
            b, "Chapter 3 - The Frozen Harbur", SCRIPT_EN, 40.0
        )

    def test_language_switch_counts_as_change(self):
        b = blk("Start", conf=95.0)
        assert live.block_content_changed(b, "開始", SCRIPT_JA, 95.0)


class TestTokenPlausibility:
    def test_words_beat_numbers(self):
        assert live._token_plausible("is") > live._token_plausible("15")
        assert live._token_plausible("one") > live._token_plausible("15%")

    def test_numbers_are_still_valid(self):
        assert live._token_plausible("15") > 0.0
        assert live._token_plausible("15%") > 0.0

    def test_kana_is_plausible(self):
        assert live._token_plausible("キャンセル") > 0.5

    def test_ocr_confusables_score_low(self):
        assert live._token_plausible("l5") < 0.2


class TestTranslationValidation:
    def test_japanese_output_is_not_an_error(self):
        """EN -> JA is a legitimate translation and must not be discarded."""
        assert not live.is_translation_error("Hello", "こんにちは")

    def test_empty_and_provider_errors_are_errors(self):
        assert live.is_translation_error("Hello", "")
        assert live.is_translation_error("Hello", "server error")
        assert live.is_translation_error("Hello", "No translation was found")


class TestTranslateManyItems:
    def test_per_item_language(self, monkeypatch):
        """A mixed batch must send each item with its own source language."""
        live.configure(live.Config(target_lang="ru"))
        tr = live.translator()
        seen: list[tuple[str, str]] = []

        def fake_gtx(text, source=None, **kw):  # noqa: ANN001, ANN003
            seen.append((source or "", text))
            return ""

        monkeypatch.setattr(tr, "via_gtx", fake_gtx)
        items = [
            {"text": "Lantern", "source": "en"},
            {"text": "灯台", "source": "ja"},
            {"text": "Magnolia", "source": "en"},
        ]
        # No backend answers, so the text is returned unchanged: the point is
        # that a mixed batch is accepted at all instead of being forced to one
        # language, and that nothing is sent for a cache/glossary hit.
        assert live.translate_many(items, {}) == ["Lantern", "灯台", "Magnolia"]
        for src, _payload in seen:
            assert src in ("en", "ja", "auto")
        live.shutdown()

    def test_disabled_translation_is_identity(self, monkeypatch):
        live.configure(live.Config())
        # Флаг - поле объекта состояния перевода. Присваивание в модуль
        # меняло бы копию из реэкспорта и ничего не отключало; раньше это
        # стоило отдельной правки, потому что флаг читали два слоя.
        from kizurium_translator.live import TRANSLATION

        TRANSLATION.disabled = True
        try:
            assert live.translate_many(
                ["abc", {"text": "def", "source": "en"}], {}
            ) == ["abc", "def"]
        finally:
            TRANSLATION.disabled = False
        live.shutdown()


class TestRegionTextWithMixed:
    def test_uses_engine_pipeline(self, monkeypatch):
        """OCR-copy must not have a second recognition system."""
        seen: dict = {}
        patch_all(monkeypatch, "region_text", lambda img, hint=None: "Settings 設定")
        assert "設定" in live.region_text(object())


class TestMojibakeHeuristics:
    """Garbage detection must not swallow normal English sentences.

    A sentence with a few short words used to be classified as Cyrillic read as
    Latin, and the line was dropped from translation without any trace.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Press E to interact with the do or",
            "Press E to interact with the door",
            "Are you sure?",
            "This will lose unsaved progress",
            "Raise your Tension",
            "Tap to interact",
        ],
    )
    def test_english_is_not_garbage(self, text):
        assert not live.is_overlay_echo_ocr(text)
        assert not live.looks_like_ocr_mojibake_of_russian(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Bai 3 e Ma H: Bep Ho",
            "Hactpon Ckpmh Becb",
        ],
    )
    def test_real_garbage_is_still_detected(self, text):
        assert live.looks_like_ocr_mojibake_of_russian(text)

    def test_english_word_counter(self):
        assert live._real_english_words("Press E to interact with the door") >= 5
        assert live._real_english_words("Tbl WMee Wb B BWAy") == 0

    def test_line_survives_the_translation_filter(self):
        """The exact regression: this line never reached the translator."""
        line = "Press E to interact with the do or"
        rejected = (
            live.is_garbage_ocr(line)
            or live.looks_like_ocr_mojibake_of_russian(line)
            or live.is_overlay_echo_ocr(line)
        )
        assert not rejected
