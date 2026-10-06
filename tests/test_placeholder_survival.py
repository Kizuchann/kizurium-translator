"""Local OPUS-MT must not paint 「ПРЮ」 or leave English under the card.

ws4 dialogue: a mid-sentence capitalised address (Doctor) was wrapped as 【0】,
local MT turned that into Cyrillic junk, restore never found the token, and
the second English sentence was dropped so it glowed through the overlay.

The fix is general (ASCII placeholders + reject truncated/lost-token local),
not a per-game glossary entry for that word.
"""

from __future__ import annotations

from kizurium_translator.translate import (
    Translator,
    placeholders_survived,
    protect_proper_nouns,
    restore_proper_nouns,
    translation_dropped_tail,
)

# Mid-sentence capitalised address — same shape as many UIs, not Arknights-only.
SAMPLE = (
    "However, you'll always be the most important person to me, Doctor. "
    "No matter what happens, this will never change."
)


def test_mid_sentence_name_uses_ascii_placeholder_not_lenticular():
    protected, mapping = protect_proper_nouns(SAMPLE)
    assert "ZZNAME0ZZ" in protected
    assert "\u3010" not in protected
    assert "Doctor" in mapping.values()


def test_pryu_garbage_is_not_a_surviving_placeholder():
    _protected, mapping = protect_proper_nouns(SAMPLE)
    garbage = "Но ты всегда будешь самым важным для меня человеком, ПРЮ"
    assert not placeholders_survived(garbage, mapping)
    assert translation_dropped_tail(SAMPLE, garbage)


def test_truncated_local_is_rejected_even_if_placeholder_stays():
    assert translation_dropped_tail(
        SAMPLE,
        "Но ты всегда будешь самым важным для меня человеком, ZZNAME0ZZ.",
    )


def test_restore_puts_original_name_back():
    protected, mapping = protect_proper_nouns(SAMPLE)
    full = (
        "Но ты всегда будешь самым важным для меня человеком, ZZNAME0ZZ. "
        "Что бы ни случилось, это никогда не изменится."
    )
    out = restore_proper_nouns(full, mapping)
    assert "Doctor" in out or "Доктор" in out
    assert "ZZNAME" not in out
    assert "ПРЮ" not in out


def test_translator_drops_local_pryu_and_falls_through(tmp_path):
    """via_local returns the real failure mode; translate() must not keep it."""
    tr = Translator(source="en", target="ru", cache_path=tmp_path / "c.sqlite")
    tr.via_local = lambda *a, **k: (  # type: ignore[method-assign]
        "Но ты всегда будешь самым важным для меня человеком, ПРЮ"
    )
    tr.via_gtx = lambda *a, **k: (  # type: ignore[method-assign]
        "Но ты всегда будешь самым важным для меня человеком, ZZNAME0ZZ. "
        "Что бы ни случилось, это никогда не изменится."
    )
    out = tr.translate(SAMPLE)
    assert "ПРЮ" not in out
    assert "Doctor" in out or "Доктор" in out
    assert "изменит" in out.lower()
    assert "No matter" not in out
