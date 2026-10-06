"""Заглавными целиком - это оформление, а не имя.

По-английски имена не пишут заглавными, и `NEW ILLUSTRATION IS NOW UNLOCKED!!`
это заголовок, а не имя. Регулярка, собирающая имя из любого слова с заглавной
буквы, уводила `IS NOW UNLOCKED` в плейсхолдеры целиком: к движку уходило
`【0】 【1】 【2】`, возвращалось то же самое, и строка оставалась по-английски.
"""

from __future__ import annotations

from kizurium_translator.translate import (
    _all_caps_is_style,
    _stat_or_plain,
    protect_proper_nouns,
)


def test_all_caps_heading_is_not_a_name():
    out, mapping = protect_proper_nouns("NEW ILLUSTRATION IS NOW UNLOCKED!!")
    assert out == "NEW ILLUSTRATION IS NOW UNLOCKED!!"
    assert mapping == {}


def test_short_all_caps_words_are_style_too():
    """Длины нет намеренно: IS и NEW короче любого порога."""
    assert _all_caps_is_style("IS")
    assert _all_caps_is_style("NEW")
    assert _all_caps_is_style("UNLOCKED")


def test_abbreviations_are_caught_before_the_style_check():
    """HP, BGM, EXP - это то, как игра называет предмет, и ловит их _stat_or_plain.

    Проверяется в связке, потому что именно так это и работает: функция стиля
    про HP ничего не знает и обязана молчать, её до этого просто не доводят.
    """
    assert _stat_or_plain("HP")
    assert _stat_or_plain("BGM")
    assert _stat_or_plain("EXP")
    assert not protect_proper_nouns("Get your HP up")[1]


def test_a_name_at_the_start_of_a_sentence_is_left_to_the_engine():
    """Правило существовало до этой правки и не менялось ею.

    Заглавное слово в начале фразы - обычный регистр предложения, а не
    свидетельство имени; движок сам разберётся. Защита ловит имена, которые
    стоят в середине.
    """
    from kizurium_translator.translate import enable_title_glossary

    # Title packs list ``Rin``; with them on, sentence-start still protects.
    enable_title_glossary(False)
    assert protect_proper_nouns("Kagamine Rin earned it")[1] == {}
    assert protect_proper_nouns("You met Kagamine Rin today")[1] != {}


def test_a_token_that_is_not_a_word_is_not_style():
    """`100%` и `2P` - это число со знаком и номер игрока."""
    assert not _all_caps_is_style("100%")
    assert not _all_caps_is_style("2P")


def test_mixed_case_is_never_style():
    assert not _all_caps_is_style("Amiya")
    assert not _all_caps_is_style("Max")
