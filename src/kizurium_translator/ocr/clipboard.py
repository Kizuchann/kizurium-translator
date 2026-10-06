"""Clipboard reconstruction

Must preserve line breaks, paragraphs, empty lines, punctuation, Unicode.
Indentation uses logical tab-stops only when a line is clearly stepped in
from its neighbours — not on every menu row.
"""

from __future__ import annotations

# Logical tab width in source pixels.
TAB_STOP_PX = 32


def _box(p: dict) -> tuple[int, int, int, int]:
    b = p.get("box") or (0, 0, 0, 0)
    return int(b[0]), int(b[1]), int(b[2]), int(b[3])


def logical_indent(x: int, *, origin_x: int = 0, tab_stop: int = TAB_STOP_PX) -> str:
    """Map a left edge ``x`` to a run of tab characters."""
    stop = max(1, int(tab_stop))
    n = max(0, int(x) - int(origin_x)) // stop
    return "\t" * n


def _paragraph_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in str(text).split("\n")]


def reconstruct_clipboard(
    paragraphs: list[dict],
    *,
    tab_stop: int = TAB_STOP_PX,
) -> str:
    """Turn ordered paragraph dicts into clipboard-ready UTF-8 text.

    Nearby lines (typical menu / paragraph) are joined with a single newline.
    A blank line is inserted only when the vertical gap is clearly a paragraph
    break. Tabs are added only for a clear horizontal step (≥ one tab-stop).
    """
    if not paragraphs:
        return ""
    with_box = [p for p in paragraphs if str(p.get("text", "")).strip()]
    if not with_box:
        return ""

    origin_x = min(_box(p)[0] for p in with_box)
    stop = max(1, int(tab_stop))
    chunks: list[str] = []
    prev_bottom: int | None = None
    prev_lh = 12

    for p in with_box:
        x1, y1, _x2, y2 = _box(p)
        lh = max(8, int(p.get("line_height") or (y2 - y1) or 12))
        raw = str(p.get("text", ""))
        lines = [ln for ln in _paragraph_lines(raw)]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue

        # Only indent when clearly stepped in (skip 0–almost-aligned rows).
        step = max(0, x1 - origin_x)
        indent = ("\t" * (step // stop)) if step >= stop else ""
        body = "\n".join(
            (indent + ln.lstrip()) if ln.strip() else ""
            for ln in lines
        )

        if chunks:
            gap = 0 if prev_bottom is None else max(0, y1 - prev_bottom)
            # Close stack → one newline; real paragraph gap → blank line.
            if gap >= max(prev_lh, lh) * 1.8:
                sep = "\n\n"
            else:
                sep = "\n"
            chunks.append(sep + body)
        else:
            chunks.append(body)
        prev_bottom = y2
        prev_lh = lh

    return "".join(chunks).rstrip() + ("\n" if chunks else "")


def clipboard_bytes(text: str) -> bytes:
    """UTF-8 payload for ``wl-copy``."""
    return (text or "").encode("utf-8")
