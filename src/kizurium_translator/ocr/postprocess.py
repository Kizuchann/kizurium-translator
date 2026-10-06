"""OCR postprocessing stages.

Pipeline:

```text
raw OCR
→ safe normalization
→ structural merge
→ semantic grouping
```

Safe normalization may:

- Unicode NFKC / width normalization
- strip control characters
- fix obvious impossible OCR artifacts (``1s``→``is`` style)

Safe normalization must **not**:

- translate
- drop punctuation
- rewrite valid Russian
- apply game glossary (plain-copy / universal mode)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..core.text import clean_ocr_text

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_normalize_text(text: str) -> str:
    """Stage 1: reversible, language-agnostic cleanup of one string."""
    if not text:
        return ""
    # NFKC folds fullwidth Latin/digits without touching Cyrillic letters.
    t = unicodedata.normalize("NFKC", text)
    t = _CONTROL_RE.sub("", t)
    # Keep newlines (layout); collapse other whitespace per line.
    lines = []
    for line in t.split("\n"):
        line = re.sub(r"[^\S\n]+", " ", line).strip()
        lines.append(line)
    t = "\n".join(lines).strip()
    # Obvious Latin OCR artifacts only (clean_ocr_text). Does not touch Russian.
    return clean_ocr_text(t)


def safe_normalize_lines(lines: list[dict]) -> list[dict[str, Any]]:
    """Apply:func:`safe_normalize_text` to each line's ``text``."""
    out: list[dict[str, Any]] = []
    for p in lines:
        copy = dict(p)
        copy["text"] = safe_normalize_text(str(p.get("text", "")))
        if copy["text"]:
            out.append(copy)
    return out


def structural_merge(lines: list[dict]) -> list[dict[str, Any]]:
    """Stage 2: reading-order sort + soft wrap merge (geometry only)."""
    if not lines:
        return []
    from .reading_order import (
        merge_soft_wraps,
        order_for_reading,
        paragraphs_without_crossing_columns,
    )

    keep = [dict(p) for p in lines if str(p.get("text", "")).strip()]
    if not keep:
        return []
    for p in keep:
        if "line_height" not in p:
            box = p.get("box") or (0, 0, 0, 12)
            p["line_height"] = max(8, int(box[3]) - int(box[1]))
    ordered = order_for_reading(keep)
    # Soft wraps only — finished sentences must stay separate for OCR-copy.
    return paragraphs_without_crossing_columns(ordered, merge_fn=merge_soft_wraps)


def semantic_group_plaintext(paragraphs: list[dict]) -> str:
    """Stage 3: paragraphs → clipboard text (reconstruction)."""
    from .clipboard import reconstruct_clipboard

    return reconstruct_clipboard(paragraphs).rstrip("\n")


def postprocess_ocr_lines(raw_lines: list[dict]) -> str:
    """Full normalize → merge → reconstruct chain for plain-copy / universal OCR."""
    safe = safe_normalize_lines(raw_lines)
    merged = structural_merge(safe)
    return semantic_group_plaintext(merged)


def postprocess_ocr_layout(raw_lines: list[dict]) -> list[dict]:
    """normalize → visual group → semantic roles (structured)."""
    from .roles import semantic_layout_pipeline

    return semantic_layout_pipeline(raw_lines)
