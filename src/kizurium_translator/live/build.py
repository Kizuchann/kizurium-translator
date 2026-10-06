"""Сборка блоков кадра из переводов.

Тело цикла кадра решает, что нарисовать, и решало это вперемешку с решением о
самом кадре - 63 строки и пять выходов внутри `worker()`. Здесь
ровно то, чем карточка наполняется: что перевод отбросить, что поправить и
чем нарисовать. Чтение экрана и решение о кадре остаются в сессии.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from PIL import Image

from ..core.text import box_overlap_ratio, strip_watermark_tail, tdetail, tlog
from ..layout.grouping import is_mostly_russian
from ..ocr.engine import is_garbage_ocr, skip_source
from ..render.cards import make_block
from ..translation.service import is_translation_error, same_line

_RU_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def _is_transliteration(source: str, translated: str) -> bool:
    """The translation only spells the source in Cyrillic letters.

    That is what a translator returns for a string with no meaning in the
    source language - a Russian word the Latin recogniser read as Latin
    homoglyphs ("Cnoapb" for "Словарь"), or a name.
    """
    src = re.sub(r"[^a-z]", "", source.lower())
    back = "".join(_RU_TO_LAT.get(c, c) for c in translated.lower())
    back = re.sub(r"[^a-z]", "", back)
    if len(src) < 3 or not back:
        return False
    return SequenceMatcher(None, src, back).ratio() >= 0.6


def _already_russian(par: dict, region_img: Image.Image | None) -> bool:
    """The box reads as Russian to the Cyrillic recogniser."""
    if region_img is None:
        return False
    from ..live.reconcile import rapid_ocr_lines_cyrillic

    x1, y1, x2, y2 = (int(v) for v in par["box"])
    pad = 4
    crop = region_img.crop(
        (max(0, x1 - pad), max(0, y1 - pad), min(region_img.width, x2 + pad), min(region_img.height, y2 + pad))
    )
    # Tesseract reads screen-sized UI type poorly; at twice the size it reads it.
    f = max(2.0, 64.0 / max(1, crop.height))
    crop = crop.resize((max(1, int(crop.width * f)), max(1, int(crop.height * f))), Image.LANCZOS)
    try:
        read = rapid_ocr_lines_cyrillic(crop)
    except Exception:  # noqa: BLE001
        return False
    cyr = sum(1 for p in read for c in str(p.get("text", "")) if "\u0400" <= c <= "\u04ff")
    letters = sum(1 for c in str(par.get("text", "")) if c.isalpha())
    if cyr < max(2, int(letters * 0.5)):
        return False
    # Latin capitals are Cyrillic homoglyphs, so a Latin name reads as Cyrillic
    # too ("Buro" as "Вшго"). Which script it is shows in how sure each
    # reading is of itself.
    from ..ocr.engine import ocr_with

    def conf(lines: list[dict]) -> float:
        vals = [float(p.get("conf", 0) or 0) for p in lines]
        return sum(vals) / len(vals) if vals else 0.0

    try:
        latin = ocr_with(crop, "eng", psm="6")
    except Exception:  # noqa: BLE001
        return True
    return conf(read) >= conf(latin) + 3.0


def _drop_overlapping(pairs: list[tuple[dict, str]]) -> list[tuple[dict, str]]:
    """One card per place on the screen.

    The recogniser reads a row of chips both as the row and as its pieces, and
    reads the same button twice from two passes. Two cards in one place are
    drawn over each other and neither reads. A box holding two or more others
    is the row; of two boxes that are the same place, the surer one stays.
    """
    boxes = [tuple(int(v) for v in par["box"]) for par, _ in pairs]

    def inside(a, b) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        iw = max(0, min(ax2, bx2) - max(ax1, bx1))
        ih = max(0, min(ay2, by2) - max(ay1, by1))
        area = max(1, (ax2 - ax1) * (ay2 - ay1))
        return iw * ih >= area * 0.8

    drop: set[int] = set()
    for i, bi in enumerate(boxes):
        held = sum(1 for j, bj in enumerate(boxes) if j != i and inside(bj, bi))
        if held >= 2:
            drop.add(i)
    for i, bi in enumerate(boxes):
        if i in drop:
            continue
        for j in range(i + 1, len(boxes)):
            if j in drop:
                continue
            bj = boxes[j]
            ai = (bi[2] - bi[0]) * (bi[3] - bi[1])
            aj = (bj[2] - bj[0]) * (bj[3] - bj[1])
            same_text = (
                SequenceMatcher(
                    None, str(pairs[i][0].get("text", "")), str(pairs[j][0].get("text", ""))
                ).ratio()
                >= 0.6
            )
            if same_text and box_overlap_ratio(bi, bj) >= 0.7 and min(ai, aj) >= max(ai, aj) * 0.6:
                ci = float(pairs[i][0].get("conf", 0) or 0)
                cj = float(pairs[j][0].get("conf", 0) or 0)
                drop.add(j if ci >= cj else i)
    if drop:
        tdetail(f"drop-overlap n={len(drop)}")
    return [pair for k, pair in enumerate(pairs) if k not in drop]


def build_blocks(
    pairs: Iterable[tuple[dict, str]],
    *,
    japanese_mode: bool,
    dialogue_mode: bool,
    eng_ui_mode: bool,
    region_img: Image.Image,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
) -> list[dict]:
    """Карточки кадра из готовых переводов, в том же порядке и числе."""
    blocks: list[dict] = []
    placed = _drop_overlapping(list(pairs))
    for par, translated in placed:
        # Текст с экрана и перевод в tlog не пишутся: этот вызов идёт на
        # каждой строке каждого кадра, и в логе он оказывается целиком.
        # Разбор идёт через tdetail, который включается переменной
        # KIZURIUM_TRANSLATOR_DEBUG.
        tlog(
            f"make-block-call kind={par.get('kind')} "
            f"src_chars={len(str(par.get('text','')))} "
            f"dst_chars={len(translated)}"
        )
        tdetail(
            f"make-block-call kind={par.get('kind')} "
            f"text={str(par.get('text',''))[:40]!r} translated={str(translated)[:60]!r}"
        )
        if not translated or same_line(par["text"], translated):
            tlog(f"drop-empty-or-same src_chars={len(str(par.get('text','')))}")
            tdetail(f"drop-empty-or-same {str(par.get('text',''))[:40]!r}")
            continue
        if (
            not japanese_mode
            and _is_transliteration(str(par.get("text", "")), translated)
            and _already_russian(par, region_img)
        ):
            tdetail(f"drop-already-russian {str(par.get('text',''))[:40]!r}")
            continue
        if not japanese_mode and skip_source(par["text"]):
            tlog(f"drop-skip-source src_chars={len(str(par.get('text','')))}")
            tdetail(f"drop-skip-source {str(par.get('text',''))[:40]!r}")
            continue
        translated = re.sub(r"@@KZT\d+@@\s*", "", translated).strip()
        # watermark по строкам
        translated = "\n".join(strip_watermark_tail(ln) for ln in translated.split("\n"))
        # Iori часто становится Tori/lori в OCR/переводе
        src0 = str(par.get("text", "")).split("\n", 1)[0]
        if re.match(r"^Iori\s*:", src0, re.I):
            translated = re.sub(r"^(Tori|lori|Lori)\s*:", "Iori:", translated, flags=re.I)
        # is_garbage_ocr — для EN OCR-каши, НЕ для нормального RU-перевода.
        # Проверка на регистр отсекала целые русские блоки вроде
        # «СМЕРТЬ БЫЛА…», и на экране не оставалось ничего.
        if not translated or is_translation_error(str(par.get("text", "")), translated):
            tdetail(f"drop-bad-mt '{str(par.get('text',''))[:40]}' -> '{translated[:40]}'")
            continue
        if (
            not dialogue_mode
            and not japanese_mode
            and not is_mostly_russian(translated)
            and is_garbage_ocr(translated)
        ):
            tdetail(f"drop-garbage-mt '{translated[:40]}'")
            continue
        if skip_source(translated) and is_mostly_russian(par["text"]):
            continue
        if dialogue_mode and (par.get("line_boxes") or str(par.get("text", "")).count("\n") >= 1):
            blocks.extend(
                expand_subtitle_line_blocks(par, translated, region_img, rx, ry, rw, rh)
            )
        elif (
            eng_ui_mode
            and str(par.get("kind", "")) == "dialogue"
            and (
                len(par.get("line_boxes") or []) >= 2
                or str(par.get("text", "")).count("\n") >= 1
            )
        ):
            # VN-пузырь: раскладка по реальным OCR-строкам / переносам
            blocks.extend(
                expand_subtitle_line_blocks(par, translated, region_img, rx, ry, rw, rh)
            )
        else:
            blocks.append(make_block(par, translated, region_img, rx, ry, rw, rh))
    _even_list_sizes(blocks)
    return blocks


def _even_list_sizes(blocks: list[dict]) -> None:
    """One size for a column of labels set in one size.

    A list on a panel - a map legend, a settings column - is one style, and
    the size measured off each row is that size plus whatever the row's
    pixels add: a descender, a stripe of artwork crossing the box. Measured
    row by row the legend came out at 14, 17, 24 and 23 for what is one size
    on screen, and that is what a reader sees first. The median of the
    column is the size; a row that differs from it is the measurement's
    error, not the game's.
    """
    rows = [
        b
        for b in blocks
        if b.get("tight")
        and b.get("oneline")
        and not b.get("fill_width")
        and abs(float(b.get("angle", 0.0) or 0.0)) < 1.0
        and str(b.get("kind") or "") not in ("dialogue", "body")
    ]
    rows.sort(key=lambda b: (int(b["y"]), int(b["x"])))
    groups: list[list[dict]] = []
    for b in rows:
        h_b = float(b.get("src_h") or 1)
        for group in groups:
            last = group[-1]
            h = float(last.get("src_h") or 1)
            cx_b = int(b["x"]) + float(b.get("src_w") or 0) / 2.0
            cx0 = int(group[0]["x"]) + float(group[0].get("src_w") or 0) / 2.0
            # Choice bars in a catscene are centred as a column: their left
            # edges wander with the text length, but their centres line up.
            if abs(int(b["x"]) - int(group[0]["x"])) > 6 and abs(cx_b - cx0) > 48:
                continue
            if abs(h_b - h) > h * 0.25:
                continue
            gap = int(b["y"]) - (int(last["y"]) + int(h))
            if 0 <= gap <= h * 2.5:
                group.append(b)
                break
        else:
            groups.append([b])
    evened: set[int] = set()
    for group in groups:
        if len(group) < 3:
            continue
        fonts = sorted(int(b["font"]) for b in group)
        size = fonts[len(fonts) // 2]
        if fonts[0] != fonts[-1]:
            tdetail(f"even-list n={len(group)} fonts={fonts} -> {size}")
        # The same column has one left edge and one weight; per-row
        # measurement wobbles both by a few pixels and a stroke class.
        left = min(int(b["x"]) for b in group)
        weights = [b.get("weight") for b in group if b.get("weight") is not None]
        weight = max(set(weights), key=weights.count) if weights else None
        for b in group:
            b["font"] = size
            shift = int(b["x"]) - left
            if shift:
                b["x"] = left
                b["src_w"] = int(b.get("src_w") or 0) + shift
            if weight is not None:
                b["weight"] = weight
            evened.add(id(b))
    # A row of tabs or buttons is the same thing laid out across: one style,
    # and on a tab bar the sizes measured came out 21, 26, 27 and 31.
    across = sorted((b for b in rows if id(b) not in evened), key=lambda b: (int(b["x"]), int(b["y"])))
    lanes: list[list[dict]] = []
    for b in across:
        h_b = float(b.get("src_h") or 1)
        cy_b = int(b["y"]) + h_b / 2.0
        for lane in lanes:
            last = lane[-1]
            h = float(last.get("src_h") or 1)
            if abs(h_b - h) > h * 0.25:
                continue
            if abs(cy_b - (int(last["y"]) + h / 2.0)) > h * 0.2:
                continue
            gap = int(b["x"]) - (int(last["x"]) + int(last.get("src_w") or 0))
            if 0 <= gap <= h * 3.0:
                lane.append(b)
                break
        else:
            lanes.append([b])
    for lane in lanes:
        if len(lane) < 3:
            continue
        fonts = sorted(int(b["font"]) for b in lane)
        size = fonts[len(fonts) // 2]
        if fonts[0] != fonts[-1]:
            tdetail(f"even-row n={len(lane)} fonts={fonts} -> {size}")
        for b in lane:
            b["font"] = size
    # Choice bars with a skipped "..." in the middle leave a gap larger than
    # two and a half heights; still one list of replies.
    leftover = [b for b in rows if id(b) not in evened]
    leftover.sort(key=lambda b: (int(b["y"]), int(b["x"])))
    stacks: list[list[dict]] = []
    for b in leftover:
        h_b = float(b.get("src_h") or 1)
        cx_b = int(b["x"]) + float(b.get("src_w") or 0) / 2.0
        for stack in stacks:
            last = stack[-1]
            h = float(last.get("src_h") or 1)
            cx0 = int(stack[0]["x"]) + float(stack[0].get("src_w") or 0) / 2.0
            if abs(h_b - h) > h * 0.3:
                continue
            if abs(cx_b - cx0) > 80:
                continue
            gap = int(b["y"]) - (int(last["y"]) + int(h))
            if 0 <= gap <= h * 6.0:
                stack.append(b)
                break
        else:
            stacks.append([b])
    for stack in stacks:
        if len(stack) < 2:
            continue
        fonts = sorted(int(b["font"]) for b in stack)
        size = fonts[len(fonts) // 2]
        if fonts[0] != fonts[-1]:
            tdetail(f"even-stack n={len(stack)} fonts={fonts} -> {size}")
        for b in stack:
            b["font"] = size


def expand_subtitle_line_blocks(
    par: dict,
    translated: str,
    region_img: Image.Image,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
) -> list[dict]:
    """2+ строки субтитров → отдельные карточки по OCR bbox каждой строки (разные ширины/Y)."""
    line_boxes = [
        lb
        for lb in list(par.get("line_boxes") or [])
        if not is_subtitle_junk_line(str(lb.get("text", "")), lb.get("box"), rw, rh)
    ]
    if len(line_boxes) < 2 and str(par.get("text", "")).count("\n") >= 1:
        parts = [p.strip() for p in str(par["text"]).split("\n") if p.strip()]
        if len(parts) >= 2:
            x1, y1, x2, y2 = par["box"]
            n = len(parts)
            slice_h = max(12, (y2 - y1) // n)
            line_boxes = []
            for i, ep in enumerate(parts):
                ly1 = y1 + i * slice_h
                ly2 = y2 if i == n - 1 else y1 + (i + 1) * slice_h - 2
                line_boxes.append(
                    {
                        "text": ep,
                        "box": (x1, ly1, x2, ly2),
                        "line_height": max(12, ly2 - ly1),
                    }
                )
    if len(line_boxes) < 2:
        # одна реальная строка — одна плашка на весь перевод (без сирот в пустоте)
        flat = re.sub(r"\s+", " ", str(translated or "").replace("\n", " ")).strip()
        if line_boxes:
            par = dict(par)
            par["box"] = line_boxes[0]["box"]
            par["text"] = line_boxes[0].get("text", par.get("text", ""))
            par["line_boxes"] = []
            par["kind"] = "dialogue"
            par["pin_box"] = False
        return [make_block(par, flat, region_img, rx, ry, rw, rh)]
    tr_lines = [ln.strip() for ln in str(translated or "").split("\n")]
    while len(tr_lines) < len(line_boxes):
        tr_lines.append("")
    if len(tr_lines) > len(line_boxes):
        tr_lines = tr_lines[: len(line_boxes) - 1] + [" ".join(tr_lines[len(line_boxes) - 1:])]
    # пустой слот при 2+ OCR-строках — перетянуть слово с соседа, НЕ схлопывать в одну
    for i in range(len(tr_lines)):
        if tr_lines[i]:
            continue
        donor = i - 1 if i > 0 and tr_lines[i - 1] else (i + 1 if i + 1 < len(tr_lines) else -1)
        if donor < 0:
            continue
        dw = tr_lines[donor].split()
        if len(dw) >= 2:
            if donor < i:
                tr_lines[i] = dw[-1]
                tr_lines[donor] = " ".join(dw[:-1])
            else:
                tr_lines[i] = dw[0]
                tr_lines[donor] = " ".join(dw[1:])
            tlog(f"sub-card-fill-empty[{i}] from[{donor}]")
    out: list[dict] = []
    for i, (lb, tr) in enumerate(zip(line_boxes, tr_lines)):
        if not tr:
            tlog(f"sub-card[{i}] SKIP empty tr y={lb['box'][1]}")
            continue
        line_par = {
            "text": lb.get("text", ""),
            "box": lb["box"],
            "line_height": lb.get("line_height", par.get("line_height", 16)),
            "kind": "dialogue-line",
            "pin_box": True,
            "wrap": False,
            "oneline": True,
            "angle": lb.get("angle", par.get("angle", 0)),
            "conf": lb.get("conf", par.get("conf", 0)),
        }
        out.append(make_block(line_par, tr, region_img, rx, ry, rw, rh))
        bx1, by1, bx2, by2 = lb["box"]
        tlog(f"sub-card[{i}] pin={bx2-bx1}x{by2-by1} y={by1} '{tr[:42]}'")
    if len(out) < 1:
        return [make_block(par, translated, region_img, rx, ry, rw, rh)]
    if len(out) < len(line_boxes):
        tlog(f"sub-card-warn got={len(out)} want={len(line_boxes)}")
    return out
