"""Game text that the glossary and the numeric/name rules have to get right.

These strings were written after the glossary was built, as a held-out set: if
any of them only works because it was tuned against it, the failure shows up
here. Expectations are checked without the network, through the offline
glossary and pattern layer, so the suite stays offline and fast.
"""

import pytest

from kizurium_translator import live
from kizurium_translator.translate import protect_proper_nouns, restore_proper_nouns

# (source, expected Russian) for words and phrases a game screen really shows.
GLOSSARY_CASES = [
    # numbers must survive as numbers
    ("Cleared 12/24", "Пройдено 12/24"),
    ("Stamina 120/120", "Выносливость 120/120"),
    ("98.7%", "98.7%"),
    ("Lv. 12", "Ур. 12"),
    ("Lv. 12 to Lv. 13", "Ур. 12 → ур. 13"),
    ("Story 8-3", "Сюжет 8-3"),
    # terms that free translation gets wrong
    ("Boost", "Усиление"),
    ("Pulling...", "Призыв..."),
    ("Broken", "Разобрать"),
    ("Owned", "Есть"),
    ("Not owned", "Нет"),
    ("Early Bird Bonus", "Бонус раннего пташки"),
    ("Team Edit", "Правка команды"),
    ("Wave incoming", "Приближается волна"),
    ("Chapter cleared", "Глава пройдена"),
    ("Character unlocked", "Персонаж разблокирован"),
    # Japanese
    ("限定", "Эксклюзивный"),
    ("開催中", "Идёт сейчас"),
    ("强化", "Усиление"),
    ("進行度", "Прогресс"),
    ("探索", "Исследование"),
    ("キャンセル", "Отмена"),
]

# Strings the protection must leave alone: capitalisation that is not a name.
NOT_A_NAME = [
    "Press E to talk",
    "Team Edit",
    "Early Bird Bonus",
    "Are you really okay?",
    "Lv. 12 to Lv. 13",
    "HP is full",
    "Stamina 120/120",
    "You have 3 new Currencies",
    "Squad blocked 3 enemies",
    "Best Combo 1487",
]

# Strings where a name has to survive translation.
HAS_NAME = [
    "Rhodes Island is counting on you",
    "Amiya trusted you",
    "My Sekai opens in 5 seconds",
    "Vanguard deployment cost reduced",
]


@pytest.fixture(scope="module", autouse=True)
def _game_vocabulary():
    """This file checks the game vocabulary, so it asks for it.

    The vocabulary is opt-in now: a term like "Bonds" or "Rhodes Island" has no
    business being in the default glossary, because somebody else's screen is
    not a game. These cases are exactly the screens it was learned from, so the
    tests that cover it enable it explicitly rather than assuming it is always
    on. That way the set keeps testing the knowledge, and the default keeps
    testing the default.
    """
    saved = dict(live.GLOSSARY)
    added = live.apply_game_glossary(True)
    yield added
    live.GLOSSARY.clear()
    live.GLOSSARY.update(saved)


@pytest.mark.parametrize("source,expected", GLOSSARY_CASES)
def test_glossary_translation(source, expected):
    assert live.glossary_translation(source) == expected


def test_the_game_vocabulary_is_not_on_by_default():
    """A game term must not be in the default glossary.

    On somebody else's screen "Gacha" is a word that means something else, and
    overriding its translation is not the project's decision to make.
    """
    saved = dict(live.GLOSSARY)
    try:
        live.GLOSSARY.clear()
        live.apply_game_glossary(False)
        for term in ("Gacha", "Bonds", "Rhodes Island", "My Sekai"):
            assert term not in live.GLOSSARY, term
    finally:
        live.GLOSSARY.clear()
        live.GLOSSARY.update(saved)


def test_a_user_entry_beats_the_game_vocabulary():
    """The user's own glossary is the deliberate answer for their own screens."""
    saved = dict(live.GLOSSARY)
    try:
        live.GLOSSARY.clear()
        live.GLOSSARY["Gacha"] = "Мой перевод"
        live.apply_game_glossary(True)
        assert live.GLOSSARY["Gacha"] == "Мой перевод"
    finally:
        live.GLOSSARY.clear()
        live.GLOSSARY.update(saved)


@pytest.mark.parametrize("text", NOT_A_NAME)
def test_plain_text_is_not_mistaken_for_a_name(text):
    protected, mapping = protect_proper_nouns(text)
    assert not mapping
    assert protected == text


@pytest.mark.parametrize("text", HAS_NAME)
def test_names_are_protected(text):
    protected, mapping = protect_proper_nouns(text)
    assert mapping, f"{text!r} has a name that must be protected"
    assert protected != text
    # Placeholder still round-trips; glossary may Cyrillicize the name.
    restored = restore_proper_nouns(protected, mapping)
    for original in mapping.values():
        assert original in text
        assert original in restored or restored != protected
    assert "ZZNAME" not in restored
    assert "\u3010" not in restored


# Lines a game HUD really shows, which the digit filter used to delete.
REAL_LINES = [
    "Bonds 14820",
    "HP 120/120",
    "CP 25/25",
    "Lv. 12 to Lv. 13",
    "Wave 3/5",
    "Turn 2/10",
    "Score 12345",
    "SCORE 999999",
    "DMG 12345",
    "Obtained 350 x Col",
    "XP 1.5M",
]

# Digit blobs and keyboard mash, which it must still catch.
GARBAGE_LINES = [
    "1482014820",
    "1122334455",
    "1 2 3 4 5 6 7 8",
    "ASDF 9876543210",
    "Mw (r ~ 6-8 nMkcenen)",
]


@pytest.mark.parametrize("text", REAL_LINES)
def test_hud_lines_survive_the_garbage_filter(text):
    assert not live.is_garbage_ocr(text), f"{text!r} is a real HUD line"


@pytest.mark.parametrize("text", GARBAGE_LINES)
def test_digit_blobs_are_still_caught(text):
    assert live.is_garbage_ocr(text), f"{text!r} is OCR noise"


# (better reading, misread of the same line) - the misread must lose.
READING_PAIRS = [
    ("Chapter 3 - The Frozen Harbour", "Chanter 3 - The Frozen Harhour"),
    ("Skills are full", "Skilis are full"),
    ("Stamina 120/120", "Stamina l20/l20"),
    ("Press E to talk", "Press E to tolk"),
]


@pytest.mark.parametrize("good,bad", READING_PAIRS)
def test_misread_never_scores_higher(good, bad):
    """A misread may tie, but must never look better than the right reading."""
    assert live._reading_quality(good) >= live._reading_quality(bad)


def test_structural_score_separates_a_real_misread():
    # The one pair the structural signals can settle: "rh" is not English.
    assert live._reading_quality("Chapter 3 - The Frozen Harbour") > live._reading_quality(
        "Chanter 3 - The Frozen Harhour"
    )


def test_same_line_is_not_drawn_twice():
    """Two boxes on the same spot must become one block, not two cards."""
    good = {"text": "Chapter 3 - The Frozen Harbour", "box": (30, 12, 340, 36), "conf": 100}
    bad = {"text": "Chanter 3 - The Frozen Harhour", "box": (30, 12, 341, 37), "conf": 100}
    other = {"text": "Skills are full", "box": (30, 120, 160, 142), "conf": 100}
    merged = live.merge_overlapping_duplicates([good, bad, other])
    assert len(merged) == 2
    assert [p["text"] for p in merged] == ["Chapter 3 - The Frozen Harbour", "Skills are full"]


def test_glossary_is_not_a_test_answer_list():
    """An entry must be a term, not a whole sentence copied from a probe."""
    for key in live.GLOSSARY:
        assert len(key) <= 40, key
        # Terms are short and capitalisation is a UI convention ("Tap!"), so the
        # real marker of a pasted sentence is its length and its verb.
        assert len(key.split()) <= 3, key
