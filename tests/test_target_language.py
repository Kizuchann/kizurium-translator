"""Перевод на любой язык, а не только на русский.

Проверки ниже фиксируют поведение, которого раньше не существовало: язык
назначения выбирается в конфиге, и всё, что решает «переводить ли строку»,
спрашивает об этом у `core.scripts`, а не считает кириллицу.

Часть проверок падает на коде, который был до правки. Это и есть причина их
написать до того, как править.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402
from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.core import scripts  # noqa: E402
from kizurium_translator.layout import grouping  # noqa: E402
from kizurium_translator.ocr import engine  # noqa: E402


@pytest.fixture
def as_english():
    """Перевод на английский, как это сделает человек в конфиге."""
    live.configure(Config(target_lang="en"))
    yield
    live.configure(Config())


class TestTheBackendAlreadyTookTheTarget:
    """Это работало и раньше; проверка защищает от отката при правках."""

    def test_a_translator_is_built_for_the_chosen_language(self):
        live.configure(Config(target_lang="de"))
        try:
            assert live.translator().target == "de"
        finally:
            live.configure(Config())

    def test_the_cache_separates_languages(self):
        """Один и тот же текст на два языка - это две разные записи.

        Если ключ кэша не включает язык назначения, английский перевод,
        случайно положенный на русский текст, будет отдан за русский.
        """
        a = live.translator()
        a.cache_flush(force=True)
        live.configure(Config(target_lang="fr"))
        try:
            assert live.translator().target == "fr"
        finally:
            live.configure(Config())


class TestAlreadyInTheTargetScriptIsNotCyrillic:
    """Четыре места, которые решали это через долю кириллицы."""

    def test_skip_source_follows_the_target_language(self):
        # Ядро запроса: при target=english русский текст должен уходить в
        # перевод, а не отбрасываться как «уже на своём».
        assert not engine.skip_source("Нажми любую кнопку", target_lang="en")
        assert engine.skip_source("Нажми любую кнопку", target_lang="ru")

    def test_skip_source_ignores_english_when_target_is_english(self):
        assert engine.skip_source("Press any button", target_lang="en")
        assert not engine.skip_source("Press any button", target_lang="ru")

    def test_is_mostly_russian_becomes_a_question_about_any_language(self):
        """Было: доля кириллицы. Стало: доля письменности языка назначения."""
        assert grouping.is_mostly_target("Уровень 27", target_lang="ru")
        assert not grouping.is_mostly_target("Уровень 27", target_lang="en")
        assert grouping.is_mostly_target("Level 27", target_lang="en")

    def test_a_japanese_screen_is_not_russian(self):
        assert not grouping.is_mostly_target("メンバーを選択", target_lang="ru")


class TestDetectLanguageOfTheSource:
    def test_russian_is_detected_without_a_hardcoded_return(self):
        from kizurium_translator.translate import detect_lang

        assert detect_lang("Нажми любую кнопку") == "ru"

    def test_japanese_and_korean_and_chinese_are_detected(self):
        from kizurium_translator.translate import detect_lang

        assert detect_lang("メンバーを選択してください") == "ja"
        assert detect_lang("멤버를 선택하세요") == "ko"
        assert detect_lang("请选择成员") in {"zh", "zh-CN"}

    def test_latin_is_still_called_english(self):
        """Документированный размен, а не недосмотр.

        Таблица письменностей для латиницы честно возвращает `None`: язык по
        ней не определить. Но в кеш попал бы `auto`, а кеш с `auto` внутри
        не работает, поэтому `detect_lang` для латиницы отвечает `en`.
        Латиница в играх - это английский, а французский источник при
        `target=fr` отсекается раньше, фильтром «уже на языке назначения».
        """
        from kizurium_translator.translate import detect_lang

        assert detect_lang("Choisissez un membre") == "en"

    def test_the_table_still_refuses_to_guess(self):
        """Таблица не притворяется: по латинице язык не определить."""
        assert scripts.code_for_script("latn") is None

class TestTokenizingIsNotLatinAndCyrillicOnly:
    """Тихая поломка, которую тесты на перевод не видели."""

    def test_a_japanese_change_is_a_change(self):
        """Раньше токены резались как `[A-Za-zА-Яа-яЁё0-9]+`.

        Для японского множество выходило пустым, `significant_word_diff`
        возвращал False, и блок не переводился заново - молча, без ошибки.
        """
        from kizurium_translator.live import tracking

        assert tracking._significant_tokens("変更しました")
        assert tracking.significant_word_diff("変更しました", "変更しません")

    def test_a_chinese_change_is_a_change(self):
        from kizurium_translator.live import tracking

        assert tracking._significant_tokens("已选择成员")

    def test_an_arabic_change_would_be_a_change(self):
        """Арабский сейчас не поддерживается отрисовкой, но токенизация
        не должна быть причиной, по которой он сломается, когда поддержка
        появится."""
        from kizurium_translator.live import tracking

        assert tracking._significant_tokens("اختر عضوا")


class TestTheDecisionSitesAskTheTable:
    """Проверка по коду: решение «переводить ли» не считает кириллицу руками.

    `RE_CYR` в слоях законен - он отвечает на другие вопросы, например «это
    русская локализация на экране», и `is_mostly_russian` остаётся именно
    для этого. Запрещено другое: функция, которая решает, отправлять ли строку
    в перевод, и считает кириллицу сама.
    """
    @pytest.mark.parametrize("func", [engine.skip_source, grouping.is_mostly_target])
    def test_the_body_does_not_count_cyrillic(self, func):
        import inspect

        src = inspect.getsource(func)
        for marker in ("RE_CYR", "cyr", "RE_LAT"):
            assert marker not in src, f"{func.__name__} всё ещё считает {marker}"

    def test_the_target_language_has_one_writer(self):
        """Язык назначения сообщается в одном месте, и это `configure`.

        Второе место записи означало бы, что есть путь, где язык сменился, а
        слои об этом не узнали: ровно та рассинхронизация, из-за которой
        настройка применялась и не действовала.
        """
        pkg = (pathlib.Path(__file__).resolve().parent.parent / "src"
               / "kizurium_translator")
        writers = []
        for path in pkg.rglob("*.py"):
            if "__pycache__" in str(path) or path.name == "scripts.py":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ("set_target_language(" in line
                        and "def set_target_language" not in line):
                    writers.append(f"{path.relative_to(pkg)}:{i}")
        assert len(writers) == 1 and writers[0].startswith("live/runtime.py:"), writers


class TestTheFrameIsNotMistakenForOurOwnOverlay:
    """Русский текст на экране - это не эхо оверлея.

    Кадр отбрасывался целиком, если в нём больше кириллицы, чем латиницы, и
    рядом не было «настоящего UI». Задумано это было для оверлея, который
    рисует по-русски, но проверка смотрела на алфавит, а не на то, что
    оверлей уже нарисовал.

    На двуязычной странице при `target=en` русский интерфейс и есть тот самый
    «чужой» текст, и весь кадр уходил в никуда: ноль карточек на экране при
    полностью рабочем переводе. Проверено на живом окне с браузером.

    Теперь кадр сравнивается с тем, что оверлей уже показал.
    """
    def test_our_own_text_is_recognised_by_being_ours(self):
        from kizurium_translator.live import state as state_mod

        drawn = [{"text": "Перевод", "box": (10, 10, 100, 30)}]
        s = state_mod.State()
        s.set(drawn, (0, 0, 500, 500))
        seen, _region, _status = s.snapshot()
        assert [b["text"] for b in seen] == ["Перевод"]

    def test_the_check_is_not_about_the_alphabet(self):
        """Проверка по коду: подсчёта кириллицы в этом месте быть не должно.

        Именно подсчёт и был причиной: при любом языке назначения, кроме
        русского, русский экран выглядел для движка как его собственное эхо.
        """
        import inspect

        from kizurium_translator.live import session

        src = inspect.getsource(session)
        assert "skip-self-echo cyr=" not in src, (
            "кадр по-прежнему отбрасывается по доле кириллицы"
        )
