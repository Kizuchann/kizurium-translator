"""game profiles as DATA, not `if game == …` branches.

Profiles live under ``data/profiles/<id>.toml``. Core code reads fields from
the active profile (gap multipliers, OCR order, timeouts). The profile id is
never compared to a game name inside layout/OCR algorithms.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "profiles"


@dataclass(frozen=True)
class UiZone:
    """Named rectangle hint (fraction of frame or absolute px — see ``unit``)."""

    name: str
    x: float
    y: float
    w: float
    h: float
    unit: str = "frac"  # "frac" 0..1 of frame, or "px"


@dataclass(frozen=True)
class SpeakerHint:
    """Layout priors for name|gap|body dialogues. Numbers only — no game names."""

    enabled: bool = True
    gap_min_px: float = 16.0
    gap_glyph_mult: float = 1.5


@dataclass(frozen=True)
class Timeouts:
    interval: float | None = None
    interval_sub: float | None = None
    interval_ui_stable: float | None = None


@dataclass(frozen=True)
class GameProfile:
    """One selectable profile. Missing optional fields keep engine defaults."""

    id: str
    lexicon_pack: str = ""
    preferred_ocr: tuple[str, ...] = ()
    ui_zones: tuple[UiZone, ...] = ()
    speaker: SpeakerHint = field(default_factory=SpeakerHint)
    dialogue_prior: str = ""  # free-form note for future layout priors
    font_hint: str = ""
    timeouts: Timeouts = field(default_factory=Timeouts)
    enable_game_glossary: bool = True


_ACTIVE: GameProfile | None = None


def _parse_zone(raw: object) -> UiZone | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    try:
        return UiZone(
            name=name,
            x=float(raw["x"]),
            y=float(raw["y"]),
            w=float(raw["w"]),
            h=float(raw["h"]),
            unit=str(raw.get("unit") or "frac"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_profile(path: Path, data: dict) -> GameProfile:
    pid = str(data.get("id") or path.stem).strip()
    speaker_raw = data.get("speaker") if isinstance(data.get("speaker"), dict) else {}
    timeouts_raw = data.get("timeouts") if isinstance(data.get("timeouts"), dict) else {}
    zones_raw = data.get("ui_zones") if isinstance(data.get("ui_zones"), list) else []
    ocr_raw = data.get("preferred_ocr") if isinstance(data.get("preferred_ocr"), list) else []
    zones = tuple(z for z in (_parse_zone(r) for r in zones_raw) if z is not None)
    speaker = SpeakerHint(
        enabled=bool(speaker_raw.get("enabled", True)),
        gap_min_px=float(speaker_raw.get("gap_min_px", 16.0)),
        gap_glyph_mult=float(speaker_raw.get("gap_glyph_mult", 1.5)),
    )
    timeouts = Timeouts(
        interval=float(timeouts_raw["interval"]) if "interval" in timeouts_raw else None,
        interval_sub=(
            float(timeouts_raw["interval_sub"]) if "interval_sub" in timeouts_raw else None
        ),
        interval_ui_stable=(
            float(timeouts_raw["interval_ui_stable"])
            if "interval_ui_stable" in timeouts_raw
            else None
        ),
    )
    return GameProfile(
        id=pid,
        lexicon_pack=str(data.get("lexicon_pack") or pid).strip(),
        preferred_ocr=tuple(str(x) for x in ocr_raw if str(x).strip()),
        ui_zones=zones,
        speaker=speaker,
        dialogue_prior=str(data.get("dialogue_prior") or "").strip(),
        font_hint=str(data.get("font_hint") or "").strip(),
        timeouts=timeouts,
        enable_game_glossary=bool(data.get("enable_game_glossary", True)),
    )


def load_profile_file(path: Path) -> GameProfile | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _parse_profile(path, data)


@lru_cache(maxsize=1)
def all_profiles(*, root: Path | None = None) -> dict[str, GameProfile]:
    """id → profile from ``data/profiles/*.toml`` (and optional override root)."""
    base = root if root is not None else DATA_DIR
    out: dict[str, GameProfile] = {}
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.toml")):
        prof = load_profile_file(path)
        if prof is not None:
            out[prof.id] = prof
    return out


def get(profile_id: str, *, root: Path | None = None) -> GameProfile | None:
    key = (profile_id or "").strip()
    if not key:
        return None
    return all_profiles(root=root).get(key)


def active() -> GameProfile | None:
    return _ACTIVE


def clear_active() -> None:
    global _ACTIVE
    _ACTIVE = None


def set_active(profile_id: str | None, *, root: Path | None = None) -> GameProfile | None:
    """Select a profile by id. Empty/None clears. Unknown id clears and returns None."""
    global _ACTIVE
    if not profile_id or not str(profile_id).strip():
        _ACTIVE = None
        return None
    prof = get(str(profile_id).strip(), root=root)
    _ACTIVE = prof
    return prof


def resolve_requested_id(
    *,
    config_id: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Env ``KIZURIUM_TRANSLATOR_PROFILE`` wins over config."""
    environ: Mapping[str, str] = env if env is not None else os.environ
    from_env = str(environ.get("KIZURIUM_TRANSLATOR_PROFILE") or "").strip()
    if from_env:
        return from_env
    return str(config_id or "").strip()


def apply_to_config(cfg: Any, profile: GameProfile | None) -> Any:
    """Return a Config with profile timeouts / OCR order folded in.

    Does not mutate ``cfg``. Game-name branches stay out of the engine: only
    numeric/list fields from DATA are copied.
    """
    if profile is None:
        return cfg
    changes: dict = {}
    if profile.preferred_ocr:
        changes["ocr_engines"] = profile.preferred_ocr
    t = profile.timeouts
    if t.interval is not None:
        changes["interval"] = t.interval
    if t.interval_sub is not None:
        changes["interval_sub"] = t.interval_sub
    if t.interval_ui_stable is not None:
        changes["interval_ui_stable"] = t.interval_ui_stable
    if not changes:
        return cfg
    return replace(cfg, **changes)


def activate(profile_id: str | None, *, root: Path | None = None) -> GameProfile | None:
    """Select profile and turn game glossary on/off to match the profile.

    Glossary enablement is a boolean from DATA, not ``if id == "<game name>"``.
    """
    from ..translation import service as translation_service

    prof = set_active(profile_id, root=root)
    if prof is None:
        translation_service.apply_game_glossary(False)
        try:
            from ..core.text import tlog

            tlog("profile=off")
        except Exception:  # noqa: BLE001
            pass
        return None
    if prof.enable_game_glossary:
        translation_service.apply_game_glossary(True)
    else:
        translation_service.apply_game_glossary(False)
    try:
        from ..core.text import tlog

        tlog(
            f"profile={prof.id} pack={prof.lexicon_pack or prof.id} "
            f"ocr={','.join(prof.preferred_ocr) or '-'} zones={len(prof.ui_zones)}"
        )
    except Exception:  # noqa: BLE001
        pass
    return prof


def speaker_gap_params() -> tuple[float, float]:
    """(gap_min_px, gap_glyph_mult) from the active profile, else plan defaults."""
    prof = active()
    if prof is None or not prof.speaker.enabled:
        return 16.0, 1.5
    return float(prof.speaker.gap_min_px), float(prof.speaker.gap_glyph_mult)


def active_lexicon_pack() -> str | None:
    """Pack id for lexicon scope filtering, or None when no profile is active."""
    prof = active()
    if prof is None:
        return None
    pack = (prof.lexicon_pack or prof.id).strip()
    return pack or None


# Zone name → soft semantic role. Matching is by substring on the zone *name*,
# never by profile id / game title.
_ZONE_ROLE_TOKENS: tuple[tuple[str, str], ...] = (
    ("dialogue", "DIALOGUE"),
    ("body", "BODY"),
    ("speaker", "SPEAKER"),
    ("name", "SPEAKER"),
    ("choice", "CHOICE"),
    ("menu", "MENU"),
    ("control", "HUD"),
    ("hud", "HUD"),
    ("notif", "NOTIFICATION"),
    ("toast", "NOTIFICATION"),
    ("title", "TITLE"),
    # A strip the profile declares to be symbols rather than words.
    ("decor", "DECOR"),
    ("glyph", "DECOR"),
    ("rune", "DECOR"),
    ("sigil", "DECOR"),
)


def zone_contains(
    zone: UiZone,
    *,
    cx: float,
    cy: float,
    frame_w: float,
    frame_h: float,
) -> bool:
    """True when point (cx, cy) lies inside the zone rectangle."""
    fw = max(1.0, float(frame_w))
    fh = max(1.0, float(frame_h))
    if zone.unit == "px":
        x, y, w, h = zone.x, zone.y, zone.w, zone.h
    else:
        x, y, w, h = zone.x * fw, zone.y * fh, zone.w * fw, zone.h * fh
    return x <= cx <= x + w and y <= cy <= y + h


def zone_role_hint(
    box: tuple[int, int, int, int] | list[int],
    *,
    frame_w: float,
    frame_h: float,
) -> str | None:
    """Soft role from the active profile's UI zones, or None.

    Without an active profile this is always None — profiles do not auto-detect
    the game from pixels; the user (or config/env) must select one.
    """
    prof = active()
    if prof is None or not prof.ui_zones:
        return None
    try:
        x1, y1, x2, y2 = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
    except Exception:  # noqa: BLE001
        return None
    cx = (x1 + x2) * 0.5
    cy = (y1 + y2) * 0.5
    # A strip the profile calls decoration wins over every other zone it may
    # also fall into. The rune band under a boss name sits inside the boss
    # plate's rectangle, and first-match-wins handed the runes the role
    # SPEAKER - the one thing the profile was written to prevent.
    fallback: str | None = None
    for zone in prof.ui_zones:
        if not zone_contains(zone, cx=cx, cy=cy, frame_w=frame_w, frame_h=frame_h):
            continue
        name = zone.name.casefold()
        for token, role in _ZONE_ROLE_TOKENS:
            if token not in name:
                continue
            if role == "DECOR":
                return role
            if fallback is None:
                fallback = role
            break
    return fallback


def reset_cache_for_tests() -> None:
    """Drop the cached profile catalog (tests that write temp TOML files)."""
    all_profiles.cache_clear()
    clear_active()
