"""Дополнительные проходы распознавания: колонки, пузыри, тосты, хром.

Тела перенесены из `live/session.py` дословно. Полный кадр собирает
`ocr/frame.py`; здесь только те проходы, которые дочитывают то, что
основной проход потерял.
"""
from __future__ import annotations

import re
import time

from PIL import Image, ImageOps

from ..core.text import (
    RE_CJK,
    RE_LAT,
    _iou,
    _reads_as_prose,
    dialogue_looks_incomplete,
    is_hud_spam,
    is_subtitle_junk_line,
    log_ocr_coverage,
    looks_like_spoken_line,
    normalize_subtitle_ocr,
    soft_descan_panel,
    tdetail,
    tlog,
)
from ..layout import grouping
from ..layout.grouping import (
    _COL_OCR_CACHE_TTL,
    column_bounds_from_centers,
    column_x_bounds,
    image_column_gutters,
    is_desktop_chrome,
    looks_like_section_header,
    normalize_column_bounds,
    page_column_gutters,
    split_false_nav_merges,
)
from ..live.reconcile import (
    _reading_key,
    merge_overlapping_duplicates,
    rapid_ocr_lines,
)
from ..ocr.engine import (
    english_ocr_quality,
    is_garbage_ocr,
    is_overlay_echo_ocr,
    looks_like_gameplay_hud_line,
    looks_like_ocr_mojibake_of_russian,
    looks_like_subtitle_continue,
    tess_fill_sparse_ui,
)
from ..typography.metrics import (
    _merge_vn_band_cluster,
    dedupe_near_ui_lines,
    is_ink_band_garbage,
    looks_like_ui_prompt,
    merge_subtitle_cluster,
    ui_text_fingerprint,
    unglue_english,
)


def ocr_region_by_columns(
    region_img: Image.Image,
    seed_lines: list[dict] | None = None,
) -> list[dict] | None:
    """Широкий веб: OCR каждой колонки отдельно — не склеивает абзацы соседних колонок."""
    w, h = region_img.size
    from ..core.scale import wide_enough_for_columns

    if not wide_enough_for_columns(w, h):
        return None
    # грубый fingerprint кадра — не гоняем col-OCR повторно на том же экране
    try:
        thumb = region_img.resize((48, 16), Image.Resampling.BILINEAR).convert("L")
        fp = hash(thumb.tobytes())
    except Exception:
        fp = 0
    cache_key = (w, h, fp)
    now = time.monotonic()
    hit = grouping._COL_OCR_CACHE.get(cache_key)
    if hit and now - hit[0] < _COL_OCR_CACHE_TTL:
        tlog(f"col-ocr-cache hit age={now - hit[0]:.1f}s n={len(hit[1])}")
        return [dict(p) for p in hit[1]]

    seed = seed_lines or []
    bounds = column_bounds_from_centers(seed, w)
    if not bounds:
        gutters = page_column_gutters(seed, w)
        wide_seed = sum(
            1
            for p in seed
            if (int(p["box"][2]) - int(p["box"][0])) >= max(400, int(w * 0.38))
        )
        if wide_seed >= 1 or len(gutters) < 1:
            img_g = image_column_gutters(region_img)
            if img_g:
                gutters = img_g
        if len(gutters) >= 1:
            bounds = column_x_bounds(w, gutters)
    if not bounds or len(bounds) < 2:
        # НЕ выдумывать equal thirds на IDE/тёмном UI — это 3× OCR впустую
        tlog("col-ocr skip: no gutters/centers")
        return None
    bounds = normalize_column_bounds(bounds, w)
    all_lines: list[dict] = []
    for ci, (x0, x1) in enumerate(bounds):
        pad = max(8, int((x1 - x0) * 0.02))
        xs = max(0, x0 - pad)
        xe = min(w, x1 + pad)
        crop = region_img.crop((xs, 0, xe, h))
        # 2× только на очень узких колонках — иначе 3×OCR убивает скорость
        scale = 2 if crop.width < 380 else 1
        work = crop
        if scale > 1:
            work = crop.resize(
                (crop.width * scale, crop.height * scale),
                Image.Resampling.LANCZOS,
            )
        raw = rapid_ocr_lines(work, max_side=0)
        n_ok = 0
        for p in raw:
            t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
            if not t or is_garbage_ocr(t) or is_hud_spam(t) or is_desktop_chrome(t):
                continue
            if len(t) <= 6 and not looks_like_ui_prompt(t) and " " not in t:
                continue
            if re.fullmatch(r"[a-z]{2,10}", t) and len(t) <= 8:
                continue
            bx1, by1, bx2, by2 = p["box"]
            copy = dict(p)
            copy["text"] = t
            copy["box"] = (
                int(xs + bx1 / scale),
                int(by1 / scale),
                int(xs + bx2 / scale),
                int(by2 / scale),
            )
            cx = (copy["box"][0] + copy["box"][2]) * 0.5
            if cx < x0 - 4 or cx > x1 + 4:
                continue
            copy["line_height"] = max(8, int(p.get("line_height", 14) / scale))
            copy["engine"] = f"col{ci}"
            copy["col"] = ci
            all_lines.append(copy)
            n_ok += 1
        tlog(f"col-ocr[{ci}] x={x0}-{x1} lines={n_ok}")
    if len(all_lines) < 4:
        return None
    all_lines.sort(key=lambda p: (p["box"][1], p["box"][0]))
    tlog(f"col-ocr total={len(all_lines)} cols={len(bounds)}")
    grouping._COL_OCR_CACHE[cache_key] = (now, [dict(p) for p in all_lines])
    # не раздувать кэш
    if len(grouping._COL_OCR_CACHE) > 8:
        oldest = min(grouping._COL_OCR_CACHE.items(), key=lambda kv: kv[1][0])[0]
        grouping._COL_OCR_CACHE.pop(oldest, None)
    return all_lines


def ocr_ink_bands_in_bubble(
    region_img: Image.Image, box: tuple[int, int, int, int], line_height: int = 0
) -> list[dict]:
    """Режет VN-пузырь на строки по тёмным пикселям и OCR'ит каждую отдельно.

    Full Rapid склеивает/глотает 2-ю строку → перевод обрубка («но оно есть»).
    """
    try:
        import numpy as np
        from PIL import ImageEnhance
    except ImportError:
        return []
    w, h = region_img.size
    x1, y1, x2, y2 = box
    lh = max(14, line_height or max(1, y2 - y1))
    # вниз: место под 2–3 строки пузыря (Rapid bbox часто только 1-я)
    ya = max(0, y1 - 8)
    # не уезжать в следующий блок UI (Sword Skill под описанием Healing Circle)
    yb = min(h, max(y2, y1 + lh) + int(lh * 2.6) + 18)
    xa = max(0, x1 - 36)
    xb = min(w, x2 + 36)
    if yb - ya < 20 or xb - xa < 40:
        return []
    crop = region_img.crop((xa, ya, xb, yb))
    gray = np.asarray(ImageOps.grayscale(crop))
    # розовый пузырь: не максимизируем dark-rows (заливает щель между строками),
    # а ищем thr с 2+ отдельными полосами разумной высоты
    best_bands: list[tuple[int, int]] = []
    for thr in (85, 95, 105, 115, 130):
        min_dark = max(28, gray.shape[1] // 28)
        ys = [i for i, c in enumerate((gray < thr).sum(axis=1)) if c >= min_dark]
        if not ys:
            continue
        bands_try: list[tuple[int, int]] = []
        start = prev = ys[0]
        for y in ys[1:]:
            if y - prev > 3:
                if prev - start >= 3:
                    bands_try.append((start, prev))
                start = y
            prev = y
        if prev - start >= 3:
            bands_try.append((start, prev))
        max_band_h = max(40, int(lh * 2.2))
        bands_try = [(a, b) for a, b in bands_try if 4 <= (b - a) <= max_band_h]
        # лучше: больше полос; при равенстве — меньше суммарной высоты (чище)
        score = (len(bands_try), -sum(b - a for a, b in bands_try))
        best_score = (len(best_bands), -sum(b - a for a, b in best_bands)) if best_bands else (-1, 0)
        if score > best_score:
            best_bands = bands_try
    bands = best_bands
    # если одна толстая полоса (две строки слиплись) — режем пополам
    if len(bands) == 1:
        a, b = bands[0]
        if (b - a) >= max(22, int(lh * 1.35)):
            mid = (a + b) // 2
            bands = [(a, mid - 1), (mid + 1, b)]
    out: list[dict] = []
    for a, b in bands[:5]:
        band = crop.crop((0, max(0, a - 3), crop.width, min(crop.height, b + 4)))
        if band.height < 6:
            continue
        g = ImageOps.autocontrast(ImageOps.grayscale(band), cutoff=1)
        candidates = [
            band,
            ImageEnhance.Contrast(g).enhance(2.1).convert("RGB"),
            g.point(lambda x: 0 if x < 130 else 255).convert("RGB"),
        ]
        best_t = ""
        best_box = None
        best_lh = b - a + 4
        for im in candidates:
            sc = max(4, (56 + im.height - 1) // max(1, im.height))
            big = im.resize(
                (max(1, im.width * sc), max(1, im.height * sc)),
                Image.Resampling.LANCZOS,
            )
            for p in rapid_ocr_lines(big, max_side=0):
                t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
                t = re.sub(r"\bfinanclal\b", "financial", t, flags=re.I)
                t = re.sub(r"\bllght\b", "light", t, flags=re.I)
                t = re.sub(r",(?=\S)", ", ", t)
                if len(t) > len(best_t):
                    best_t = t
                    bx1, by1, bx2, by2 = p["box"]
                    best_box = (
                        int(xa + bx1 / sc),
                        int(ya + a - 3 + by1 / sc),
                        int(xa + bx2 / sc),
                        int(ya + a - 3 + by2 / sc),
                    )
                    best_lh = max(8, int((by2 - by1) / sc))
            # уже хорошая строка — не гоняем остальные кандидаты
            if len(best_t) >= 24:
                break
        if len(best_t) < 6:
            continue
        if is_ink_band_garbage(best_t) or looks_like_section_header(best_t):
            continue
        words = re.findall(r"[A-Za-z]{3,}", best_t)
        good = [w for w in words if sum(c in "aeiouAEIOU" for c in w) >= 1]
        if len(good) < 1 and len(RE_LAT.findall(best_t)) < 12:
            continue
        # полоса слишком далеко от исходной строки — чужой блок UI
        bb = best_box or (xa, ya + a - 3, xb, ya + b + 4)
        if bb[1] > y2 + int(lh * 2.4):
            continue
        out.append(
            {
                "text": best_t,
                "box": bb,
                "line_height": best_lh,
                "kind": "dialogue",
                "engine": "ink-band",
                "conf": 62.0,
            }
        )
    return out


def ocr_continuation_below(
    region_img: Image.Image, box: tuple[int, int, int, int], line_height: int = 0
) -> list[dict]:
    """Сначала ink-bands (2-я строка пузыря), иначе пусто — cover-expand снаружи."""
    bands = ocr_ink_bands_in_bubble(region_img, box, line_height)
    if len(bands) >= 2:
        return bands[1:]
    if len(bands) == 1:
        _x1, _y1, _x2, y2 = box
        below = [b for b in bands if b["box"][1] >= y2 - 6]
        return below
    return []


def finish_incomplete_vn_lines(region_img: Image.Image, lines: list[dict]) -> list[dict]:
    """Добивает обрубленные VN-фразы 2-й строкой; kind=dialogue для cover/2 строк."""
    if not lines:
        return lines
    out: list[dict] = []
    for p in lines:
        copy = dict(p)
        t = unglue_english(normalize_subtitle_ocr(str(copy.get("text", "")).strip()))
        copy["text"] = t
        copy["kind"] = "dialogue"
        copy["wrap"] = True
        lbs0 = list(copy.get("line_boxes") or [])
        if (
            "\n" in t
            and len(lbs0) >= 2
            and not dialogue_looks_incomplete(t)
        ):
            out.append(copy)
            continue
        # A bubble is a bubble because someone is speaking in it. A codex entry,
        # a tip and a paragraph are not, and reading ink bands across them to
        # find the rest of a sentence is what turned a four-line game hint into
        # one card joined mid-word - "Safe Areas" came out as "Safe Are as",
        # because the bands were stacked and joined with nothing saying they
        # belonged together.
        #
        # The line is still worth completing when it plainly stops mid-sentence
        # or when it is already known to be part of a bubble, so the test is not
        # "does it have a speaker" but "is there any reason to think it is one".
        # Prose, not speech. Seven words or more with no capital in it is a
        # sentence, whatever it looks like it stops on: the reported hint had a
        # line ending in "progressing" and one ending in "there", both of which
        # read as a half-spoken bubble and both of which are the middle of a
        # codex paragraph.
        bubble_hint = not _reads_as_prose(t) and (
            str(copy.get("kind", "")) == "dialogue"
            or looks_like_spoken_line(t)
            or dialogue_looks_incomplete(t)
            or bool(lbs0)
        )
        need_ink = bubble_hint and (
            dialogue_looks_incomplete(t)
            or "\n" not in t
            or len(lbs0) < 2
        )
        bands: list[dict] = []
        if need_ink:
            bands = ocr_ink_bands_in_bubble(
                region_img, copy["box"], int(copy.get("line_height", 16))
            )
        if len(bands) >= 2:
            # не склеивать описание с заголовком следующей секции
            clean_bands = [
                b
                for b in bands
                if not is_ink_band_garbage(str(b.get("text", "")))
                and not looks_like_section_header(str(b.get("text", "")))
            ]
            if len(clean_bands) >= 2:
                bands = clean_bands
                merged = _merge_vn_band_cluster(bands)
                if not is_ink_band_garbage(str(merged.get("text", ""))):
                    merged["kind"] = "dialogue"
                    merged["wrap"] = True
                    merged.pop("incomplete", None)
                    tlog(
                        f"vn-ink replace n={len(bands)} "
                        + " || ".join(str(e.get("text", ""))[:40] for e in bands)
                    )
                    out.append(merged)
                    continue
            bands = clean_bands or bands
        main_c = re.sub(r"\s+", "", t).casefold()
        usable: list[dict] = []
        if len(bands) == 1:
            b0 = str(bands[0].get("text", ""))
            if (
                len(b0) > len(t) + 4
                and not is_ink_band_garbage(b0)
                and not looks_like_section_header(b0)
            ):
                copy["text"] = b0
                t = b0
                main_c = re.sub(r"\s+", "", t).casefold()
                copy["box"] = bands[0]["box"]
        # Only a bubble gets completed. The line above already decided whether
        # this is one, and a paragraph that happens to end mid-word is not a
        # half-spoken sentence - it is the middle of a codex entry, and reading
        # the line below it joined two sentences with no sentence between them.
        if bubble_hint and dialogue_looks_incomplete(t):
            for e in ocr_continuation_below(
                region_img, copy["box"], int(copy.get("line_height", 16))
            )[:2]:
                et = str(e.get("text", ""))
                if is_ink_band_garbage(et) or looks_like_section_header(et):
                    continue
                ec = re.sub(r"\s+", "", et).casefold()
                if ec and ec not in main_c and len(et) >= 8:
                    usable.append(e)
        if usable:
            merged = _merge_vn_band_cluster([copy] + usable)
            if is_ink_band_garbage(str(merged.get("text", ""))):
                out.append(copy)
                continue
            merged["kind"] = "dialogue"
            merged["wrap"] = True
            merged.pop("incomplete", None)
            tlog(
                f"vn-ink +{len(usable)} "
                + " || ".join(str(e.get("text", ""))[:44] for e in usable)
            )
            out.append(merged)
            continue
        if dialogue_looks_incomplete(t):
            x1, y1, x2, y2 = copy["box"]
            lh = max(16, y2 - y1)
            copy["box"] = (x1, y1, x2, min(region_img.height - 2, y2 + int(lh * 1.35) + 10))
            copy["incomplete"] = True
            tlog(f"vn-cover-expand h={copy['box'][3] - copy['box'][1]} incomplete=1")
        out.append(copy)
    return out


def extend_dialogue_with_bottom_pass(region_img: Image.Image, blocks: list[dict]) -> list[dict]:
    """Второй проход по самому низу, если реплика обрезана (2-я строка letterbox)."""
    if not blocks:
        return blocks
    main = blocks[0]
    text = str(main.get("text", ""))
    lbs0 = list(main.get("line_boxes") or [])
    # уже собрали 2+ строки и хвост полный — не трогаем
    if len(lbs0) >= 2 and "\n" in text and not dialogue_looks_incomplete(text):
        return blocks
    w, h = region_img.size
    x1, y1, x2, y2 = main["box"]
    lh = max(14, int(main.get("line_height", 18)))
    # одна строка OCR, но bbox уже «двухэтажный» — всё равно глянем низ
    tall_box = (y2 - y1) >= max(44, int(lh * 2.1))
    need_pass = dialogue_looks_incomplete(text) or (len(lbs0) < 2 and tall_box)
    if not need_pass:
        return blocks
    # Сразу под первой строкой / серединой tall box.
    #
    # Полоса идёт на две строки ниже блока, а не до низа экрана. Раньше она шла
    # от y2-4 до нижнего края кадра, и на одном экране с именем персонажа
    # и подписью «CV: Horse» внизу в 600px под описание реплики попадала
    # совершенно другая строка: получился блок с четырьмя line_boxes через пол-
    # экрана и карточка, нарисованная там, где текста нет.
    #
    # Перенос строки живёт в пределах интерлиньяжа — двух высот строки от края
    # блока. Всё, что дальше, — другой элемент экрана, каким бы длинным текстом
    # оно ни было.
    reach = max(48, int(lh * 2.6))
    y0 = max(int(h * 0.58), int(y1 + lh * 0.85) if tall_box else int(y2) - 4)
    y0 = min(y0, max(0, int(y2) - 4))
    band_bottom = min(h, int(y2) + reach)
    if y0 >= band_bottom - 16 or band_bottom >= h - 16:
        return blocks
    band = region_img.crop((0, y0, w, band_bottom))
    if band.height < 24:
        return blocks
    scale = 3 if band.height < 120 else 2
    band2 = band.resize((band.width * scale, band.height * scale), Image.Resampling.LANCZOS)
    raw = rapid_ocr_lines(band2)
    extra = []
    for p in raw:
        bx1, by1, bx2, by2 = p["box"]
        copy = dict(p)
        copy["box"] = (
            int(bx1 / scale),
            int(y0 + by1 / scale),
            int(bx2 / scale),
            int(y0 + by2 / scale),
        )
        copy["line_height"] = max(8, int(p["line_height"] / scale))
        raw_t = str(p.get("text", ""))
        if is_overlay_echo_ocr(raw_t):
            continue
        copy["text"] = unglue_english(normalize_subtitle_ocr(raw_t))
        extra.append(copy)
    # не дублировать уже известный текст
    main_low = re.sub(r"\s+", "", text).casefold()
    filtered = []
    for p in extra:
        t = str(p.get("text", "")).strip()
        if not t or is_desktop_chrome(t) or is_hud_spam(t):
            continue
        if is_overlay_echo_ocr(t):
            continue
        if is_subtitle_junk_line(t, p.get("box"), w, h):
            tdetail(f"bottom-drop-junk '{t[:36]}'")
            continue
        if p["box"][1] < y1 - 4:
            continue
        # вторая строка должна быть заметно ниже первой
        if p["box"][1] < y1 + max(10, int(lh * 0.55)):
            continue
        #... и не дальше, чем на пару строк. Верхней границы не было, и строка
        # из другого элемента экрана дописывалась в реплику как её хвост.
        if p["box"][1] > y2 + reach:
            continue
        compact = re.sub(r"\s+", "", t).casefold()
        if compact and compact in main_low:
            continue
        if looks_like_subtitle_continue(t) or looks_like_spoken_line(t) or (
            len(RE_LAT.findall(t)) >= 10 and not looks_like_spoken_line(t)
        ) or len(RE_CJK.findall(t)) >= 4:
            filtered.append(p)
    if not filtered:
        # уже есть перенос — не раздуваем и не ломаем вёрстку
        if "\n" in text and len(lbs0) >= 2:
            return blocks
        tdetail(f"bottom-pass miss after incomplete {text[-40:]!r} "
            f"raw={len(extra)} y0={y0}")
        if not extra:
            # Полоса под блоком прочитана и оказалась пустой: второй строки нет.
            # Раздувать бок всё равно, что и было сделано, - кегль берётся из
            # высоты бокса, и «Резюме: Лошадь» выходила вдвое мельче
            # соседнего «HP:250», хотя строк у неё ровно одна, как и у соседа.
            return blocks
        # OCR не видит 2-ю строку — расширяем cover вниз + synthetic line_boxes
        expanded = dict(main)
        nx1, ny1, nx2, ny2 = x1, y1, x2, y2
        ny2 = min(h - 2, max(ny2, ny1 + int(lh * 2.55) + 8))
        pad_x = max(40, int((nx2 - nx1) * 0.08))
        nx1 = max(0, nx1 - pad_x)
        nx2 = min(w, nx2 + pad_x)
        expanded["box"] = (nx1, ny1, nx2, ny2)
        expanded["text"] = text
        # synthetic 2 pin-boxes только если OCR bbox уже «двухэтажный»
        if len(lbs0) < 2 and tall_box:
            mid = ny1 + max(lh + 2, (ny2 - ny1) // 2)
            expanded["line_boxes"] = [
                {
                    "box": (nx1, ny1, nx2, mid - 2),
                    "text": text.split("\n")[0],
                    "line_height": lh,
                    "angle": float(main.get("angle", 0) or 0),
                    "conf": float(main.get("conf", 0) or 0),
                },
                {
                    "box": (nx1, mid + 2, nx2, ny2),
                    "text": "",
                    "line_height": lh,
                    "angle": float(main.get("angle", 0) or 0),
                    "conf": 0.0,
                },
            ]
            tlog(f"cover-expand synthetic-2line box={nx2-nx1}x{ny2-ny1}")
        else:
            tlog(f"cover-expand box={nx2-nx1}x{ny2-ny1} (2nd-line reserve)")
        return [expanded] + blocks[1:]
    # не склеивать заново в одну строку: дописать только новые хвосты
    if "\n" in text:
        # уже 2 строки — только расширим bbox если extra ниже
        nx1, ny1, nx2, ny2 = main["box"]
        for p in filtered:
            nx1 = min(nx1, p["box"][0])
            ny1 = min(ny1, p["box"][1])
            nx2 = max(nx2, p["box"][2])
            ny2 = max(ny2, p["box"][3])
        updated = dict(main)
        updated["box"] = (nx1, ny1, nx2, ny2)
        # дописать строки которых ещё нет
        existing = {re.sub(r"\s+", "", ln).casefold() for ln in text.split("\n")}
        extra_lines = []
        new_lbs = list(lbs0)
        for p in filtered:
            t = str(p.get("text", "")).strip()
            key = re.sub(r"\s+", "", t).casefold()
            if key and key not in existing:
                extra_lines.append(t)
                new_lbs.append(
                    {
                        "box": tuple(int(v) for v in p["box"]),
                        "text": t,
                        "line_height": max(10, int(p.get("line_height", lh))),
                        "angle": float(p.get("angle", 0) or 0),
                        "conf": float(p.get("conf", 0) or 0),
                    }
                )
        if extra_lines:
            updated["text"] = text + "\n" + "\n".join(extra_lines)
            updated["line_boxes"] = new_lbs
            tlog(f"bottom-pass keep-breaks +{len(extra_lines)}")
        return [updated] + blocks[1:]
    # первая строка отдельно + продолжения (вертикальный merge с \n)
    line1 = {
        "text": text.split("\n")[0],
        "box": main["box"],
        "line_height": main.get("line_height", 20),
        "conf": main.get("conf", 80),
    }
    merged = merge_subtitle_cluster([line1] + filtered, w, h)
    tlog(
        f"bottom-pass +{len(filtered)} chars={len(merged['text'])} "
        f"box={merged['box'][2]-merged['box'][0]}x{merged['box'][3]-merged['box'][1]}"
    )
    tdetail(f"bottom-pass merged={merged['text'][:90]!r}")
    return [merged] + blocks[1:]


def offset_boxes(lines: list[dict], dy: int = 0, dx: int = 0) -> list[dict]:
    out = []
    for p in lines:
        x1, y1, x2, y2 = p["box"]
        copy = dict(p)
        copy["box"] = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        out.append(copy)
    return out


def _ocr_scaled_panel(
    region_img: Image.Image,
    box: tuple[int, int, int, int],
    scale: int,
    tag: str,
    *,
    try_descan: bool = False,
) -> list[dict]:
    x0, y0, x1, y1 = box
    w, h = region_img.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 40 or y1 - y0 < 24:
        return []
    panel = region_img.crop((x0, y0, x1, y1))
    # 1 проход 2× — хватает; descan только если пусто (Blinded-шум)
    variants: list[Image.Image] = [
        panel.resize(
            (max(1, panel.width * scale), max(1, panel.height * scale)),
            Image.Resampling.BILINEAR,
        ),
    ]
    if try_descan:
        soft = soft_descan_panel(panel)
        variants.append(
            soft.resize(
                (max(1, soft.width * scale), max(1, soft.height * scale)),
                Image.Resampling.BILINEAR,
            )
        )

    def _score(work: Image.Image) -> list[dict]:
        sx = work.width / max(1, panel.width)
        sy = work.height / max(1, panel.height)
        scored: list[dict] = []
        for p in rapid_ocr_lines(work):
            t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
            if not t or not looks_like_gameplay_hud_line(t):
                continue
            if len(RE_LAT.findall(t)) < 4 and len(RE_CJK.findall(t)) < 2:
                continue
            bx1, by1, bx2, by2 = p["box"]
            copy = dict(p)
            copy["box"] = (
                int(x0 + bx1 / sx),
                int(y0 + by1 / sy),
                int(x0 + bx2 / sx),
                int(y0 + by2 / sy),
            )
            copy["line_height"] = max(8, int(p.get("line_height", 14) / max(sx, sy)))
            copy["text"] = t
            copy["kind"] = "ui"
            copy["engine"] = tag
            scored.append(copy)
        return scored

    best = _score(variants[0])
    if not best and len(variants) > 1:
        best = _score(variants[1])
    return best


def ocr_center_modal_panel(region_img: Image.Image, existing: list[dict]) -> list[dict]:
    """Догон центральных модалок (Game Hints / Tutorial) — верх панели часто теряется."""
    w, h = region_img.size
    if w < 700 or h < 420:
        return existing
    blob = " ".join(str(p.get("text", "")) for p in existing).casefold()
    want = any(
        k in blob
        for k in (
            "game hint", "hints", "tutorial", "player menu", "close",
            "look around", "stuck", "подсказ",
        )
    )
    # мало строк в центре — тоже попробуем
    mid = [
        p
        for p in existing
        if int(w * 0.25) <= (p["box"][0] + p["box"][2]) // 2 <= int(w * 0.75)
        and int(h * 0.15) <= p["box"][1] <= int(h * 0.85)
    ]
    if not want and len(mid) >= 4:
        return existing
    if not want and len(existing) >= 12:
        return existing
    # The main pass already read this screen confidently: hunting for a modal is
    # a whole extra RapidOCR pass for nothing.
    if not want and english_ocr_quality(existing) >= 55.0:
        return existing
    x0, y0 = int(w * 0.20), int(h * 0.15)
    x1, y1 = int(w * 0.80), int(h * 0.90)
    crop = region_img.crop((x0, y0, x1, y1))
    try:
        from PIL import ImageEnhance, ImageOps
    except ImportError:
        work = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
        lines = rapid_ocr_lines(work, max_side=0)
    else:
        work = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
        g = ImageOps.autocontrast(ImageOps.grayscale(work), cutoff=1)
        g = ImageEnhance.Contrast(g).enhance(1.7).convert("RGB")
        lines = rapid_ocr_lines(g, max_side=0)
    if not lines:
        return existing
    extras: list[dict] = []
    have = {ui_text_fingerprint(str(p.get("text", ""))) for p in existing}
    for p in lines:
        t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
        if not t or is_desktop_chrome(t) or is_hud_spam(t) or is_overlay_echo_ocr(t):
            continue
        if is_garbage_ocr(t) and not looks_like_spoken_line(t) and not looks_like_ui_prompt(t):
            continue
        # только осмысленный EN/JP
        if not (RE_LAT.search(t) or RE_CJK.search(t)):
            continue
        if len(RE_LAT.findall(t)) < 4 and not looks_like_ui_prompt(t):
            continue
        fp = ui_text_fingerprint(t)
        if fp in have:
            continue
        bx1, by1, bx2, by2 = p["box"]
        copy = dict(p)
        copy["text"] = t
        copy["box"] = (
            x0 + bx1 // 2,
            y0 + by1 // 2,
            x0 + bx2 // 2,
            y0 + by2 // 2,
        )
        copy["line_height"] = max(8, int(p.get("line_height", 14)) // 2)
        copy["kind"] = "ui"
        extras.append(copy)
        have.add(fp)
    if not extras:
        return existing
    tlog(
        f"modal-panel +{len(extras)} "
        + " || ".join(str(e.get("text", ""))[:36] for e in extras[:5])
    )
    return dedupe_near_ui_lines(list(existing) + extras)


def ocr_right_panel_toasts(region_img: Image.Image, existing: list[dict]) -> list[dict]:
    """Догон HUD-зон только если полный кадр явно недодал строки.

    Четыре зоны по три прохода RapidOCR стоят около пяти секунд на цикл, что
    для VN и меню неприемлемо, поэтому добор включается только когда основной
    проход явно недобрал строки.
    """
    w, h = region_img.size
    if w < 640 or h < 400:
        return existing
    n = len(existing)
    if n >= 6:
        return existing
    if english_ocr_quality(existing) >= 55.0:
        return existing
    # текст в основном снизу (новелла / confirm) — боевой HUD не нужен
    if n >= 1:
        bottomish = sum(1 for p in existing if p["box"][1] >= int(h * 0.52))
        if bottomish >= max(1, (n + 1) // 2):
            return existing
    # полный кадр уже видит и лево и право — панели не дадут выигрыша
    if n >= 3:
        left = sum(1 for p in existing if p["box"][2] < w * 0.45)
        right = sum(1 for p in existing if p["box"][0] > w * 0.50)
        if left >= 1 and right >= 1:
            return existing

    # мало строк → точечный догон; scanline-descan только если совсем пусто справа
    need_descan = n <= 2
    extras: list[dict] = []
    # право: миссии/тосты (главный пробел при Blinded)
    extras.extend(
        _ocr_scaled_panel(
            region_img,
            (int(w * 0.52), int(h * 0.08), w, int(h * 0.48)),
            2,
            "hud-right",
            try_descan=need_descan and n <= 1,
        )
    )
    # лево: Waiting / status — только если слева пусто
    if not any(p["box"][2] < w * 0.42 for p in existing):
        extras.extend(
            _ocr_scaled_panel(
                region_img,
                (0, int(h * 0.06), int(w * 0.40), int(h * 0.32)),
                2,
                "hud-left",
                try_descan=False,
            )
        )
    # центр: статус над персонажем — только если центр пуст и строк мало
    if n + len(extras) <= 2 and not any(
        w * 0.30 < (p["box"][0] + p["box"][2]) * 0.5 < w * 0.70
        and h * 0.45 < (p["box"][1] + p["box"][3]) * 0.5 < h * 0.78
        for p in existing
    ):
        extras.extend(
            _ocr_scaled_panel(
                region_img,
                (int(w * 0.32), int(h * 0.48), int(w * 0.68), int(h * 0.72)),
                2,
                "hud-center",
                try_descan=need_descan,
            )
        )
    if not extras:
        return existing
    merged = dedupe_near_ui_lines(list(existing) + extras)
    gained = len(merged) - len(existing)
    if gained > 0:
        tlog(
            f"hud-panels +{gained} "
            + " || ".join(str(e.get("text", ""))[:32] for e in extras[:6])
        )
    return merged


def merge_rapid_prefer_dialogue(full: list[dict], band: list[dict]) -> list[dict]:
    """Склеивает fullscreen Rapid + нижнюю полосу; диалоги снизу важнее HUD.

    Одинаковый текст на разной высоте (pinned chat + тот же chat в repo) — оба оставляем.
    Схлопываем только почти совпадающие по месту дубли.
    """
    out: list[dict] = []
    dropped_same_spot = 0
    kept_same_text = 0
    dropped_overlap = 0
    for p in full + band:
        t = str(p.get("text", "")).strip()
        if not t or is_hud_spam(t) or is_desktop_chrome(t):
            continue
        key = re.sub(r"\s+", " ", t).casefold()
        cy = (p["box"][1] + p["box"][3]) * 0.5
        replaced = False
        # Two sources often read the same line differently ("Chapter 3" from
        # the full pass, "Chanter 3" from the top strip). Comparing text alone
        # kept both and drew two cards on top of each other, so the same spot is
        # collapsed here too and the better reading wins.
        for i, prev in enumerate(out):
            if _iou(tuple(p["box"]), tuple(prev["box"])) < 0.55:
                continue
            if _reading_key(p) > _reading_key(prev):
                out[i] = p
            dropped_overlap += 1
            replaced = True
            break
        if replaced:
            continue
        for i, prev in enumerate(out):
            pk = re.sub(r"\s+", " ", str(prev.get("text", ""))).casefold()
            pcy = (prev["box"][1] + prev["box"][3]) * 0.5
            if pk != key:
                continue
            if abs(cy - pcy) < 18:
                # тот же текст, почти тот же Y — берём более длинный / диалог
                if len(t) > len(str(prev.get("text", ""))) or looks_like_spoken_line(t):
                    out[i] = p
                dropped_same_spot += 1
                replaced = True
                break
            # тот же текст, другой Y — это не дубль UI
            kept_same_text += 1
        if not replaced:
            out.append(p)
    out.sort(
        key=lambda p: (
            0 if looks_like_spoken_line(str(p.get("text", ""))) else 1,
            p["box"][1],
            p["box"][0],
        )
    )
    if kept_same_text or dropped_same_spot or dropped_overlap:
        tlog(f"merge-spatial keep-dup-text={kept_same_text} "
            f"drop-same-spot={dropped_same_spot} drop-overlap={dropped_overlap} n={len(out)}")
    return out


def ocr_top_chrome_strip(region_img: Image.Image, lines: list[dict]) -> list[dict]:
    """Тонкая верхняя полоса меню (File/Edit/View/Help) — Rapid часто пропускает на fullscreen."""
    w, h = region_img.size
    if h < 36 or w < 200:
        return lines
    top_h = min(48, max(30, h // 36))
    have = {ui_text_fingerprint(str(p.get("text", ""))) for p in lines}
    # уже есть меню — не гоняем лишний OCR
    menu_hit = sum(
        1
        for p in lines
        if str(p.get("text", "")).strip().casefold() in ("file", "edit", "view", "help")
        and int(p["box"][1]) <= top_h + 8
    )
    if menu_hit >= 3:
        return lines
    extras: list[dict] = []
    # левая зона меню + чуть шире (иногда Help правее)
    for x0, x1, tag in ((0, min(w, 420), "menu-l"), (0, min(w, 640), "menu-w")):
        if extras and tag == "menu-w":
            break
        strip = region_img.crop((x0, 0, x1, top_h))
        scale = 3 if max(strip.size) < 700 else 2
        work = strip.resize(
            (max(1, strip.width * scale), max(1, strip.height * scale)),
            Image.Resampling.LANCZOS,
        )
        raw = rapid_ocr_lines(work, max_side=0)
        for p in raw:
            t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
            if not t or is_hud_spam(t) or is_desktop_chrome(t):
                continue
            if looks_like_ocr_mojibake_of_russian(t) or is_overlay_echo_ocr(t):
                continue
            # меню или короткий Title Case
            words = t.split()
            menuish = all(
                re.sub(r"[^A-Za-z]", "", w).casefold()
                in {"file", "edit", "view", "help", "go", "run", "terminal", "selection"}
                for w in words
            ) and 1 <= len(words) <= 6
            if not menuish and not looks_like_ui_prompt(t):
                continue
            bx1, by1, bx2, by2 = p["box"]
            copy = dict(p)
            copy["text"] = t
            copy["box"] = (
                x0 + int(bx1 / scale),
                int(by1 / scale),
                x0 + int(bx2 / scale),
                int(by2 / scale),
            )
            copy["line_height"] = max(8, int(p.get("line_height", 12) / scale))
            copy["kind"] = "ui"
            copy["oneline"] = True
            copy["engine"] = "top-chrome"
            fp = ui_text_fingerprint(t)
            if fp in have:
                continue
            extras.append(copy)
            have.add(fp)
        if extras:
            tlog(
                f"top-chrome {tag} +{len(extras)} "
                + " || ".join(str(e.get("text", ""))[:24] for e in extras[:6])
            )
            break
    if not extras:
        return lines
    # File Edit View Help могли склеиться — разрежем
    extras = split_false_nav_merges(extras)
    merged = list(lines) + extras
    merged.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return merged


def ocr_fill_vertical_gaps(region_img: Image.Image, lines: list[dict]) -> list[dict]:
    """eng-ui: между строками бывает дыра (чат под репо) — RapidOCR часто её пропускает.
    На узком выделении абзаца ещё и верх до первой строки; иначе «Learn with…» пропадает."""
    if region_img.height < 80:
        return lines
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    extras: list[dict] = []
    w, h = region_img.size
    # "Fullscreen" = large landscape capture, not an FHD-only 1100×700 gate.
    aspect = w / max(1, h)
    fullscreen = w >= max(900, int(h * 1.35)) and h >= max(480, int(w * 0.35)) and aspect >= 1.2
    # fullscreen / игра: не гоняем все гэпы (дорого), но верхнюю дыру добираем
    gap_budget = 1 if fullscreen else (6 if w < 720 else 3)
    strips: list[tuple[int, int, str]] = []
    if ordered:
        top_y = int(ordered[0]["box"][1])
        if top_y >= 28:
            strips.append((0, max(10, top_y - 2), "top"))
    else:
        strips.append((0, h, "full"))
    if not fullscreen:
        for a, b in zip(ordered, ordered[1:]):
            gap = int(b["box"][1] - a["box"][3])
            if gap < 22 or gap > 160:
                continue
            y0 = max(0, int(a["box"][3]) + 1)
            y1 = min(h, int(b["box"][1]) - 1)
            if y1 - y0 < 10:
                continue
            # низ страницы (кнопки) — можно, но не самый footer
            if y0 > h * 0.88:
                continue
            strips.append((y0, y1, f"gap{gap}"))
    for y0, y1, tag in strips:
        if gap_budget <= 0:
            break
        if y1 - y0 < 10:
            continue
        strip = region_img.crop((0, y0, w, y1))
        scale = 2 if max(strip.size) < 900 else 1
        work = strip.resize(
            (max(1, strip.width * scale), max(1, strip.height * scale)),
            Image.Resampling.LANCZOS,
        )
        raw = rapid_ocr_lines(work, max_side=0)
        gap_budget -= 1
        found = 0
        for p in raw:
            t = str(p.get("text", "")).strip()
            if not t or is_hud_spam(t) or is_desktop_chrome(t) or is_garbage_ocr(t):
                continue
            if len(RE_LAT.findall(t)) < 4 and not re.search(r"[.!?]", t):
                continue
            x1, y1b, x2, y2 = p["box"]
            copy = dict(p)
            copy["box"] = (
                int(x1 / scale),
                int(y0 + y1b / scale),
                int(x2 / scale),
                int(y0 + y2 / scale),
            )
            copy["line_height"] = max(8, int(p.get("line_height", 12) / scale))
            copy["engine"] = "gap-fill"
            copy["text"] = unglue_english(normalize_subtitle_ocr(t))
            cy = (copy["box"][1] + copy["box"][3]) * 0.5
            if any(abs(cy - (q["box"][1] + q["box"][3]) * 0.5) < 12 for q in ordered + extras):
                continue
            extras.append(copy)
            found += 1
        if found:
            tlog(
                f"gap-fill {tag} y={y0}-{y1} +{found} "
                + " || ".join(str(e.get("text", ""))[:40] for e in extras[-found:])
            )
        else:
            tlog(f"gap-fill-miss {tag} y={y0}-{y1}h={y1 - y0}")
    if not extras:
        return lines
    merged = list(lines) + extras
    merged.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return merged


def ocr_eng_ui_boost(region_img: Image.Image, lines: list[dict]) -> list[dict]:
    """Добор пропущенных строк абзаца на выделенной области."""
    w, h = region_img.size
    log_ocr_coverage("ocr-ui", lines, w, h)
    lines = ocr_top_chrome_strip(region_img, lines)
    lines = ocr_fill_vertical_gaps(region_img, lines)
    lines = tess_fill_sparse_ui(region_img, lines)
    # The extra passes read the same lines a second time and sometimes get them
    # wrong ("Chanter" for "Chapter"). The boost runs after the spatial merge,
    # so the duplicates had to be collapsed here as well.
    lines = merge_overlapping_duplicates(lines)
    log_ocr_coverage("ocr-ui-after", lines, w, h)
    return lines

