"""generic semantic layout roles (game-agnostic).

Pipeline stage:

```text
OCR lines → visual grouping → semantic grouping → role classification
```

Roles are properties of shape and reading context, not of a title's glossary.
"""
from __future__ import annotations

from enum import Enum

from ..core.text import looks_like_spoken_line
from ..ocr.engine import looks_like_dialogue_choice
from ..typography.metrics import looks_like_ui_prompt


class SemanticRole(str, Enum):
    DIALOGUE = "DIALOGUE"
    SPEAKER = "SPEAKER"
    SUBTITLE = "SUBTITLE"
    HUD = "HUD"
    MENU = "MENU"
    NOTIFICATION = "NOTIFICATION"
    ITEM = "ITEM"
    TITLE = "TITLE"
    BODY = "BODY"
    CHOICE = "CHOICE"
    # Not words. A strip of glyphs - a rune bar, a sigil row, an icon legend -
    # that the recogniser reads as letters and the backend then transliterates
    # into a card. A profile may point at such a strip; the universal path has
    # no way to tell it from text and must not guess one way or the other.
    DECOR = "DECOR"
    UNKNOWN = "UNKNOWN"


# Font / layout faces (_role_for) stay separate; these are semantic overlay roles.
PLAN_ROLES: frozenset[str] = frozenset(r.value for r in SemanticRole)


def classify_line_role(line: dict, *, peers: list[dict] | None = None) -> SemanticRole:
    """Assign a semantic role to one OCR/layout line.

    Uses geometry and light linguistic cues shared across games — never a
    per-title vocabulary.
    """
    text = str(line.get("text") or line.get("source") or "").strip()
    kind = str(line.get("kind") or "").strip().lower()
    box = line.get("box") or (0, 0, 0, 0)
    try:
        x1, y1, x2, y2 = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
    except Exception:  # noqa: BLE001
        x1 = y1 = 0
        x2 = y2 = 1
    w = max(1, x2 - x1)
    h = max(1, y2 - y1)
    ratio = w / float(h)

    if kind in ("dialogue", "dialogue-line", "body") or line.get("subtitle"):
        if kind == "subtitle" or line.get("subtitle"):
            return SemanticRole.SUBTITLE
        return SemanticRole.DIALOGUE
    if kind in ("name", "speaker", "chip"):
        return SemanticRole.SPEAKER
    if kind in ("menu", "choice"):
        return SemanticRole.CHOICE if kind == "choice" else SemanticRole.MENU
    if kind in ("toast", "popup", "transient", "notification"):
        return SemanticRole.NOTIFICATION
    if kind in ("item", "loot", "reward"):
        return SemanticRole.ITEM
    if kind in ("title", "heading", "display"):
        return SemanticRole.TITLE
    if kind in ("hud", "stat", "label", "ui"):
        return SemanticRole.HUD

    if not text:
        return SemanticRole.UNKNOWN

    if looks_like_dialogue_choice(text):
        return SemanticRole.CHOICE
    if looks_like_spoken_line(text) and ratio >= 4.0:
        return SemanticRole.DIALOGUE
    if looks_like_ui_prompt(text) and len(text) <= 28:
        return SemanticRole.MENU

    # Short centred-ish name above a longer line → speaker.
    if peers and len(text) <= 24 and h <= 28 and ratio <= 8.0:
        below = [
            p
            for p in peers
            if int((p.get("box") or (0, 0, 0, 0))[1]) >= y2 - 4
            and looks_like_spoken_line(str(p.get("text") or ""))
        ]
        if below and y2 < int((below[0].get("box") or (0, 9999, 0, 0))[1]) + 8:
            return SemanticRole.SPEAKER

    if h >= 34 and ratio >= 3.2 and len(text) <= 32:
        return SemanticRole.TITLE
    if float(line.get("src_lines", 1) or 1) > 1 or ratio >= 6.0:
        if looks_like_spoken_line(text):
            return SemanticRole.DIALOGUE
        return SemanticRole.BODY
    if len(text) <= 40 and ratio < 6.0:
        return SemanticRole.HUD
    return SemanticRole.UNKNOWN


def annotate_roles(
    lines: list[dict],
    *,
    frame_size: tuple[int, int] | None = None,
) -> list[dict]:
    """Copy lines and set ``semantic_role`` / keep ``kind`` aligned when empty.

    ``frame_size`` (w, h) enables soft priors from the active profile's UI zones.
    No profile selected → zones ignored (universal path unchanged).
    """
    from ..profile import zone_role_hint

    out: list[dict] = []
    fw = fh = 0
    if frame_size is not None:
        fw, fh = int(frame_size[0]), int(frame_size[1])
    for line in lines:
        copy = dict(line)
        role = classify_line_role(copy, peers=lines)
        # Soft prior: profile zones may correct a weak/geometry-only guess.
        # Strong linguistic kinds (speaker/choice already set) stay put.
        if fw > 0 and fh > 0 and role in (
            SemanticRole.UNKNOWN,
            SemanticRole.HUD,
            SemanticRole.BODY,
            SemanticRole.TITLE,
            SemanticRole.MENU,
            SemanticRole.NOTIFICATION,
            SemanticRole.ITEM,
        ):
            hint = zone_role_hint(copy.get("box") or (0, 0, 0, 0), frame_w=fw, frame_h=fh)
            if hint:
                try:
                    role = SemanticRole(hint)
                    copy["role_from_zone"] = hint
                except ValueError:
                    pass
        copy["semantic_role"] = role.value
        if role is SemanticRole.DECOR:
            # Overrides whatever kind the line arrived with: a rune strip reads
            # as `ui` to the recogniser, and that kind is exactly what would
            # put a card on top of it.
            copy["kind"] = "decor"
        elif not str(copy.get("kind") or "").strip():
            copy["kind"] = {
                SemanticRole.DIALOGUE: "dialogue",
                SemanticRole.SPEAKER: "name",
                SemanticRole.SUBTITLE: "subtitle",
                SemanticRole.HUD: "hud",
                SemanticRole.MENU: "menu",
                SemanticRole.NOTIFICATION: "toast",
                SemanticRole.ITEM: "item",
                SemanticRole.TITLE: "title",
                SemanticRole.BODY: "body",
                SemanticRole.CHOICE: "choice",
                SemanticRole.DECOR: "decor",
                SemanticRole.UNKNOWN: "label",
            }.get(role, "label")
        out.append(copy)
    return out


def semantic_layout_pipeline(raw_lines: list[dict]) -> list[dict]:
    """The chain: normalize → visual merge → role classify."""
    from .postprocess import safe_normalize_lines, structural_merge

    safe = safe_normalize_lines(raw_lines)
    grouped = structural_merge(safe)
    return annotate_roles(grouped)
