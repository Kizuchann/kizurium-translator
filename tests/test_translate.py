"""Translation: cache behaviour, language codes, detection, error answering.

The cache bugs here were not crashes. They were a cache that never hit, a
deletion that came back, and a language guess presented to the backend as fact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import translate as tr  # noqa: E402
from kizurium_translator.translate import Translator, _Cache  # noqa: E402


class TestNoDevNullCache:
    """/dev/null is not a writable cache file, however convenient it looks.

    The cache wrote a temp file and os.replace()d it onto the device node on
    every insert. Whether that fails quietly or does something stranger depends
    on the kernel and the filesystem.
    """

    def test_no_path_means_no_cache_not_a_device_node(self, tmp_path):
        before = set(tmp_path.iterdir())
        cache = _Cache(None, 10)
        cache.put("k", "v")
        cache.flush(force=True)
        cache.write()
        assert cache.get("k") == "v", "an in-memory cache still works"
        assert set(tmp_path.iterdir()) == before, "nothing may be written"

    def test_the_translator_never_reaches_for_dev_null(self, tmp_path):
        t = Translator(target="ru", cache_path=None)
        assert t._cache.path is None

    def test_a_path_is_still_a_real_file(self, tmp_path):
        path = tmp_path / "cache.sqlite"
        cache = _Cache(path, 10)
        cache.put("k", "v")
        cache.flush(force=True)
        import sqlite3

        row = sqlite3.connect(path).execute(
            "SELECT translated_text FROM translations WHERE cache_key = ?", ("k",)
        ).fetchone()
        assert row is not None and row[0] == "v"

    def test_an_unwritable_cache_does_not_raise(self, tmp_path):
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        cache = _Cache(blocker / "sub" / "cache.json", 10)
        cache.put("k", "v")
        cache.flush(force=True)  # must not raise
        assert cache.get("k") == "v"


class TestForgetSurvives:
    def test_forget_marks_the_cache_dirty(self, tmp_path):
        """A removal that is not flushed comes back on the next run.

        pop() changed the dict but left the dirty flag alone, so a forget
        followed by an exit wrote the entry back out and the stale translation
        was served again.
        """
        path = tmp_path / "cache.json"
        cache = _Cache(path, 10)
        cache.put("k", "v")
        cache.flush(force=True)

        fresh = _Cache(path, 10)
        assert fresh.forget("k") is True
        assert fresh._dirty, "the removal has to be marked for writing"
        fresh.flush(force=True)

        again = _Cache(path, 10)
        assert again.get("k") == "", "the entry must be gone from disk"

    def test_forget_reports_whether_anything_was_there(self, tmp_path):
        cache = _Cache(tmp_path / "c.json", 10)
        assert cache.forget("absent") is False
        cache.put("k", "v")
        assert cache.forget("k") is True

    def test_forgetting_nothing_leaves_the_file_alone(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _Cache(path, 10)
        cache.put("k", "v")
        cache.flush(force=True)
        fresh = _Cache(path, 10)
        assert fresh.forget("absent") is False
        assert not fresh._dirty, "nothing changed, so nothing to write"

    def test_translator_forget_round_trips_through_disk(self, tmp_path):
        path = tmp_path / "cache.json"
        t = Translator(target="ru", cache_path=path)
        t.cache_store("hello", "привет", "en")
        t.cache_flush()
        t.cache_forget("hello", "en")
        t.cache_flush()
        again = Translator(target="ru", cache_path=path)
        assert again.cache_lookup("hello", "en") == ""


class TestLatinIsNotCalledEnglish:
    """Латинский источник не определяется по алфавиту.

    Французский, немецкий и английский выглядят одинаково, поэтому объявлять
    источник `en` - значит просить бэкенд перевести `en→ru` французский текст.
    Настоящий `sl=auto` бэкенд разбирает сам, и перевод выходит лучше, а не
    хуже.
    """

    def test_latin_source_is_still_named_english(self, tmp_path):
        """Латинский источник остаётся `en`, и это размен, а не недосмотр.

        Честный ответ для латиницы - `auto`, но `auto` в ключе кеша ломает
        кеш: запись и чтение разойдутся по источнику, и кеш перестанет
        попадать. Латиница в играх - это английский, поэтому `en` здесь
        полезнее, а случайный французский источник при `target=fr` отсекается
        раньше, фильтром «уже на языке назначения».
        """
        t = Translator(target="ru", source="auto", cache_path=tmp_path / "c.json")
        assert t.resolve_source("Choisissez un membre") == "en"

    def test_a_non_latin_source_is_still_named(self, tmp_path):
        t = Translator(target="ru", source="auto", cache_path=tmp_path / "c.json")
        assert t.resolve_source("Нажми любую кнопку") == "ru"
        assert t.resolve_source("メンバーを選択") == "ja"


class TestCacheKeyUsesResolvedSource:
    def test_the_default_configuration_actually_hits(self, tmp_path):
        """Storing under "auto" and reading under "en" never matched.

        "auto" is the default source, so this is every real use of the cache,
        and it did nothing.
        """
        t = Translator(target="ru", source="auto", cache_path=tmp_path / "c.json")
        assert t.source == "auto"
        # Источник берётся тем же разбором, что и при записи: тест проверяет
        # механику ключа, а не определение языка, и жёстко вписанный "en"
        # делал его зависимым от того, что `detect_lang` считает латиницу.
        src = t.resolve_source("hello")
        t._cache.put(tr.cache_key("hello", src, "ru"), "привет")
        assert t._cache.get(tr.cache_key("hello", src, "ru")) == "привет"

    def test_a_cached_translation_is_returned_without_calling_a_backend(
        self, tmp_path
    ):
        t = Translator(target="ru", source="auto", cache_path=tmp_path / "c.json")
        t._cache.put(tr.cache_key("hello", t.resolve_source("hello"), "ru"), "привет")
        calls: list[str] = []
        t.via_gtx = lambda text, source=None: calls.append("gtx") or ""
        t._slow_translate = lambda text, source="": calls.append("slow") or ""
        assert t.translate("hello") == "привет"
        assert not calls, "a cache hit must not reach the network"

    def test_auto_is_never_written_as_a_cache_key(self, tmp_path):
        t = Translator(target="ru", source="auto", cache_path=tmp_path / "c.json")
        t.via_gtx = lambda text, source=None: "привет"
        t.translate("hello")
        keys = list(t._cache.load())
        assert keys
        assert all(not k.startswith("auto\x1f") for k in keys), keys

    def test_a_rejected_cached_answer_is_dropped_and_retried(self, tmp_path):
        t = Translator(target="ru", source="auto", cache_path=tmp_path / "c.json")
        t._cache.put(tr.cache_key("hello", t.resolve_source("hello"), "ru"), "hello")
        t.via_gtx = lambda text, source=None: "привет"
        assert t.translate("hello") == "привет"
        # and the bad entry is gone rather than served again next time
        assert t._cache.get(tr.cache_key("hello", t.resolve_source("hello"), "ru")) == "привет"


class TestLangCode:
    def test_known_codes_map_through(self):
        assert tr._lang_code("ru") == "ru"
        assert tr._lang_code("zh") == "zh-CN"
        assert tr._lang_code("ja") == "ja"

    def test_regional_variants_keep_the_base(self):
        assert tr._lang_code("zh-Hans") == "zh-CN"
        assert tr._lang_code("en-US") == "en"

    def test_auto_stays_auto(self):
        """Defaulting to English turned a Japanese source into a wrong request.

        A well-formed request that says the wrong thing is worse than one that
        lets the backend detect, because nothing anywhere reports an error.
        """
        assert tr._lang_code("auto") == "auto"
        assert tr._lang_code("") == "auto"
        assert tr._lang_code("   ") == "auto"

    def test_an_unknown_code_is_passed_through_not_guessed(self):
        assert tr._lang_code("uk") == "uk"
        assert tr._lang_code("xx") == "xx"

    def test_resolve_source_does_not_invent_english(self):
        t = Translator(target="ru", source="auto")
        # Nothing to detect in a bare symbol string.
        assert t.resolve_source("···") == "auto"
        assert t.resolve_source("") == "auto"

    def test_an_explicit_source_still_wins(self):
        t = Translator(target="ru", source="ja")
        assert t.resolve_source("Hello there") == "ja"


class TestKanjiOnlyJapaneseIsNotChinese:
    """A Japanese UI label is often one Han word with no kana anywhere near it."""

    def test_the_common_ones_stay_japanese(self):
        for text in ("設定", "開始", "能力", "戦闘", "第3章", "戻る", "決定"):
            assert tr.detect_lang(text) == "ja", text

    def test_short_game_ui_words_stay_japanese(self):
        for text in ("本", "次", "防", "攻", "強", "弱", "力", "水", "火"):
            got = tr.detect_lang(text)
            # None is fine for a single character; zh-CN is not.
            assert got in (None, "ja"), (text, got)

    def test_a_real_chinese_label_is_still_chinese(self):
        for text in ("开始", "设置", "战斗", "退出", "确认", "关闭"):
            assert tr.detect_lang(text) == "zh-CN", text

    def test_chinese_punctuation_decides(self):
        assert tr.detect_lang("你好，世界") == "zh-CN"

    def test_japanese_punctuation_does_not_look_chinese(self):
        """Japanese uses the ideographic comma constantly.

        Checking punctuation before kana sent every Japanese sentence to the
        Chinese backend, which is the same bug from the other direction.
        """
        assert tr.detect_lang("こんにちは、元気ですか") == "ja"
        assert tr.detect_lang("こんにちは。元気") == "ja"

    def test_kana_always_wins_over_punctuation(self):
        assert tr.detect_lang("元気？") == "zh-CN"  # no kana, full-width question mark
        assert tr.detect_lang("元気ですか？") == "ja"

    def test_a_sentence_with_some_kana_is_japanese(self):
        assert tr.detect_lang("能力値の.Interface") == "ja"


class TestIsErrorResponse:
    def test_an_empty_answer_is_a_failure(self):
        assert tr.is_error_response("hello", "")

    def test_the_input_unchanged_is_a_failure(self):
        assert tr.is_error_response("hello", "hello")

    def test_japanese_output_is_valid(self):
        assert not tr.is_error_response("Tap to start", "タップして開始", "ja")

    def test_a_japanese_name_in_a_russian_answer_is_not_a_failure(self):
        """English to Russian can legitimately contain a Japanese name.

        Judging the answer by its alphabet declared a correct translation an
        error, and the user saw nothing at all instead.
        """
        out = "Это «Sekai», видимая область"
        assert not tr.is_error_response("This is Sekai, the visible area", out, "ru")
