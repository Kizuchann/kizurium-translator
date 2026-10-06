"""VISIBLE → TEMPORARILY_MISSING → REMOVED with timed grace.

"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.scheduler import (  # noqa: E402
    GONE_GRACE_MIN_PASSES,
    GONE_GRACE_MS_DIALOGUE,
    GONE_GRACE_MS_HUD,
    GONE_GRACE_MS_TRANSIENT,
    VisibilityState,
    gone_grace,
    gone_grace_ms,
    is_removal_due,
    visibility_state,
)
from kizurium_translator.live.state import TrackedBlock  # noqa: E402


def _blk(text: str, kind: str) -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=(0, 0, 40, 16),
        text=text,
        norm=text.lower(),
        script="en",
        lang="en",
        conf=90,
        engine="rapid",
        params={"kind": kind},
    )


def test_phase41_grace_windows_ms():
    assert GONE_GRACE_MS_DIALOGUE == 500
    assert GONE_GRACE_MS_HUD == 1000
    assert GONE_GRACE_MS_TRANSIENT == 300
    assert GONE_GRACE_MIN_PASSES == 2


def test_grace_ms_by_kind():
    assert gone_grace_ms(_blk("Hi there friend", "dialogue")) == 500
    assert gone_grace_ms(_blk("Settings", "ui")) == 1000
    assert gone_grace_ms(_blk("Toast!", "transient")) == 300
    assert gone_grace(_blk("Hi there friend", "dialogue")) < gone_grace(_blk("Settings", "ui"))


def test_visibility_state_machine():
    b = _blk("Settings", "ui")
    assert visibility_state(b) is VisibilityState.VISIBLE
    b.missing_passes = 1
    b.missing_since = 1.0
    assert visibility_state(b) is VisibilityState.TEMPORARILY_MISSING


def test_one_miss_is_never_enough_for_removal():
    b = _blk("Settings", "ui")
    b.missing_passes = 1
    b.missing_since = 0.0
    assert is_removal_due(b, now=10.0) is False


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.state import TrackedBlock  # noqa: E402


def _block(text: str, kind: str) -> TrackedBlock:
    blk = TrackedBlock(
        id=1,
        box=(10, 10, 400, 40),
        text=text,
        norm=text.lower(),
        script="en",
        lang="en",
        conf=90.0,
        engine="rapidocr",
        first_seen=0.0,
        last_seen=1.0,
        seen_passes=2,
        change_count=0,
        position_drift=0.0,
    )
    blk.params["kind"] = kind
    return blk


def test_hud_outlives_subtitle_which_outlives_a_pickup():
    hud = _block("HP 250", "ui")
    subtitle = _block("Iori: That legendary weapon is ours!", "subtitle")
    pickup = _block("Pick Up", "ui")
    assert gone_grace_ms(hud) > gone_grace_ms(subtitle) > gone_grace_ms(pickup)


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live.state import TrackedBlock  # noqa: E402


def _block(text: str, kind: str = "ui") -> TrackedBlock:
    blk = TrackedBlock(
        id=1,
        box=(10, 10, 200, 40),
        text=text,
        norm=text.lower(),
        script="en",
        lang="en",
        conf=90.0,
        engine="rapidocr",
        first_seen=0.0,
        last_seen=1.0,
        seen_passes=2,
        change_count=0,
        position_drift=0.0,
    )
    blk.params["kind"] = kind
    return blk


def test_pick_up_is_a_prompt_not_a_courier():
    assert live.glossary_translation("Pick Up") == "Подобрать"


def test_pickup_and_obtained_leave_before_hud():
    hud = _block("In Combat", "ui")
    pickup = _block("Pick Up", "ui")
    loot = _block("Obtained Bronze Rapier.", "ui")
    assert gone_grace_ms(hud) > gone_grace_ms(pickup)
    assert gone_grace_ms(pickup) == 300
    assert gone_grace_ms(loot) == 300
