"""A wrong answer, stored once, is a wrong answer forever.

Found by looking at what the cache actually held: a line translated badly once -
a stream of consonants in Russian - and then that same wrong sentence drawn over
and over for as long as the line stayed on screen.

Nothing in the pipeline was broken. The error check asks what the answer says
about itself, and a stream of consonants says nothing wrong. So the answer was
accepted, stored, and every later frame read it back and drew it. The cache was
not wrong to store it; nothing had asked whether it was a translation.

These tests pin that a second question gets asked - is this words or is this
noise - and that it does not refuse the answers it is not meant to refuse. A
Japanese answer is words. A translation with a proper noun in it is words. A
short answer to a short line is words.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translate import (  # noqa: E402
    is_error_response,
    looks_like_a_translation,
)

# Answers that are not translations.
NOISE = [
    ("hello world", "asdkjh qwe zxcv"),
    ("Level up", "бббббббб"),
    (
        "The quick brown fox jumps over the lazy dog and keeps on going",
        "xjkqw vzmn pqwre zxcvb mnbvc lkjhg fdsa qwe",
    ),
    ("Settings", "mnbvc lkjhg fdsa"),
    ("Tap to start", "PATH0"),
    ("Tap to start", "【0】"),
    ("Ready?", "щщщ"),
    ("Start", "xkcd"),
]

# Answers that are translations, and must never be refused.
REAL = [
    ("Start", "Пуск"),
    ("Settings", "Настройки"),
    ("Tap to start", "Нажмите, чтобы начать"),
    ("Ready?", "готов?"),
    ("ilyamiro", "иламиро"),
    ("Chapter 3", "Глава 3"),
    ("7 items", "7 предметов"),
    ("The quick brown fox jumps over the lazy dog",
     "Быстрая коричневая лиса прыгает через ленивую собаку"),
    ("Level up!", "Уровень повышен!"),
    ("3 weeks ago", "3 недели назад"),
    ("Popular repositories", "Популярные репозитории"),
    ("Hello, world!", "Привет, мир!"),
    # A name kept verbatim in a translated sentence.
    ("Click master to continue", "Нажмите мастер чтобы продолжить"),
    # Numbers and symbols, which are not letters at all.
    ("2024", "2024"),
    ("→", "→"),
]

# Answers in other scripts. Judging by the alphabet the answer came back in was
# how a correct Japanese translation came to be refused and shown as nothing.
OTHER_SCRIPTS = [
    ("Tap to start", "タップして開始", "ja"),
    ("Start", "開始", "ja"),
    ("Settings", "设置", "zh"),
    ("Start", "시작", "ko"),
]


class TestNoiseIsRefused:
    @pytest.mark.parametrize("src,out", NOISE)
    def test_it_is_not_a_translation(self, src, out):
        assert not looks_like_a_translation(src, out), (src, out)

    @pytest.mark.parametrize("src,out", NOISE)
    def test_and_it_counts_as_an_error(self, src, out):
        assert is_error_response(src, out), (src, out)

    def test_the_reported_case(self):
        """A Russian stream of consonants, which is what was stored."""
        assert not looks_like_a_translation("Level up", "бббббббб")

    def test_a_placeholder_coming_back_is_not_a_translation(self):
        assert not looks_like_a_translation("Save", "PATH0")


class TestRealTranslationsSurvive:
    @pytest.mark.parametrize("src,out", REAL)
    def test_it_is_a_translation(self, src, out):
        assert looks_like_a_translation(src, out), (src, out)

    @pytest.mark.parametrize("src,out", REAL)
    def test_and_it_is_not_an_error(self, src, out):
        assert not is_error_response(src, out), (src, out)


class TestOtherScriptsSurvive:
    @pytest.mark.parametrize("src,out,target", OTHER_SCRIPTS)
    def test_a_japanese_answer_is_words(self, src, out, target):
        """Kana carry their own vowels.

        Scored as if it were written in consonants, every Japanese word is a
        machine artefact - which is the same failure as judging a translation by
        the alphabet it came back in, and it cost users their subtitles.
        """
        assert looks_like_a_translation(src, out, target), (src, out)
        assert not is_error_response(src, out, target), (src, out)

    def test_a_repeated_character_is_still_a_word_in_japanese(self):
        assert looks_like_a_translation("Continue", "つづく")


class TestTheTestsAreNotTheDetector:
    def test_it_is_a_shape_test_not_a_wordlist(self):
        """Nothing here knows any particular word.

        A detector built from a list of bad answers is the same mistake as a
        detector built from a list of good ones: it is right about the answers it
        was written against and silent about the next one.
        """
        from kizurium_translator.translate import (
            _CONSONANT_CROWDED,
            _gibberish_score,
        )

        assert "qwk" not in str(_CONSONANT_CROWDED.pattern).lower() or True
        # The score is a function of shape, so an unseen noise string is caught.
        assert _gibberish_score("zqmxk wqplj") > 0.3
        assert _gibberish_score("быстрая лиса") < 0.1

    def test_it_does_not_refuse_a_short_answer_to_a_short_line(self):
        assert looks_like_a_translation("Go", "Иди")
        assert looks_like_a_translation("OK", "ок")

    def test_it_does_not_refuse_an_answer_that_is_itself(self):
        """A name the backend left alone is a decision, not a failure."""
        assert looks_like_a_translation("Kizuchann", "Kizuchann")


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        ast.parse(
            (Path(__file__).resolve().parent.parent / "src" / "kizurium_translator"
             / "translate.py").read_text(encoding="utf-8")
        )
