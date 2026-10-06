""": STABLE / SLOW / DYNAMIC / TRANSIENT block classification."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.scheduler import (  # noqa: E402
    DynamicsClass,
    classify_block_dynamics,
    gone_grace_ms,
)
from kizurium_translator.live.state import TrackedBlock  # noqa: E402


def _block(
    *,
    first_seen: float,
    last_seen: float,
    seen_passes: int,
    change_count: int,
    position_drift: float = 0.0,
    text: str = "Settings",
) -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=(10, 10, 100, 30),
        text=text,
        norm=text.lower(),
        script="en",
        lang="en",
        conf=90.0,
        engine="rapidocr",
        first_seen=first_seen,
        last_seen=last_seen,
        seen_passes=seen_passes,
        change_count=change_count,
        position_drift=position_drift,
)


def test_phase45_labels():
    assert {c.value for c in DynamicsClass} == {
        "STABLE",
        "SLOW",
        "DYNAMIC",
        "TRANSIENT",
}


def test_stable_hud():
    now = 100.0
    blk = _block(
        first_seen=now - 20.0,
        last_seen=now,
        seen_passes=40,
        change_count=1,
        position_drift=5.0,
)
    assert classify_block_dynamics(blk, now) == DynamicsClass.STABLE
    assert gone_grace_ms(blk) >= 1000


def test_transient_toast():
    now = 50.0
    blk = _block(
        first_seen=now - 0.4,
        last_seen=now,
        seen_passes=2,
        change_count=1,
        text="Saved!",
)
    assert classify_block_dynamics(blk, now) == DynamicsClass.TRANSIENT
    # Grace still follows explicit kind until enough history exists.
    blk.params["kind"] = "transient"
    assert gone_grace_ms(blk) == 300
    blk.seen_passes = 5
    assert gone_grace_ms(blk) == 300


def test_dynamic_dialogue():
    now = 80.0
    blk = _block(
        first_seen=now - 5.0,
        last_seen=now,
        seen_passes=20,
        change_count=14,
        text="Hello there friend",
)
    assert classify_block_dynamics(blk, now) == DynamicsClass.DYNAMIC
