"""A frame is not a language.

The screen can be EN on the left and JP on the right, on the same frame, read by
the same multilingual pass. These tests pin the two decisions that used to get
that wrong: the language is taken from each block, and no early return from the
English path throws the Japanese away.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources, owner, patch_all

from kizurium_translator import live  # noqa: E402

JP = "開始"  # kanji only: Japanese-shaped, language honestly "auto"
JP_KANA = "こんにちは"  # kana: unmistakably Japanese
JP_LONG = "可以进行"
EN = "Settings"


def _line(text: str, box=(0, 0, 10, 10), conf: float = 90.0) -> dict:
    return {"text": text, "box": box, "conf": conf}


class TestPerBlockLanguage:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("こんにちは", "ja"),
            ("ライブ", "ja"),
            ("Hello", "en"),
            ("Settings", "en"),
            ("안녕하세요", "ko"),
            ("안녕", "ko"),
            ("Привет", "ru"),
            ("選択", "auto"),
            ("选择", "auto"),
            ("", ""),
        ],
    )
    def test_the_language_comes_from_the_block(self, text, expected):
        assert live.block_lang(text) == expected

    def test_kanji_only_is_honest_rather_than_guessed(self):
        """Kanji-only is Japanese UI and Chinese UI at once.

        Guessing "ja" would be a coin flip with a wrong answer half the time, and
        the backend reads both, so the honest answer costs nothing.
        """
        assert live.block_lang("強化") == live.block_lang("强化") == "auto"

    def test_a_frame_may_hold_several_languages_at_once(self):
        blocks = [_line(EN), _line(JP_LONG), _line(EN), _line(JP), _line(EN)]
        assert [live.block_lang(str(b["text"])) for b in blocks] == [
            "en",
            "auto",
            "en",
            "auto",
            "en",
        ]
        assert [live.block_lang(str(b["text"])) for b in blocks[:3] + [blocks[4]]] == [
            "en",
            "auto",
            "en",
            "en",
        ]


class TestResultsCarryTheirProvenance:
    def test_every_result_gets_the_five_fields(self):
        got = live.annotate_lines([_line(JP_KANA, conf=61.0)], "rapid")
        assert got[0]["engine"] == "rapid"
        assert got[0]["conf"] == 61.0
        assert got[0]["lang"] == "ja"
        assert got[0]["script"] == live.SCRIPT_JA
        assert got[0]["box"] == (0, 0, 10, 10)

    def test_a_refined_block_keeps_saying_which_engine_read_it(self):
        got = live.annotate_lines([{**_line(JP_LONG), "engine": "meiki"}], "rapid")
        assert got[0]["engine"] == "meiki"

    def test_annotating_twice_changes_nothing(self):
        once = live.annotate_lines([_line(JP_LONG)], "rapid")
        twice = live.annotate_lines(once, "tesseract")
        assert once[0]["engine"] == twice[0]["engine"] == "rapid"
        assert once[0]["lang"] == twice[0]["lang"]

    def test_annotating_does_not_invent_a_language(self):
        assert live.annotate_lines([_line("")], "rapid")[0]["lang"] == ""

    def test_japanese_is_picked_out_of_a_multilingual_result(self):
        raw = [_line(EN), _line(JP_LONG), _line("Hello"), _line(JP_KANA), _line("Привет")]
        picked = [str(b["text"]) for b in live.japanese_blocks(raw)]
        assert picked == [JP_LONG, JP_KANA]


class TestNoEarlyReturnDropsJapanese:
    def test_the_english_exits_all_go_through_one_function(self):
        """One exit, so no path can decide the language on its own."""
        source = inspect.getsource(live.ocr_image)
        stray = [
            ln.strip()
            for ln in source.split("\n")
            if ln.strip().startswith("return ") and '"eng-ui"' in ln
            and "finish_mixed_frame" not in ln
            and ln.strip() != 'return [], hint or "eng-ui"'
        ]
        assert stray == [], stray

    def test_an_empty_frame_still_returns_empty(self):
        """Nothing to keep is not a reason to invent something."""
        assert 'return [], hint or "eng-ui"' in inspect.getsource(live.ocr_image)

    def test_every_english_exit_receives_the_same_pass(self):
        source = inspect.getsource(live.ocr_image)
        calls = [
            ln.strip()
            for ln in source.split("\n")
            if "finish_mixed_frame(" in ln and ln.strip().startswith("return ")
        ]
        assert len(calls) >= 10, len(calls)
        for call in calls:
            assert "raw_all" in call, call

    def test_every_unfiltered_read_feeds_the_accumulator(self):
        """The Japanese is only free if it was already read."""
        source = inspect.getsource(live.ocr_image)
        for read in (
            "rapid_ocr_lines(work_band)",
            "rapid_ocr_lines(region_img, max_side=1280)",
            "ocr_region_by_columns(region_img, [])",
        ):
            idx = source.index(read)
            assert "raw_all.extend" in source[idx: idx + 200], read

    def test_japanese_joins_an_english_frame_with_its_own_language(self, monkeypatch):
        raw = [_line("Settings", box=(0, 0, 100, 20)), _line(JP_LONG, box=(0, 40, 100, 60))]
        patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))
        got, kind = live.finish_mixed_frame(
            [raw[0]], raw, None, "eng-ui", 40
        )
        assert kind == "eng-ui"
        assert [str(b["text"]) for b in got] == ["Settings", JP_LONG]
        assert got[1]["lang"] == "auto"
        assert got[0]["lang"] == "en"

    def test_japanese_already_in_the_frame_is_not_added_twice(self, monkeypatch):
        raw = [_line("Settings", box=(0, 0, 100, 20)), _line(JP_LONG, box=(0, 40, 100, 60))]
        patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))
        got, _ = live.finish_mixed_frame([raw[0], raw[1]], raw, None, "eng-ui", 40)
        assert len(got) == 2

    def test_the_same_block_read_twice_stays_one_block(self, monkeypatch):
        """Two reads of one element is one element, not two cards."""
        patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))
        kept = [_line(JP_KANA, box=(0, 0, 100, 20))]
        raw = kept + [_line("  " + JP_KANA + " ", box=(0, 0, 100, 20))]
        got, _ = live.finish_mixed_frame(kept, raw, None, "eng-ui", 40)
        assert len(got) == 1

    def test_two_different_japanese_blocks_are_two_blocks(self, monkeypatch):
        patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))
        raw = [_line(JP_KANA, box=(0, 0, 100, 20)), _line(JP_LONG, box=(0, 40, 100, 60))]
        got, _ = live.finish_mixed_frame([], raw, None, "eng-ui", 40)
        assert [str(b["text"]) for b in got] == [JP_KANA, JP_LONG]

    def test_a_pure_english_frame_is_left_exactly_as_it_was(self, monkeypatch):
        """The common case must not pay for the fix."""
        raw = [_line("Settings", box=(0, 0, 100, 20))]
        monkeypatch.setattr(
            live, "refine_japanese_blocks", lambda *_a: pytest.fail("refined an English frame")
        )
        got, kind = live.finish_mixed_frame(list(raw), raw, None, "eng-ui", 40)
        assert got == raw
        assert kind == "eng-ui"

    def test_a_failing_refinement_keeps_the_rapid_reading(self, monkeypatch):
        """A second opinion that crashes must not cost the first one."""

        def boom(*_a):
            raise RuntimeError("engine died")

        raw = [_line("Settings", box=(0, 0, 100, 20)), _line(JP_LONG, box=(0, 40, 100, 60))]
        patch_all(monkeypatch, "refine_japanese_blocks", boom)
        got, _ = live.finish_mixed_frame([raw[0]], raw, None, "eng-ui", 40)
        assert [str(b["text"]) for b in got] == ["Settings", JP_LONG]

    def test_a_block_whitespace_differs_is_merged_below_the_cap(self, monkeypatch):
        patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))
        kept = [_line(f"Item {i}", box=(0, i * 20, 100, i * 20 + 18)) for i in range(4)]
        raw = kept + [_line(JP, box=(0, 200, 100, 220))]
        got, _ = live.finish_mixed_frame(kept, raw, None, "eng-ui", 40)
        assert JP in [str(b["text"]) for b in got]


class TestJapaneseRefinement:
    """Meiki re-reads crops, never the screen."""

    def test_one_character_japanese_is_worth_a_second_opinion(self):
        """A single kanji is the read Rapid is least sure of.

        The length guard used to be two characters, so exactly the blocks that
        needed help most were the ones never re-read. They were not refused -
        they were never asked.
        """
        assert live.JP_REFINE_MIN_CHARS == 1

    @pytest.mark.parametrize("text", [JP, "無", "強", "気", "選", JP_KANA, "参加"])
    def test_a_doubtful_single_kanji_is_a_refinement_target(self, text):
        """One character, and it is the read Rapid is least sure of."""
        target = live.is_japanese_block(text) and (
            len(live.RE_JPN.findall(text)) >= live.JP_REFINE_MIN_CHARS
        )
        assert target, text

    def test_a_single_kanji_is_japanese_shaped_even_when_block_script_says_unknown(self):
        """The reason it vanished: two Han characters were needed to qualify."""
        assert live.block_script("無") == live.SCRIPT_UNKNOWN
        assert live.is_japanese_block("無")

    @pytest.mark.parametrize("text", ["Settings", "Hello world", "Привет", "", "12345", "ABCDEFG"])
    def test_latin_and_russian_are_not_japanese_shaped(self, text):
        assert not live.is_japanese_block(text)

    def test_a_confident_japanese_block_is_left_alone(self):
        """High quality means no second pass, which is where the cost was."""
        line = _line("こんにちは、はじめまして", conf=99.0)
        kana = len(live.RE_KANA.findall(str(line["text"])))
        doubtful = (
            float(line["conf"]) < live.JP_REFINE_CONF
            or kana == 0
            or len(live.RE_HAN.findall(str(line["text"])))
            > len(live.RE_KANA.findall(str(line["text"]))) * 2
        )
        assert not doubtful

    def test_a_doubtful_block_without_kana_is_still_doubtful(self):
        line = _line("強化", conf=95.0)
        assert len(live.RE_KANA.findall(str(line["text"]))) == 0

    def test_refinement_only_ever_looks_at_japanese(self):
        raw = [_line("Settings"), _line(JP_LONG), _line("Hello"), _line(JP_KANA)]
        assert [str(b["text"]) for b in live.japanese_blocks(raw)] == [JP_LONG, JP_KANA]

    def test_refinement_writes_which_engine_read_the_block(self):
        """A refined block has to be traceable, or a wrong refinement is invisible."""
        source = inspect.getsource(live.refine_japanese_blocks)
        assert 'ln["engine"] = "meiki"' in source

    def test_no_engine_means_no_refinement(self):
        """Meiki is optional; without it the frame is still the Rapid one."""
        source = inspect.getsource(live.refine_japanese_blocks)
        assert 'if not engine_enabled("meiki")' in source
        assert "return lines, 0" in source

    def test_tesseract_can_read_japanese_for_the_rescue(self):
        """The fallback had no Japanese in it, so it could not rescue Japanese."""
        assert live.TESSERACT_JAPANESE == "jpn+eng"
        assert live.tesseract_langs_for_script("ja") == "jpn+eng"
        assert live.tesseract_langs_for_script("japanese") == "jpn+eng"
        assert live.tesseract_langs_for_script("") == "jpn+eng+rus"
        assert "jpn" in live.tesseract_langs_for_script("")

    def test_the_installer_asks_for_the_japanese_data(self):
        install = (Path(__file__).resolve().parent.parent / "install.sh").read_text(
            encoding="utf-8"
        )
        assert "tesseract-data-jpn" in install


class TestNoFullScreenSecondEngine:
    def test_meiki_is_never_run_on_the_whole_frame(self):
        """Only on crops of doubtful blocks - that is the whole cost argument."""
        source = inspect.getsource(live.refine_japanese_blocks)
        assert "_crop_box(region_img, box, pad=4)" in source
        assert "meiki_ocr_lines(crop)" in source
        assert "meiki_ocr_lines(region_img)" not in source

    def test_the_only_full_frame_read_is_the_multilingual_one(self):
        """One pass reads everything; the second engine reads elements."""
        source = inspect.getsource(live.ocr_image)
        for read in ("meiki_ocr_lines(region_img)", "pytesseract.image_to_string(region_img"):
            assert read not in source, read


class TestStructure:
    def test_the_module_still_parses(self):
        """Cheap, and it catches a half-finished edit immediately."""
        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
