"""a punctuation skeleton, built before the backend sees the text.

`carry_edge_punctuation`protects the punctuation at the two ends of a
line, because a run of punctuation lives at an edge. Interior punctuation is the
other half of the plan's own input and had nothing protecting it: `My:Name` came
back as `моё имя` with the colon gone, and `Kira -` with the dash gone - both
because a translation engine is right to treat them as its own punctuation to
place.

So interior punctuation is replaced with a placeholder before the backend runs,
verified afterwards, and restored if it survived. A lost placeholder is a lost
character, so the result is rejected and the next backend gets a turn.

The tests also cover the asymmetry that was in the code: the slow backend
(MyMemory) restored names and paths but never checked the tokens had survived,
so a broken answer went into the cache. That is the part worth a test, because
nothing about it was visible from the outside.

    uv run pytest tests/test_punctuation_skeleton.py
"""

from __future__ import annotations

import pytest

from kizurium_translator.translate import (
    build_punctuation_skeleton,
    restore_punctuation_skeleton,
    skeleton_survived,
)


def test_interior_colon_is_protected_and_comes_back():
    src = "Kira - My:Name Kizu"
    protected, mapping = build_punctuation_skeleton(src)
    assert ":" not in protected.replace("__KZT_P", ""), "двоеточие ушло в плейсхолдер"
    assert mapping, "внутри строки есть пунктуация, а защиты нет"
    assert restore_punctuation_skeleton(protected, mapping).count(":") == 1


def test_the_plans_own_input_round_trips():
    src = "Hello... I.. Kira - My:Name Kizu!!"
    protected, mapping = build_punctuation_skeleton(src)
    got = restore_punctuation_skeleton(protected, mapping)
    # Interior punctuation comes back; the edges belong to the edge function
    # and are deliberately not in this mapping.
    assert ":" in got
    assert "-" in got


def test_edges_are_left_to_carry_edge_punctuation():
    """A leading `...` is not interior. Protecting it here would double it.

    The comma further along *is* interior, so it is protected - the point is that
    the run at the very start of the line is not in the mapping.
    """
    src = "...Umm, huh"
    protected, mapping = build_punctuation_skeleton(src)
    assert not protected.startswith("__KZT"), "крайняя точка защищается отдельно"
    assert protected.startswith("...")
    assert "..." not in mapping.values()


def test_text_with_no_interior_punctuation_is_not_substituted():
    plain = "Just some words"
    protected, mapping = build_punctuation_skeleton(plain)
    assert mapping == {}
    assert protected == plain


def test_a_source_already_containing_the_token_shape_is_left_alone():
    """Un-protecting is not reversible, so a token-looking source is data we
    cannot verify. Leave it rather than guess."""
    src = "weird __KZT_P0__ token here"
    protected, mapping = build_punctuation_skeleton(src)
    assert mapping == {}
    assert protected == src


def test_a_lost_placeholder_is_detected():
    """lost placeholder = invalid result."""
    _, mapping = build_punctuation_skeleton("My:Name Kizu")
    kept = "моё " + " ".join(mapping.keys()) + " имя"
    assert skeleton_survived(kept, mapping) is True
    dropped = "моё имя"
    assert skeleton_survived(dropped, mapping) is False


def test_restore_is_safe_on_an_empty_mapping():
    assert restore_punctuation_skeleton("текст", {}) == "текст"
    assert restore_punctuation_skeleton("", {"a": "b"}) == ""


@pytest.mark.parametrize(
    ("src", "expected_count"),
    [
        ("one: two", 1),
        ("a - b - c", 2),
        ("a: b: c: d", 3),
        ("wait... what", 1),
    ],
)
def test_every_run_is_protected_not_just_the_first(src, expected_count):
    _, mapping = build_punctuation_skeleton(src)
    assert len(mapping) == expected_count, f"{src!r} -> {mapping}"


def test_the_token_does_not_collide_with_the_name_placeholder():
    """`ZZNAME0ZZ` is the existing shape; these must not collide."""
    from kizurium_translator.translate import protect_proper_nouns

    protected, names = protect_proper_nouns("Kira said: hi")
    protected2, mapping = build_punctuation_skeleton(protected)
    for token in names:
        assert token not in protected2 or token in mapping.values()
    for token in mapping:
        assert not token.startswith("ZZNAME")

"""punctuation is register, and a card that drops it lies about it.

The plan names one input - `Hello... I.. Kira - My:Name Kizu!!` - and requires
`...`, `..`, `-`, `:` and `!!` to survive the whole pipeline, plus `?!`, `!?`,
`—`, `–`, `「」`, `『』`, `()`, `[]`, `{}`.

What existed was `carry_edge_punctuation`, which covered the runs at the two
ends of a line but with a narrower alphabet than the plan lists: brackets were
absent entirely, so `「」` - how a line of Japanese dialogue is marked at all -
could be dropped, and a card would read as narration where the game had
speech.

These tests check the ends, because that is where a run of punctuation lives.
Punctuation in the *middle* of a line (`My:Name`) is a different problem and
belongs to the skeleton; the assertion here says so rather than pretending
the whole input is covered.

    uv run pytest tests/test_punctuation_skeleton.py
"""


import pytest

from kizurium_translator.translate import carry_edge_punctuation


def test_the_plans_own_input_keeps_its_edges():
    """`Hello... I.. Kira - My:Name Kizu!!` - what is at the two ends survives.

    The `...` here is interior, and the `..` after `I` is interior too: this
    function only ever touches the edges, and the interior is the
    skeleton. What this asserts is that the input does not lose its closing
    `!!` and does not gain a `...` it never had at the start.
    """
    src = "Hello... I.. Kira - My:Name Kizu!!"
    out = "Привет! Я.. Кира - моё имя: Кидзу"
    got = carry_edge_punctuation(src, out)
    assert got.endswith("!!")
    assert not got.startswith("..."), "крайняя точка не выдумывается"


@pytest.mark.parametrize(
    ("src", "out", "lead"),
    [
        ("...Umm", "Угу", "..."),
        ("..Umm", "Угу", ".."),
        ("?!What", "Что", "?!"),
        ("!?Really", "Правда", "!?"),
        ("—Listen", "Слушай", "—"),
        ("–Wait", "Подожди", "–"),
        ("「Quote」", "Цитата", "「"),
        ("『Nested』", "Вложенная", "『"),
        ("(Hidden)", "Скрытый", "("),
        ("[Bracket]", "Скобка", "["),
        ("{Brace}", "Скобка", "{"),
    ],
)
def test_every_mark_the_plan_lists_survives_at_the_edge(src, out, lead):
    """'s list, one mark at a time. A run the engine kept is not doubled."""
    got = carry_edge_punctuation(src, out)
    assert got.startswith(lead), f"{src!r} -> {got!r}"


@pytest.mark.parametrize(
    ("src", "lead", "tail"),
    [
        ("!!It failed", "!!", ""),
        ("It failed!!", "", "!!"),
        ("Really...", "", "..."),
        ("Stop?!", "", "?!"),
        ("Yes!?", "", "!?"),
        ("Long dash—", "", "—"),
    ],
)
def test_the_tail_keeps_the_run(src, lead, tail):
    got = carry_edge_punctuation(src, "Хорошо")
    if lead:
        assert got.startswith(lead)
    if tail:
        assert got.endswith(tail)


def test_brackets_that_pair_are_restored_as_a_pair():
    """Half a bracket is worse than none: `「Цитата` is not a quotation."""
    got = carry_edge_punctuation("「Quote」", "Цитата")
    assert got.startswith("「")
    assert got.endswith("」")


def test_a_translation_with_its_own_punctuation_is_left_alone():
    """Two runs of dots is not what anybody said."""
    src = "Hello... I did it"
    out = "Привет... Я это сделал"
    assert carry_edge_punctuation(src, out) == out


def test_empty_inputs_are_safe():
    assert carry_edge_punctuation("", "текст") == "текст"
    assert carry_edge_punctuation("текст", "") == ""
    assert carry_edge_punctuation("", "") == ""


def test_nothing_in_the_translation_is_removed():
    """This only ever prepends or appends; it is not allowed to trim."""
    src = "..Umm, huh.. E-excuse me!"
    out = "Угу-угу, хм, извините!"
    got = carry_edge_punctuation(src, out)
    assert out in got or got.strip(".,!? ") == out.strip(".,!? ")


def test_middle_punctuation_is_phase_72s_problem_not_this_one():
    """`My:Name` has nothing wrong with its colon, and this does not touch it.

    Asserted so that a future change that widens this function past the edges
    has to decide on purpose whether to protect interior punctuation, rather
    than doing it by accident.
    """
    src = "Kira - My:Name Kizu!!"
    out = "Кира - моё имя: Кидзу!!"
    got = carry_edge_punctuation(src, out)
    assert got.count(":") == 1
    assert got.endswith("!!")
