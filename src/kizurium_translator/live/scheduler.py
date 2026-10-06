"""Как долго ждать, прежде чем сдаться на блок.

Элемент, который исчез с экрана, не всегда исчез: бывает,
кадр просто поймал его мимо. Уход блока подтверждается только
спустя несколько кадров, иначе карточка мигает на каждом
пропуске строки диалога.

visibility state machine:

```text
VISIBLE → TEMPORARILY_MISSING → REMOVED
```

Grace windows:

```text
dialogue/subtitle = 500 ms
HUD               = 1000 ms
transient         = 300 ms
```

One OCR miss is never enough for REMOVED.
"""
from __future__ import annotations

import math
from enum import Enum

from ..core.text import (  # noqa: F401
    looks_like_spoken_line,
)
from .reconcile import (  # noqa: F401
    _box_has_ink,
)
from .state import (  # noqa: F401
    TrackedBlock,
)

STABLE_CHECK_S = 8.0


VIDEO_RECHECK_S = 1.2


SUBTITLE_EMPTY_RECHECK_S = 1.6


GAME_RECHECK_S = 1.4


SLOW_CYCLE_S = 1.2


CYCLE_DUTY_CAP = 2.0


# starting points (ms). Calibrate on real scenes later.
GONE_GRACE_MS_DIALOGUE = 500
GONE_GRACE_MS_SUBTITLE = 500
GONE_GRACE_MS_HUD = 1000
GONE_GRACE_MS_TRANSIENT = 300

# Convert time grace → pass floor using a conservative live cycle.
ASSUMED_CYCLE_MS = 250
# Plan: one miss is never enough.
GONE_GRACE_MIN_PASSES = 2


class VisibilityState(str, Enum):
    """disappearance states."""

    VISIBLE = "visible"
    TEMPORARILY_MISSING = "temporarily_missing"
    REMOVED = "removed"


class DynamicsClass(str, Enum):
    """lifespan / change-rate classes."""

    STABLE = "STABLE"
    SLOW = "SLOW"
    DYNAMIC = "DYNAMIC"
    TRANSIENT = "TRANSIENT"


# starting points. Calibrate on real scenes later.
DYNAMICS_STABLE_MIN_S = 8.0
DYNAMICS_STABLE_MAX_FREQ = 0.08
DYNAMICS_STABLE_MIN_POS = 0.75
DYNAMICS_SLOW_MIN_S = 3.0
DYNAMICS_SLOW_MAX_FREQ = 0.25
DYNAMICS_DYNAMIC_MAX_FREQ = 0.55
DYNAMICS_TRANSIENT_MAX_S = 2.0


def classify_block_dynamics(block: TrackedBlock, now: float | None = None) -> DynamicsClass:
    """STABLE / SLOW / DYNAMIC / TRANSIENT from track signals."""
    import time as _time

    clock = float(now if now is not None else _time.monotonic())
    lifespan = block.lifespan_s(clock)
    freq = block.change_frequency()
    pos = block.position_stability()
    visible = max(lifespan, float(block.seen_passes) * (ASSUMED_CYCLE_MS / 1000.0))

    if (
        visible >= DYNAMICS_STABLE_MIN_S
        and freq <= DYNAMICS_STABLE_MAX_FREQ
        and pos >= DYNAMICS_STABLE_MIN_POS
    ):
        return DynamicsClass.STABLE
    if visible <= DYNAMICS_TRANSIENT_MAX_S and (freq >= 0.4 or block.seen_passes <= 3):
        return DynamicsClass.TRANSIENT
    if freq >= DYNAMICS_DYNAMIC_MAX_FREQ or (freq >= 0.35 and pos < 0.5):
        return DynamicsClass.DYNAMIC
    if visible >= DYNAMICS_SLOW_MIN_S and freq <= DYNAMICS_SLOW_MAX_FREQ:
        return DynamicsClass.SLOW
    if freq >= 0.3:
        return DynamicsClass.DYNAMIC
    if visible < DYNAMICS_TRANSIENT_MAX_S:
        return DynamicsClass.TRANSIENT
    return DynamicsClass.SLOW


def _fleeting_prompt(text: str) -> bool:
    """Item pickup and loot toasts. They leave with the prompt, not with the HUD."""
    low = " ".join(str(text or "").split()).casefold()
    if low in {"pick up", "pickup"}:
        return True
    return low.startswith(("obtained ", "acquired ", "picked up "))


def _block_kind(block: TrackedBlock) -> str:
    kind = str(block.params.get("kind", "") or "").strip().lower()
    if _fleeting_prompt(block.text):
        return "transient"
    if kind in ("dialogue", "dialogue-line", "body", "subtitle"):
        return "dialogue" if kind != "subtitle" else "subtitle"
    if kind in ("transient", "toast", "popup"):
        return "transient"
    if looks_like_spoken_line(str(block.text)):
        return "dialogue"
    return "hud"


def gone_grace_ms(block: TrackedBlock) -> int:
    """Milliseconds a block may stay TEMPORARILY_MISSING before REMOVED."""
    # once enough track history exists, dynamics can override kind.
    if int(getattr(block, "seen_passes", 0) or 0) >= 4:
        dynamics = classify_block_dynamics(block)
        if dynamics == DynamicsClass.TRANSIENT:
            return GONE_GRACE_MS_TRANSIENT
        if dynamics == DynamicsClass.DYNAMIC:
            return GONE_GRACE_MS_DIALOGUE
        if dynamics == DynamicsClass.STABLE:
            return GONE_GRACE_MS_HUD
    kind = _block_kind(block)
    if kind == "dialogue":
        return GONE_GRACE_MS_DIALOGUE
    if kind == "subtitle":
        return GONE_GRACE_MS_SUBTITLE
    if kind == "transient":
        return GONE_GRACE_MS_TRANSIENT
    return GONE_GRACE_MS_HUD


def gone_grace(block: TrackedBlock) -> int:
    """Passes a block may stay on screen after it stops being readable.

    Dialogue changes fast and is worth re-reading sooner; a static label is
    cheaper to keep and more likely to be missed for a frame or two while the
    screen redraws underneath it. Derived from millisecond windows.
    """
    passes = int(math.ceil(gone_grace_ms(block) / float(ASSUMED_CYCLE_MS)))
    return max(GONE_GRACE_MIN_PASSES, passes)


# Back-compat aliases used by older tests.
GONE_GRACE_DIALOGUE = max(
    GONE_GRACE_MIN_PASSES, int(math.ceil(GONE_GRACE_MS_DIALOGUE / ASSUMED_CYCLE_MS))
)
GONE_GRACE_UI = max(
    GONE_GRACE_MIN_PASSES, int(math.ceil(GONE_GRACE_MS_HUD / ASSUMED_CYCLE_MS))
)


def visibility_state(block: TrackedBlock, now: float | None = None) -> VisibilityState:
    """Current visibility state for one tracked block (not yet REMOVED until confirm)."""
    if block.missing_passes <= 0 and block.missing_since is None:
        return VisibilityState.VISIBLE
    return VisibilityState.TEMPORARILY_MISSING


def is_removal_due(block: TrackedBlock, now: float) -> bool:
    """True when TEMPORARILY_MISSING has outlived both pass floor and time grace."""
    if block.missing_passes < GONE_GRACE_MIN_PASSES:
        return False
    if block.missing_passes < gone_grace(block):
        # Time can still fire if we have a clock and enough wall time.
        if block.missing_since is None:
            return False
        return (now - float(block.missing_since)) * 1000.0 >= float(gone_grace_ms(block))
    return True


def confirm_gone(
    region_img,
    blocks: list[TrackedBlock],
    seen_ids: set[int],
    now: float,
) -> tuple[list[int], list[TrackedBlock]]:
    """Decide which tracked elements have actually left the screen.

    Two questions, and they are not the same. A block that was not re-read this
    pass has no news: its pixels did not move enough to be scheduled, which is
    also what a still-present element looks like, so it is left alone. A block
    that was re-read and came back empty has been looked at and found nothing,
    and only that counts towards removal.

    The second question is whether the box is still empty by now. A single empty
    read is a missed frame, so the box is sampled again from the current frame
    and the block is only marked once the ink is genuinely gone. Reading the box
    is cheap; keeping a card that no longer has anything under it is not.
    """
    gone: list[int] = []
    keep: list[TrackedBlock] = []
    for blk in blocks:
        if blk.id in seen_ids:
            blk.missing_passes = 0
            blk.missing_since = None
            keep.append(blk)
            continue
        if blk.missing_since is None:
            blk.missing_since = float(now)
        blk.missing_passes += 1
        if not is_removal_due(blk, now):
            keep.append(blk)
            continue
        if _box_has_ink(region_img, blk.box):
            # Still something at the same place, so it is unreadable rather than
            # gone. Counted again on the next pass.
            blk.missing_passes = 0
            blk.missing_since = None
            keep.append(blk)
            continue
        gone.append(blk.id)
    return gone, keep
