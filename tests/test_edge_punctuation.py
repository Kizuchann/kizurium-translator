"""Punctuation at the edges of a line is register, and it survives translation.

`..Umm, huh.. E-excuse me!` comes back from the engine as
`Угу-угу, хм, извините!` - the hesitation is gone. On ws 8 those dots are the
whole point of the line: they are what the character is doing, and their absence
leaves the card sitting over the dots that carry the meaning.
"""

from __future__ import annotations

from kizurium_translator.translate import carry_edge_punctuation


def test_leading_ellipsis_comes_back():
    assert (
        carry_edge_punctuation("..Umm, huh.. E-excuse me!", "Угу-угу, хм, извините!")
        == "..Угу-угу, хм, извините!"
    )


def test_trailing_punctuation_comes_back():
    assert (
        carry_edge_punctuation("Hello.", "Привет") == "Привет."
    )
    assert (
        carry_edge_punctuation("Sorry to intrude!!", "Простите, что я вмешался")
        == "Простите, что я вмешался!!"
    )


def test_both_ends_at_once():
    assert carry_edge_punctuation("...Wait...", "Подожди") == "...Подожди..."


def test_a_translation_that_already_has_its_own_is_left_alone():
    """Two runs of dots is not what anybody said."""
    assert carry_edge_punctuation("..Umm", "...Угу") == "...Угу"
    assert carry_edge_punctuation("Hello.", "Привет.") == "Привет."


def test_nothing_to_carry_leaves_the_translation_alone():
    assert carry_edge_punctuation("Can I trust you?", "Можно тебе доверять?") == (
        "Можно тебе доверять?"
    )


def test_brackets_are_not_edge_punctuation():
    """A parenthesis opens a clause, it does not lead the line."""
    assert carry_edge_punctuation("(Hidden) Purchase", "(Скрыто) Купить") == (
        "(Скрыто) Купить"
    )


def test_empty_sides_are_safe():
    assert carry_edge_punctuation("", "Привет") == "Привет"
    assert carry_edge_punctuation("Hello", "") == ""
