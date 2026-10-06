"""OCR engine routing

Hierarchy:

```text
RapidOCR  = primary generic
MeikiOCR  = Japanese specialist
Tesseract = fallback
```

Do not run every engine on every frame. Confidence scores from different
engines are not directly comparable without calibration — routing uses
presence/emptiness and script cues, not raw score contests.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass

from ..core.text import engine_enabled

# Canonical order for config defaults and --doctor display.
ENGINE_HIERARCHY: tuple[str,...] = ("rapid", "meiki", "tesseract")

PRIMARY = "rapid"
JP_SPECIALIST = "meiki"
FALLBACK = "tesseract"


@dataclass(frozen=True)
class EnginePlan:
    """Which engines to touch for one recognition pass."""

    engines: tuple[str, ...]
    reason: str


def available_engines() -> tuple[str, ...]:
    """Engines that are both configured and importable enough to try."""
    out: list[str] = []
    for name in ENGINE_HIERARCHY:
        if not engine_enabled(name):
            continue
        if name == "rapid":
            try:
                if importlib.util.find_spec("rapidocr") is None:
                    continue
            except (ImportError, ValueError):
                continue
        elif name == "meiki":
            try:
                if importlib.util.find_spec("meikiocr") is None:
                    continue
            except (ImportError, ValueError):
                continue
        elif name == "tesseract":
            if not shutil.which("tesseract"):
                continue
        out.append(name)
    return tuple(out)


def plan_engines(
    *,
    cjk_hint: bool = False,
    primary_empty: bool = False,
    force_all: bool = False,
) -> EnginePlan:
    """Decide the engine sequence for one pass.

    ``force_all`` is reserved for diagnostics — live/universal OCR must not
    use it on the hot path.
    """
    avail = available_engines()
    if not avail:
        return EnginePlan((), "no OCR engines available")
    if force_all:
        return EnginePlan(avail, "diagnostic: all available")

    chosen: list[str] = []
    if PRIMARY in avail:
        chosen.append(PRIMARY)
    if cjk_hint and JP_SPECIALIST in avail and JP_SPECIALIST not in chosen:
        chosen.append(JP_SPECIALIST)
    if (primary_empty or not chosen) and FALLBACK in avail and FALLBACK not in chosen:
        chosen.append(FALLBACK)
    # Meiki after a weak Rapid Latin-only read when CJK shows up later.
    if primary_empty and JP_SPECIALIST in avail and JP_SPECIALIST not in chosen:
        chosen.append(JP_SPECIALIST)
    if not chosen:
        chosen = list(avail[:1])
    return EnginePlan(tuple(chosen), _reason(chosen, cjk_hint=cjk_hint, primary_empty=primary_empty))


def _reason(engines: list[str], *, cjk_hint: bool, primary_empty: bool) -> str:
    parts = ["+".join(engines) or "none"]
    if cjk_hint:
        parts.append("cjk")
    if primary_empty:
        parts.append("fallback")
    return ",".join(parts)
