"""Every block has its own source language.

A frame is EN, JP, EN, JP and mixed at once. The one thing that was wrong for
years was a single boolean for the whole batch: it was either wrong for half the
blocks or a guess dressed as a fact. These tests pin the per-block contract and
the cache key that has to follow from it.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources, owner, patch_all

from kizurium_translator import live  # noqa: E402
from kizurium_translator.translate import cache_key  # noqa: E402


@pytest.fixture
def offline(monkeypatch):
    """No network and no disk cache: what matters here is the grouping."""
    # Присваивание в `live` меняло бы копию из реэкспорта: ранний выход по
    # флагу читает `translate_many` в своём модуле, и без адресной подмены он
    # не срабатывает, а тест падает на `None`-переводчике ниже.
    # Флаг и переводчик лежат в объекте состояния перевода: подменяются поля
    # экземпляра, а не модульные имена. Модульных имён у них больше нет, и
    # раньше каждый слой держал свою копию - `main` писал в модуль сессии,
    # а `translate_many` читал модуль перевода, и выключение не действовало.
    from kizurium_translator.live import TRANSLATION

    monkeypatch.setattr(TRANSLATION, "disabled", True)
    monkeypatch.setattr(TRANSLATION, "translator", lambda: None)
    return {}


def _item(text: str, source: str = "") -> dict:
    return {"text": text, "source": source}


class TestTheBatchBooleanIsGone:
    def test_the_signature_has_no_batch_language(self):
        params = inspect.signature(live.translate_many).parameters
        assert "is_jpn" not in params, list(params)

    def test_the_word_is_not_in_the_body_either(self):
        """Mentioned in the prose is fine; used as an argument is not."""
        src = inspect.getsource(live.translate_many)
        body = src.split('"""')[0] + src.split('"""')[-1]
        assert "is_jpn" not in body

    def test_the_call_site_sends_no_batch_language(self):
        src = inspect.getsource(live.worker)
        assert "translate_many(" in src
        for line in src.split("\n"):
            if "translate_many(" in line:
                assert "is_jpn" not in line, line


class TestPerBlockSource:
    def test_each_block_keeps_its_own_source(self, offline):
        items = [_item("Hello"), _item("こんにちは", "ja"), _item("Goodbye")]
        got = live.translate_many(items, offline)
        assert got == ["Hello", "こんにちは", "Goodbye"]

    def test_a_bare_string_reads_its_own_language(self, offline):
        """No boolean to fall back on, so the text decides - per block."""
        got = live.translate_many(["こんにちは", "Hello", "안녕"], offline)
        assert got == ["こんにちは", "Hello", "안녕"]

    def test_a_bare_string_is_not_assumed_english(self):
        """A default of "en" is how a Japanese block gets sent as English."""
        src = inspect.getsource(live.translate_many)
        assert '("ja" if is_jpn else "en")' not in src
        assert "block_lang(text)" in src

    def test_the_ocr_language_wins_over_a_second_guess(self):
        block = {"text": "強化", "lang": "ja"}
        assert live.block_source_lang(block, "強化") == "ja"

    def test_a_block_that_was_never_annotated_still_travels(self):
        assert live.block_source_lang({"text": "こんにちは"}, "こんにちは") == "ja"
        assert live.block_source_lang({}, "Hello") == "en"

    def test_the_part_language_follows_the_block_not_the_frame(self):
        src = inspect.getsource(live.worker)
        assert "block_source_lang(par," in src
        assert "source_lang_for(block_script(" not in src


class TestDuplicateBlocks:
    def test_two_blocks_with_the_same_text_both_get_a_result(self, offline):
        """list.index returns the first match, which mis-attributed the second."""
        got = live.translate_many([_item("Settings"), _item("Settings")], offline)
        assert got == ["Settings", "Settings"]

    def test_the_result_length_always_matches_the_input(self, offline):
        items = [_item("A"), _item("B"), _item("A"), _item("C"), _item("B")]
        assert len(live.translate_many(items, offline)) == len(items)

    def test_an_empty_batch_is_empty(self, offline):
        assert live.translate_many([], offline) == []

    def test_blank_items_do_not_shift_the_answers(self, offline):
        got = live.translate_many([_item(""), _item("Live"), _item("")], offline)
        assert got == ["", "Live", ""]


class TestCacheKey:
    def test_the_key_is_source_target_and_text(self):
        assert cache_key("こんにちは", "ja", "ru") == "ja\x1fru\x1fこんにちは"

    def test_the_same_text_in_two_languages_is_two_entries(self):
        """One text, two readings: sharing a key is how a language is lost."""
        assert cache_key("Live", "en", "ru") != cache_key("Live", "ja", "ru")

    def test_the_same_text_in_two_targets_is_two_entries(self):
        assert cache_key("Live", "en", "ru") != cache_key("Live", "en", "en")

    def test_surrounding_whitespace_is_not_a_different_string(self):
        assert cache_key("  Live  ", "en", "ru") == cache_key("Live", "en", "ru")

    def test_the_batch_looks_the_cache_up_per_language(self, offline):
        src = inspect.getsource(live.translate_many)
        assert "tr.cache_lookup(key, src)" in src
        assert "tr.cache_store(key, manual, src)" in src


class TestGroupingIsOnlyTransport:
    def test_one_http_call_carries_one_language(self):
        """Batching survives, but not as a way of deciding the language."""
        src = inspect.getsource(live.translate_many)
        assert "groups.setdefault(src, []).append(key)" in src
        # a grouping map that is built and never read is not grouping
        assert "by_src" not in src

    def test_a_cap_still_defers_the_tail(self):
        src = inspect.getsource(live.translate_many)
        assert "hard_cap" in src
        assert "translate-cap" in src


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
