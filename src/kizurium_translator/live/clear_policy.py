"""when a full ``state.clear()`` is allowed.

Incremental updates must never wipe the whole overlay and re-OCR/retranslate
everything. A global clear is reserved for confirmed scene transitions,
session boundaries, and a confirmed empty scene.
"""
from __future__ import annotations

ALLOWED_GLOBAL_CLEAR = frozenset(
    {
        "scene_transition",
        "session_start",
        "session_stop",
        "empty_scene",
    }
)

# Reasons that look tempting on the incremental path but must not wipe.
FORBIDDEN_INCREMENTAL_CLEAR = frozenset(
    {
        "incremental",
        "partial_miss",
        "filter_empty",
        "retry",
        "soft_change",
    }
)


def may_global_clear(reason: str) -> bool:
    """gate: True only for the plan's allowed clear reasons."""
    return str(reason or "") in ALLOWED_GLOBAL_CLEAR
