"""multi-signal speaker detection (no single-rule shortcuts).

Scores candidates from geometry, style, repetition, and proximity to dialogue.
A lone short word is never enough on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.text import looks_like_spoken_line
from .roles import SemanticRole, classify_line_role


@dataclass(frozen=True)
class SpeakerScore:
    line_index: int
    score: float
    reasons: tuple[str, ...]


def _box(line: dict) -> tuple[int, int, int, int]:
    b = line.get("box") or (0, 0, 0, 0)
    try:
        return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    except Exception:  # noqa: BLE001
        return 0, 0, 0, 1


def score_speaker_candidate(
    line: dict,
    *,
    index: int,
    peers: list[dict],
    known_names: set[str] | None = None,
) -> SpeakerScore:
    """aggregate speaker likelihood from many weak signals."""
    text = str(line.get("text") or "").strip()
    x1, y1, x2, y2 = _box(line)
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    reasons: list[str] = []
    score = 0.0

    role = classify_line_role(line, peers=peers)
    if role == SemanticRole.SPEAKER:
        score += 2.5
        reasons.append("role_speaker")
    if role == SemanticRole.DIALOGUE:
        return SpeakerScore(index, 0.0, ("is_dialogue",))

    # Relative position: above a dialogue body.
    dialogue_below = False
    for p in peers:
        px1, py1, px2, py2 = _box(p)
        if py1 < y2 - 2:
            continue
        if py1 - y2 > max(48, h * 3):
            continue
        overlap = min(x2, px2) - max(x1, px1)
        if overlap <= 0:
            continue
        if looks_like_spoken_line(str(p.get("text") or "")) or classify_line_role(
            p, peers=peers
        ) == SemanticRole.DIALOGUE:
            dialogue_below = True
            gap = py1 - y2
            score += 1.8
            reasons.append("above_dialogue")
            if gap <= max(12, h):
                score += 0.6
                reasons.append("tight_gap")
            break

    # Box size: name plates are compact.
    if h <= 32 and w <= 280:
        score += 0.7
        reasons.append("compact_box")
    font_px = float(line.get("font_px") or h or 0)
    if 10 <= font_px <= 28:
        score += 0.4
        reasons.append("name_size")
    weight = int(line.get("weight") or 0)
    if weight >= 600:
        score += 0.3
        reasons.append("weight")

    # Alignment with dialogue column.
    if dialogue_below:
        score += 0.4
        reasons.append("aligned")

    # Repetition / known names.
    norm = text.casefold()
    if known_names and norm in {n.casefold() for n in known_names}:
        score += 1.5
        reasons.append("known_name")
    repeats = sum(
        1
        for j, p in enumerate(peers)
        if j != index and str(p.get("text") or "").strip().casefold() == norm
    )
    if repeats:
        score += min(1.2, 0.4 * repeats)
        reasons.append("repeated")

    # Stability / visual style hints from prior track.
    if int(line.get("stable_passes") or 0) >= 3:
        score += 0.3
        reasons.append("stable")
    if str(line.get("kind") or "").lower() in ("name", "speaker", "chip"):
        score += 1.0
        reasons.append("kind")

    # Single-rule traps: short/one-word alone must not decide.
    tokens = [t for t in text.split() if t]
    if len(tokens) == 1 or len(text) < 12:
        if score < 2.0:
            score *= 0.35
            reasons.append("short_alone_damped")
        else:
            reasons.append("short_but_backed")

    if not dialogue_below and role not in (SemanticRole.SPEAKER,) and score < 2.5:
        score *= 0.5
        reasons.append("no_dialogue_near")

    return SpeakerScore(index, float(score), tuple(reasons))


def detect_speakers(
    lines: list[dict],
    *,
    known_names: set[str] | None = None,
    threshold: float = 2.2,
) -> list[SpeakerScore]:
    """Return speaker-likely lines sorted by score descending."""
    scored = [
        score_speaker_candidate(
            line, index=i, peers=lines, known_names=known_names
        )
        for i, line in enumerate(lines)
    ]
    return sorted(
        (s for s in scored if s.score >= threshold),
        key=lambda s: (-s.score, s.line_index),
    )
