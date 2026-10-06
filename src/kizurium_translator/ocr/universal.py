"""Universal OCR core

Contract:

```text
screen → region → OCR → reconstruct → clipboard
```

Forbidden in this mode (live overlay may still do them):

```text
translation
glossary rewrite
live / game UI heuristics
```

Language coverage comes from whichever backends/models are installed
(Tesseract lang packs, RapidOCR, MeikiOCR) — not a hardcoded EN+JA gate.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from PIL import Image

from ..core.text import engine_enabled
from .engine import is_garbage_ocr, ocr_with
from .routing import FALLBACK, JP_SPECIALIST, PRIMARY, available_engines

# Preferred Tesseract packs when present. Order is preference, not a hard gate.
_TESS_PREFERRED = (
    "eng",
    "rus",
    "jpn",
    "chi_sim",
    "chi_tra",
    "kor",
    "deu",
    "fra",
    "spa",
    "ita",
    "por",
    "ukr",
)


def installed_tesseract_langs() -> tuple[str, ...]:
    """Lang codes reported by ``tesseract --list-langs``."""
    exe = shutil.which("tesseract")
    if not exe:
        return ()
    try:
        res = subprocess.run(
            [exe, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    lines = [ln.strip() for ln in (res.stdout or "").splitlines()]
    out = [ln for ln in lines[1:] if ln and re.fullmatch(r"[a-z0-9_]+", ln)]
    return tuple(out)


def tesseract_lang_string(installed: tuple[str, ...] | None = None) -> str:
    """Build a ``eng+rus+jpn…`` string from what is actually installed."""
    have = set(installed if installed is not None else installed_tesseract_langs())
    picked = [code for code in _TESS_PREFERRED if code in have]
    if not picked and have:
        picked = sorted(have)[:6]
    return "+".join(picked) if picked else "eng"


def _cjk_ratio(text: str) -> float:
    body = text.replace(" ", "")
    if not body:
        return 0.0
    cjk = sum(
        1
        for ch in body
        if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af"
    )
    return cjk / len(body)


def _lines_look_cjk(lines: list[dict]) -> bool:
    blob = " ".join(str(p.get("text", "")) for p in lines)
    if _cjk_ratio(blob) >= 0.12:
        return True
    return sum(1 for p in lines if _cjk_ratio(str(p.get("text", ""))) >= 0.3) >= 2


def _cyr_ratio(text: str) -> float:
    body = (text or "").replace(" ", "")
    if not body:
        return 0.0
    cyr = sum(1 for ch in body if "\u0400" <= ch <= "\u04ff")
    return cyr / len(body)


def _lat_ratio(text: str) -> float:
    body = (text or "").replace(" ", "")
    if not body:
        return 0.0
    lat = sum(1 for ch in body if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    return lat / len(body)


def _y_mid(p: dict) -> float:
    box = p.get("box") or (0, 0, 0, 12)
    return (int(box[1]) + int(box[3])) / 2.0


def _x1(p: dict) -> int:
    return int((p.get("box") or (0, 0, 0, 0))[0])


def _is_clipboard_junk(text: str, *, allow_cjk: bool) -> bool:
    """Drop icon-soup / mojibake / chrome that contaminates OCR-copy."""
    t = (text or "").strip()
    if len(t) < 2:
        return True
    # Keep code-ish tokens (content_revision) that live ink-band filters kill.
    if re.fullmatch(r"[A-Za-z][\w.]*\)?\.?", t):
        return False
    from .engine import is_desktop_chrome, looks_like_ocr_mojibake_of_russian

    if is_desktop_chrome(t) or is_garbage_ocr(t):
        return True
    if looks_like_ocr_mojibake_of_russian(t):
        return True
    # Cursor footer / reaction chrome.
    if re.fullmatch(r"[□■▪▫◉●○]\s*\d*\s*m?\s*ago", t, re.I):
        return True
    if re.search(r"\b\d+m\s*ago\b", t, re.I) and len(t) <= 16:
        return True
    # Circled digits / CJK icon OCR when the page is EN/RU.
    if not allow_cjk and _cjk_ratio(t) >= 0.4 and _cyr_ratio(t) < 0.1 and _lat_ratio(t) < 0.3:
        return True
    if re.fullmatch(r"[\u2460-\u24ff\u25a0-\u25ff\u3000-\u303f\s]+", t):
        return True
    # Lone "Edited" / "+283" scraps Tess splits off a longer Rapid line.
    if re.fullmatch(r"Edited(\s+\d+)?", t, re.I):
        return True
    if re.fullmatch(r"[+＋]?\s*[\d①-⑨\u2460-\u2473]+(\s*[-–]\s*[\d①-⑨\u2460-\u2473]+)?", t):
        return True
    return False


def _line_quality(text: str) -> tuple[float, int]:
    """Higher is better: Cyrillic/Latin substance, length; penalise CJK soup."""
    t = (text or "").strip()
    cyr = _cyr_ratio(t)
    lat = _lat_ratio(t)
    cjk = _cjk_ratio(t)
    score = 2.5 * cyr + 1.5 * lat + 0.02 * len(t) - 2.0 * cjk
    if looks_like_broken_rapid_cyrillic(t):
        score -= 3.0
    return score, len(t)


def looks_like_broken_rapid_cyrillic(text: str) -> bool:
    """Rapid often turns Russian into Latin soup with odd punctuation."""
    t = (text or "").strip()
    if not t or _cyr_ratio(t) >= 0.2:
        return False
    from .engine import looks_like_ocr_mojibake_of_russian

    if looks_like_ocr_mojibake_of_russian(t):
        return True
    # Short Latin scraps where Russian was expected (Oc -e / Ck bio / a 37:).
    if len(t) <= 24 and _lat_ratio(t) >= 0.5 and t.count(" ") <= 3:
        if re.search(r"(^|\s)(a|φa|Oc|Ck|abwe)\b", t) or "φ" in t or "ー" in t:
            return True
    return False


def _stitch_horizontal(lines: list[dict], *, y_tol: int = 10) -> list[dict]:
    """Join same-row fragments Tess splits (union… + pad 4-8).)."""
    if len(lines) < 2:
        return list(lines)
    ordered = sorted(lines, key=lambda p: (_y_mid(p), _x1(p)))
    out: list[dict] = []
    cur = dict(ordered[0])
    for nxt in ordered[1:]:
        cy, ny = _y_mid(cur), _y_mid(nxt)
        if abs(cy - ny) > y_tol:
            out.append(cur)
            cur = dict(nxt)
            continue
        cx2 = int((cur.get("box") or (0, 0, 0, 0))[2])
        nx1 = _x1(nxt)
        gap = nx1 - cx2
        # Same baseline: Tess often splits a long UI line with a wide hole.
        line_w = max(40, cx2 - int((cur.get("box") or (0, 0, 0, 0))[0]))
        max_gap = max(180, int(0.55 * line_w))
        if 0 <= gap <= max_gap or (gap < 0 and abs(gap) < 40):
            a = str(cur.get("text", "")).rstrip()
            b = str(nxt.get("text", "")).lstrip()
            cur["text"] = f"{a} {b}".strip() if a and b else (a or b)
            box = cur.get("box") or (0, 0, 0, 12)
            nbox = nxt.get("box") or box
            cur["box"] = (
                min(int(box[0]), int(nbox[0])),
                min(int(box[1]), int(nbox[1])),
                max(int(box[2]), int(nbox[2])),
                max(int(box[3]), int(nbox[3])),
            )
            cur["conf"] = max(float(cur.get("conf", 0) or 0), float(nxt.get("conf", 0) or 0))
            continue
        out.append(cur)
        cur = dict(nxt)
    out.append(cur)
    return out


def _drop_contained_fragments(lines: list[dict]) -> list[dict]:
    """Drop 'Edited' when 'Edited 2 files…' already exists nearby."""
    texts = [(i, str(p.get("text", "")).strip()) for i, p in enumerate(lines)]
    drop: set[int] = set()
    for i, ti in texts:
        if len(ti) < 4:
            drop.add(i)
            continue
        for j, tj in texts:
            if i == j or j in drop:
                continue
            if len(ti) + 4 < len(tj) and ti.casefold() in tj.casefold():
                # Same-ish row or fragment is clearly a subset.
                if abs(_y_mid(lines[i]) - _y_mid(lines[j])) <= 28:
                    drop.add(i)
                    break
    return [p for i, p in enumerate(lines) if i not in drop]


def _merge_line_sets(primary: list[dict], extras: list[dict]) -> list[dict]:
    """Merge Rapid+Tess for clipboard: keep Russian Tess, EN Rapid, drop junk."""
    allow_cjk = _lines_look_cjk(primary) or _lines_look_cjk(extras)
    rapid = _stitch_horizontal(
        [p for p in primary if not _is_clipboard_junk(str(p.get("text", "")), allow_cjk=allow_cjk)]
    )
    tess = _stitch_horizontal(
        [p for p in extras if not _is_clipboard_junk(str(p.get("text", "")), allow_cjk=allow_cjk)]
    )

    # Bucket by coarse y; pick the better reading per band.
    bands: dict[int, list[dict]] = {}
    for p in rapid:
        copy = dict(p)
        copy["_src"] = "rapid"
        bands.setdefault(int(_y_mid(copy) // 16), []).append(copy)
    for p in tess:
        copy = dict(p)
        copy["_src"] = "tess"
        bands.setdefault(int(_y_mid(copy) // 16), []).append(copy)

    chosen: list[dict] = []
    for key in sorted(bands):
        group = bands[key]
        # Also peek neighbouring bands for near-duplicates.
        near = list(group)
        for k in (key - 1, key + 1):
            near.extend(bands.get(k, []))
        best = max(group, key=lambda p: _line_quality(str(p.get("text", ""))))
        # If Rapid soup and Tess has Cyrillic in this/near band, prefer Tess.
        if best.get("_src") == "rapid" and looks_like_broken_rapid_cyrillic(str(best.get("text", ""))):
            tess_alt = [
                p
                for p in near
                if p.get("_src") == "tess" and _cyr_ratio(str(p.get("text", ""))) >= 0.25
            ]
            if tess_alt:
                best = max(tess_alt, key=lambda p: _line_quality(str(p.get("text", ""))))
        # Prefer longer EN Rapid over Tess scraps on the same chrome line.
        if best.get("_src") == "tess" and _lat_ratio(str(best.get("text", ""))) >= 0.6:
            rapid_alt = [
                p
                for p in near
                if p.get("_src") == "rapid"
                and len(str(p.get("text", ""))) > len(str(best.get("text", ""))) + 8
                and not looks_like_broken_rapid_cyrillic(str(p.get("text", "")))
            ]
            if rapid_alt:
                best = max(rapid_alt, key=lambda p: _line_quality(str(p.get("text", ""))))
        chosen.append({k: v for k, v in best.items() if k != "_src"})

    # De-dupe near-identical chosen lines across adjacent bands.
    chosen = _drop_contained_fragments(chosen)
    deduped: list[dict] = []
    for p in sorted(chosen, key=lambda q: (_y_mid(q), _x1(q))):
        t = str(p.get("text", "")).strip()
        if not t:
            continue
        if deduped:
            prev = deduped[-1]
            pt = str(prev.get("text", "")).strip()
            if abs(_y_mid(prev) - _y_mid(p)) <= 18 and (
                t.casefold() == pt.casefold()
                or (t.casefold() in pt.casefold() and len(t) + 6 < len(pt))
                or (pt.casefold() in t.casefold() and len(pt) + 6 < len(t))
            ):
                # Keep the higher-quality reading.
                if _line_quality(t) > _line_quality(pt):
                    deduped[-1] = p
                continue
        deduped.append(p)
    return deduped


def _run_rapid(region_img: Image.Image) -> list[dict]:
    from ..live.reconcile import rapid_ocr_lines

    # Clipboard regions are often tall IDE/chat crops — keep more pixels than live.
    return list(rapid_ocr_lines(region_img, max_side=2400) or [])


def _run_meiki(region_img: Image.Image) -> list[dict]:
    from ..translation.service import meiki_ocr_lines

    return list(meiki_ocr_lines(region_img) or [])


def _run_tesseract(region_img: Image.Image, langs: str | None = None) -> list[dict]:
    if not engine_enabled("tesseract"):
        return []
    lang = langs or tesseract_lang_string()
    try:
        # psm=None → adaptive geometry
        return list(ocr_with(region_img, lang, psm=None) or [])
    except Exception:  # noqa: BLE001 - soft miss
        return []


def _coverage_thin(region_img: Image.Image, lines: list[dict]) -> bool:
    """True when the first engine almost certainly missed body text."""
    from ..core.text import ocr_coverage_stats

    w, h = region_img.size
    st = ocr_coverage_stats(lines, w, h)
    if st["sparse"]:
        return True
    # Large selection with only a couple of hits → typical Cursor/IDE miss.
    if h >= 160 and st["n"] <= 2:
        return True
    if max(w, h) >= 400 and st["n"] <= 4 and st["coverage"] < 0.35:
        return True
    return False


def universal_ocr_lines(region_img: Image.Image) -> list[dict[str, Any]]:
    """Recognise a region without live/game heuristics or glossary."""
    if region_img is None:
        return []
    from .preprocess import (
        prepare_for_ocr,
        result_looks_unusable,
        scale_boxes_down,
    )

    work, scale = prepare_for_ocr(region_img)
    lines = _recognize_on(work)
    lines = scale_boxes_down(lines, scale)

    # if tiny text still failed after 2×, retry once at 4×.
    if result_looks_unusable(lines) and scale < 4:
        work4, scale4 = prepare_for_ocr(region_img, still_bad=True, previous_scale=max(2, scale))
        if scale4 > scale:
            again = scale_boxes_down(_recognize_on(work4), scale4)
            if not result_looks_unusable(again):
                lines = again

    allow_cjk = _lines_look_cjk(lines)
    keep: list[dict[str, Any]] = []
    for p in lines:
        text = str(p.get("text", "")).strip()
        if not text or _is_clipboard_junk(text, allow_cjk=allow_cjk):
            continue
        keep.append(dict(p))
    return _drop_contained_fragments(keep)


def _recognize_on(region_img: Image.Image) -> list[dict]:
    """One recognition pass on an (already prepared) image."""
    avail = set(available_engines())
    lines: list[dict] = []

    if PRIMARY in avail:
        lines = _run_rapid(region_img)

    if JP_SPECIALIST in avail and (not lines or _lines_look_cjk(lines)):
        meiki = _run_meiki(region_img)
        if meiki:
            if lines and _lines_look_cjk(lines):
                latin = [p for p in lines if _cjk_ratio(str(p.get("text", ""))) < 0.2]
                lines = latin + meiki
            elif not lines:
                lines = meiki

    # Tess fills Cyrillic / missed body. Skip CJK packs unless Rapid already saw CJK —
    # eng+jpn+chi on a Cursor chat hallucinates 口ロマ over reaction icons.
    if FALLBACK in avail:
        if lines and not _lines_look_cjk(lines):
            have = set(installed_tesseract_langs())
            compact = "+".join(c for c in ("eng", "rus", "ukr") if c in have) or "eng"
            tess = _run_tesseract(region_img, compact)
        else:
            tess = _run_tesseract(region_img)
        if tess:
            lines = tess if not lines else _merge_line_sets(lines, tess)
    return lines


def reconstruct_plaintext(lines: list[dict]) -> str:
    """Reading order + safe normalize + paragraph merge → clipboard text."""
    from .postprocess import postprocess_ocr_lines

    return postprocess_ocr_lines(lines)


def universal_ocr_text(region_img: Image.Image) -> str:
    """Full pipeline: OCR → reconstruct (no translate/glossary)."""
    return reconstruct_plaintext(universal_ocr_lines(region_img))
