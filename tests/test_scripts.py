"""Язык назначения задаётся данными, а не кириллицей в коде.

Проблема, которую закрывает этот файл.

Проверка «эта строка уже на языке назначения, не переводить её» была написана
четыре раза независимо, и все четыре раза через долю кириллицы: в
`skip_source`, `is_mostly_russian`, `drop_russian_columns` и в счётчике кадра.
Ни одно из этих мест не читало `Config.target_lang`. Проект умел переводить
только на русский, и менять язык в конфиге было бессмысленно - фильтры
продолжали считать русский «своим», а английский «чужим».

Проверки ниже - контракт, а не описание текущего кода. Часть из них падает на
нынешнем состоянии и обязана стать зелёной.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.core import scripts  # noqa: E402


class TestTheTableIsTheSingleSource:
    @pytest.mark.parametrize("lang, script", [
        ("ru", "cyrl"), ("uk", "cyrl"),
        ("en", "latn"), ("fr", "latn"), ("de", "latn"), ("es", "latn"),
        ("pt", "latn"), ("pl", "latn"),
        ("ja", "jpan"), ("zh", "hans"), ("zh-CN", "hans"),
        ("zh-TW", "hant"), ("ko", "hang"),
    ])
    def test_a_language_knows_its_script(self, lang, script):
        assert scripts.script_of(lang) == script

    def test_an_unknown_language_does_not_lie(self):
        """Неизвестный код не должен молча считаться латиницей.

        Иначе «перевести на язык, которого нет в таблице» превратится в
        «перевести на английский»: строка на этом языке будет считаться
        латинской и уйдёт в перевод без надобности.
        """
        assert scripts.script_of("xx-YY") is None
        assert scripts.is_known("xx-YY") is False

    def test_every_script_has_a_pattern(self):
        for script in scripts.ALL_SCRIPTS:
            assert scripts.pattern_of(script).pattern

    def test_a_script_declared_in_the_table_has_a_pattern(self):
        """Таблица и набор письменностей не должны разъезжаться."""
        for lang, script in scripts.SCRIPT_OF_LANG.items():
            assert script in scripts.ALL_SCRIPTS, f"{lang} -> {script}"


class TestAlreadyInTheTargetScript:
    """Ровно то, что просил пользователь: игнорировать то, что уже на языке
    назначения, и переводить всё остальное."""

    def test_russian_text_is_already_target_when_target_is_russian(self):
        assert scripts.is_in_target_script("Нажми любую кнопку", "ru")

    def test_russian_text_is_not_target_when_target_is_english(self):
        # То, ради чего всё затевалось: при target=en русский должен уходить в
        # перевод, а не отбрасываться как «уже на своём языке».
        assert not scripts.is_in_target_script("Нажми любую кнопку", "en")

    def test_english_text_is_already_target_when_target_is_english(self):
        assert scripts.is_in_target_script("Press any button", "en")

    def test_english_text_is_not_target_when_target_is_russian(self):
        assert not scripts.is_in_target_script("Press any button", "ru")

    @pytest.mark.parametrize("lang, text", [
        ("ja", "メンバーを選択してください"),
        ("ko", "멤버를 선택하세요"),
        ("zh", "请选择成员"),
        ("fr", "Choisissez un membre"),
        ("de", "Wähle ein Mitglied"),
    ])
    def test_other_languages_know_their_own_script(self, lang, text):
        assert scripts.is_in_target_script(text, lang)

    def test_japanese_text_is_kana_and_not_kanji_only(self):
        # Только иероглифы - это в первую очередь китайский, но в игре
        # японский без канны встречается. Иероглиф не должен считаться
        # японским, иначе весь японский интерфейс молча уйдёт в никуда.
        assert not scripts.is_in_target_script("選択", "ja")
        assert scripts.is_in_target_script("選択してください", "ja")

    def test_a_few_letters_are_not_a_language(self):
        """Одно-два слога - это одиночная буква UI, а не строка на своём языке."""
        assert not scripts.is_in_target_script("OK", "en")
        assert not scripts.is_in_target_script("OK", "ru")

    def test_a_single_line_of_a_target_language_in_a_foreign_frame(self):
        """«LV» среди японского - не русская строка, хоть буквы и кириллические."""
        assert not scripts.is_in_target_script("LV", "ru")

    def test_a_mixed_line_counts_as_the_target_language(self):
        """Порог намеренно низкий, и это не совпадение.

        `Level 27 — уровень` на 58% кириллицы при `target=ru` считается
        «уже на своём языке» и не переводится. Так и было задумано: любой
        заметный кусок своего языка оставляем как есть, потому что бэкенд,
        получив `ru→ru`, калечит текст. Плата - строка на двух языках не
        переводится целиком.

        Поднимать порог нельзя без проверки на экране: тогда в сеть пойдут
        запросы `ru→ru`, и русский текст начнёт портиться там, где сейчас
        остаётся нетронутым.
        """
        assert scripts.is_in_target_script("Level 27 — уровень", "ru")

    def test_a_mixed_line_matches_both_languages_at_once(self):
        """Следствие низкого порога, а не ошибка.

        Строка «уровень Level 27» на 58% кириллицы и 42% латиницы
        одновременно проходит и за русскую, и за английскую. На практике
        это значит ровно то, что написано в предыдущем тесте: двуязычная
        строка не переводится ни в одну сторону. Экран игры monolingual,
        и этот случай редок; менять поведение - значит менять русский
        перевод, который сейчас работает.
        """
        assert scripts.is_in_target_script("уровень Level 27", "ru")
        assert scripts.is_in_target_script("уровень Level 27", "en")

    def test_an_unknown_target_cannot_decide(self):
        """Нет языка - нет решения. Лучше перевести лишний раз, чем выкинуть."""
        assert not scripts.is_in_target_script("Нажми", "xx-YY")


class TestDominantScript:
    def test_a_japanese_line_is_japanese(self):
        assert scripts.dominant_script("メンバーを選択してください") == "jpan"

    def test_a_chinese_line_is_chinese(self):
        assert scripts.dominant_script("请选择成员") == "hans"

    def test_a_russian_line_is_cyrillic(self):
        assert scripts.dominant_script("Нажми любую кнопку") == "cyrl"

    def test_digits_and_punctuation_are_not_a_script(self):
        assert scripts.dominant_script("100% 27 - 3.5") is None

    def test_an_empty_line_has_no_script(self):
        assert scripts.dominant_script("") is None
        assert scripts.dominant_script("   \n\t ") is None


class TestSourceLanguageFromScript:
    """Обратная таблица: по письменности назвать исходный язык.

    Она нужна `detect_lang`, который сейчас возвращает жёсткое `"ru"` при
    доле кириллицы больше трети - то есть любой русский текст уезжает в сеть
    как `ru→ru`, а любой другой язык не опознаётся вовсе.
    """

    def test_cyrillic_gives_a_cyrillic_language(self):
        assert scripts.code_for_script("cyrl") in {"ru", "uk", "bg", "sr"}

    def test_kana_gives_japanese(self):
        assert scripts.code_for_script("jpan") == "ja"

    def test_hangul_gives_korean(self):
        assert scripts.code_for_script("hang") == "ko"

    def test_han_gives_chinese(self):
        assert scripts.code_for_script("hans") in {"zh", "zh-CN"}

    def test_latin_has_several_possible_languages(self):
        """Латинца неоднозначна: по ней язык не определить.

        Возвращать `en` по умолчанию - значит французский текст уйдёт в
        перевод как `en→ru`, и бэкенд переведёт, но исходный язык будет назван
        неверно, а кэш по ключу `en` станет общим для разных языков.
        """
        assert scripts.code_for_script("latn") is None
