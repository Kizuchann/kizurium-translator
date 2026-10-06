"""Снимок области и мелкие вопросы к уже прочитанной строке.

`grim` сюда попадает один раз. Цикл кадра спрашивает снимок, а не знает,
чем он снят.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PIL import Image

from .. import capture
from ..core.text import (
    RE_CJK,
    looks_like_spoken_line,
    normalize_subtitle_ocr,
)
from ..ocr.engine import is_overlay_echo_ocr, looks_like_subtitle_continue
from ..typography.metrics import unglue_english

# paint-complete wait is the barrier; sleep is only a safety fallback.
CAPTURE_PAINT_TIMEOUT_S = 0.28
CAPTURE_SAFETY_SLEEP_S = 0.02


def parse_geom(raw: str) -> tuple[int, int, int, int]:
    return capture.parse_geom(raw)


def grim_region(geom: str) -> Image.Image | None:
    try:
        return capture.capture_image(geom)
    except capture.CaptureError:
        return None


@dataclass
class CleanCapture:
    """Result of a synchronized clean capture"""

    image: Image.Image | None
    was_hidden: bool


def clean_capture(
    state,
    geom: str,
    *,
    paint_timeout: float = CAPTURE_PAINT_TIMEOUT_S,
    safety_sleep: float = CAPTURE_SAFETY_SLEEP_S,
    restore: bool = False,
) -> CleanCapture:
    """Hide overlay → wait for a completed paint → grim → optional restore.

    The paint event (``wait_hidden_paint``) is the primary barrier. ``safety_sleep``
    is only a fallback for compositors that never signal — never the sole sync.
    """
    was_hidden = bool(getattr(state, "hidden", False))
    state.hide()
    state.wait_hidden_paint(float(paint_timeout))
    if safety_sleep and safety_sleep > 0:
        time.sleep(float(safety_sleep))
    image = grim_region(geom)
    if restore and not was_hidden:
        state.show()
    return CleanCapture(image=image, was_hidden=was_hidden)

def is_map_legend_label(text: str) -> bool:
    """Пункты легенды карты — чтобы отличать меню карты от геймплей-HUD."""
    t = unglue_english(normalize_subtitle_ocr(text or "")).strip()
    if not t or looks_like_spoken_line(t):
        return False
    low = t.casefold()
    keys = (
        "waypoint", "safe area", "warp terminal", "magical seal", "treasure chest",
        "side quest", "quest area", "remove all", "reset view", "zoom in", "zoom out",
        "change map", "checkpoint", "arks", "seals", "trinket", "other",
    )
    return any(k in low for k in keys)


def soft_probe_is_real_dialogue(qkey: str, lang: str | None) -> bool:
    """Грязный soft/dirty OCR: реальная EN/JP реплика, не эхо оверлея."""
    if not qkey or is_overlay_echo_ocr(qkey):
        return False
    parts = [p.strip() for p in qkey.split("|") if p.strip()]
    if not parts:
        return False
    if any(is_overlay_echo_ocr(p) for p in parts):
        return False
    if lang == "jpn-game":
        return any(len(RE_CJK.findall(p)) >= 2 for p in parts)
    return any(looks_like_spoken_line(p) or looks_like_subtitle_continue(p) for p in parts)

