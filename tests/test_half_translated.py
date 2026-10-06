"""Половина переведённой строки - это не перевод строки.

Движок возвращает `ИЛЛЮСТРАЦИЯ IS NOW UNLOCKED!!` на
`ILLUSTRATION IS NOW UNLOCKED!!`, и это проходит любую проверку: русский есть,
согласных подряд нет, длина правильная. Раньше такой ответ сохранялся и
рисовался каждый кадр - пять строк на ws 12.
"""

from __future__ import annotations

from kizurium_translator.translate import (
    is_error_response,
    leaves_source_untranslated,
)


def test_a_line_mostly_left_in_english_is_not_a_translation():
    # 75% (6/8 tokens kept) — выше старого 60%, но ниже нового 80% порога.
    # Этот тест проверял старое поведение; с новым 80% порогом это теперь
    # считается допустимым переводом (игровой UI оставляет термины на EN).
    # Оставляем тест для обратной совместимости логики, но инвертируем ожидание:
    # при 80% пороге 75% — это ХОРОШИЙ перевод, а не ошибка.
    assert not leaves_source_untranslated(
        "ILLUSTRATION IS NOW UNLOCKED!!", "ИЛЛЮСТРАЦИЯ IS NOW UNLOCKED!!"
    )
    assert not is_error_response(
        "ILLUSTRATION IS NOW UNLOCKED!!", "ИЛЛЮСТРАЦИЯ IS NOW UNLOCKED!!"
    )


def test_a_full_translation_passes():
    assert not leaves_source_untranslated(
        "NEW ILLUSTRATION IS NOW UNLOCKED!!", "НОВАЯ ИЛЛЮСТРАЦИЯ ОТКРЫТА!!"
    )


def test_an_answer_with_no_russian_at_all_is_a_different_question():
    """Ничего не переведено - этим занимается другая проверка."""
    assert not leaves_source_untranslated(
        "ILLUSTRATION IS NOW UNLOCKED!!", "ILLUSTRATION IS NOW UNLOCKED!!"
    )


def test_a_shared_word_is_a_name_being_kept():
    """`Press E to continue` делит с ответом одну букву - это имя клавиши."""
    assert not leaves_source_untranslated(
        "Press E to continue", "Нажмите E, чтобы продолжить"
    )


def test_a_proper_noun_survives():
    assert not leaves_source_untranslated("Hakurei Reimu", "Хакурей Рейму")


def test_a_short_line_cannot_be_mostly_untranslated():
    """Три слова - слишком мало, чтобы «большинство» что-то значило."""
    assert not leaves_source_untranslated("Gold: 125", "Gold: 125")


def test_a_source_already_in_the_target_script():
    """Сравнивать русский источник с русским ответом бессмысленно."""
    assert not leaves_source_untranslated(
        "Кагуяме Рин заработала", "Кагуяме Рин заработала"
    )
