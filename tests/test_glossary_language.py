"""Глоссарий отвечает на языке назначения, а не всегда по-русски.

Что было сломано.

`TAP!` при переводе на английский давал `Нажми!`. Причина: `glossary_translation`
возвращал значение из словаря, где все 271 запись на русском, и делал это
без оглядки на `target_lang`. При `target=ru` это правильно, при любом
другом - мусор на экране, и выглядит это как «перевод не работает», хотя
работает всё остальное.

Второе, менее очевидное: числа. Движок превращает `12/24` в дату, а `Lv. 12`
в имя. Чтобы этого не было, глоссарий подставляет слово сам - и подставляет
русское. Форма защиты языконезависима, слово внутри - нет.

Проверки ниже фиксируют разделение: форма общая, слово по языку.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402
from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.translation import service  # noqa: E402


@pytest.fixture
def as_english():
    live.configure(Config(target_lang="en"))
    yield
    live.configure(Config())


class TestTheGlossaryDoesNotAnswerInRussianWhenAskedInEnglish:
    def test_a_japanese_button_is_translated_not_russified(self, as_english):
        """Именно это было видно на экране: `TAP!` -> `Нажми!`."""
        got = service.glossary_translation("TAP!", target_lang="en")
        assert got != "Нажми!"
        assert got is None or not _is_cyrillic(got)

    def test_russian_glossary_still_works_for_russian(self):
        assert service.glossary_translation("TAP!") == "Нажми!"
        assert service.glossary_translation("ダウンロード") == "Скачать"

    @pytest.mark.parametrize("target", ["en", "de", "fr"])
    def test_no_cyrillic_leaks_into_another_language(self, target):
        for text in ("TAP!", "キャンセル", "ダウンロード", "ランク12", "Lv. 12",
                     "Stamina 30/30", "Tutorial 1-2"):
            got = service.glossary_translation(text, target_lang=target)
            assert not _is_cyrillic(got), f"{text!r} -> {got!r} при target={target}"


class TestTheNumericGuardsAreLanguageNeutral:
    """Форма защиты общая, слово - нет.

    `12/24` движок читает как дату, поэтому число отдаётся назад как есть.
    Это должно работать при любом языке: форма не переводится.
    """

    @pytest.mark.parametrize("target", ["ru", "en", "de"])
    def test_a_fraction_never_becomes_a_date(self, target):
        """Слово берётся из словаря, число остаётся числом.

        `Cleared 12/24` - ровно тот случай, из-за которого форма и существует:
        движок читал `12/24` как дату и возвращал «Очищено 24 декабря».
        Дробь обязана уйти в неизменном виде при любом языке.
        """
        got = service.glossary_translation("Cleared 12/24", target_lang=target)
        assert got is not None and got.endswith("12/24"), got

    @pytest.mark.parametrize("target", ["ru", "en", "de"])
    def test_a_percentage_survives_intact(self, target):
        got = service.glossary_translation("50%", target_lang=target)
        assert got is not None and "50" in got

    @pytest.mark.parametrize("target, word", [
        ("ru", "Ур."), ("en", "Lv."), ("de", "Lv."),
    ])
    def test_the_level_word_follows_the_language(self, target, word):
        got = service.glossary_translation("Lv. 12", target_lang=target)
        assert got is not None and got.startswith(word), got


class TestAddingALanguageIsDataNotCode:
    """Требование «чтобы в будущем легко добавить язык» проверяется здесь.

    Язык добавляется файлом в `data/glossary/` и строкой в одной таблице.
    Никаких новых веток в коде.
    """

    def test_the_glossary_lives_in_data_files(self):
        data = (pathlib.Path(__file__).resolve().parent.parent
                / "src" / "kizurium_translator" / "data" / "glossary")
        assert data.is_dir(), "глоссарий должен лежать в data/glossary, а не в коде"
        assert (data / "ru.toml").is_file()

    def test_every_data_file_matches_the_table(self):
        """Файл без строки в таблице - это молчаливо неработающий язык."""
        from kizurium_translator.translation import glossary

        data = glossary.DATA_DIR
        for path in sorted(data.glob("*.toml")):
            code = path.stem
            assert code in glossary.GLOSSARY_FILES, (
                f"{path.name} есть, а код его не знает: язык не загрузится"
            )

    def test_an_unknown_language_has_no_glossary_rather_than_the_russian_one(self):
        from kizurium_translator.translation import glossary

        assert glossary.entries_for("xx-YY") == {}

    def test_a_broken_data_file_is_not_fatal(self):
        """Плохой словарь - это отсутствие словаря, а не падение перевода."""
        from kizurium_translator.translation import glossary

        assert isinstance(glossary.load_file("broken-test-lang"), dict)


def _is_cyrillic(text: str) -> bool:
    return any("Ѐ" <= c <= "ӿ" for c in text or "")
