"""Layout: turning recognised lines into blocks.

Распознаватель отдаёт то, что он увидел: отдельные слова в отдельных
прямоугольниках, а на экране это абзац, колонка веб-страницы,
имя персонажа рядом с его репликой или ряд кнопок. Ни одно из этих решений в
тексте не записано - их видно только по геометрии, поэтому здесь они и
принимаются.

Модуль ничего не знает про перевод и про отрисовку: на входе строки OCR с
боксами, на выходе то же самое, но собранное в блоки. Резать то, что
распознаватель склеил, и склеивать то, что он нарезал, - два направления одного
прохода, и оба решаются здесь.

Тела перенесены из `live.py` дословно. Первый проход этой работы был написан по
памяти: функции выглядели теми же, тесты проходили, а `stitch_same_baseline`
молча склеивал соседние колонки веб-страницы, потому что потерялся `median` и
порог по базовой линии съехал на несколько процентов.
"""
from __future__ import annotations

import re
from statistics import median

from PIL import Image

from ..core.scale import wide_enough_for_columns
from ..core.scripts import (  # noqa: F401
    script_of,
    share_in_script,
    target_language,
)
from ..core.text import (  # noqa: F401
    RE_CJK,
    RE_CYR,
    RE_LAT,
    _continues_flow,
    _empty_column_runs,
    _ink_column_profile,
    cap_ui_lines,
    is_hud_spam,
    looks_like_speaker_prefix,
    looks_like_spoken_line,
    normalize_subtitle_ocr,
    tdetail,
    text_by_x_range,
    tlog,
)
from ..typography.metrics import (  # noqa: F401
    RE_JPN,
    _ink_mask,
    _merge_vn_band_cluster,
    _metrics_crop,
    dedupe_near_ui_lines,
    looks_like_ui_prompt,
    unglue_english,
    vn_lines_should_merge,
)

_COL_OCR_CACHE: dict[tuple, tuple[float, list]] = {}


_COL_OCR_CACHE_TTL = 25.0


def page_column_gutters(lines: list[dict], rw: int) -> list[tuple[int, int]]:
    """Вертикальные пустые желоба между колонками (веб 2–3 колонки)."""
    if rw < 520 or len(lines) < 3:
        return []
    # Don't split if we only have UI buttons (AUTO, OFF, SKIP, etc.)
    button_texts = {"AUTO", "OFF", "SKIP", "PAUSE", "PLAY", "AUTO OFF", "AUTO OFF SKIP"}
    text_count = sum(1 for p in lines if str(p.get("text", "")).strip().upper() in button_texts)
    if text_count >= 2:
        return []
    hist = [0] * rw
    for p in lines:
        try:
            x1, _y1, x2, _y2 = (int(v) for v in p["box"])
        except Exception:
            continue
        if x2 <= x1:
            continue
        x1 = max(0, min(rw - 1, x1))
        x2 = max(x1 + 1, min(rw, x2))
        for x in range(x1, x2):
            hist[x] += 1
    peak = max(hist) if hist else 0
    if peak < 3:
        return []
    empty_thr = max(1, int(peak * 0.12))
    margin = max(24, int(rw * 0.04))
    min_w = max(36, int(rw * 0.028))
    gutters: list[tuple[int, int]] = []
    x = margin
    while x < rw - margin:
        if hist[x] <= empty_thr:
            x0 = x
            while x < rw - margin and hist[x] <= empty_thr:
                x += 1
            x1 = x
            if x1 - x0 >= min_w:
                left_mass = sum(hist[margin:x0])
                right_mass = sum(hist[x1 : rw - margin])
                # желоб только если слева и справа есть колонки текста
                if left_mass >= 4 and right_mass >= 4:
                    gutters.append((x0, x1))
        else:
            x += 1
    return [g for g in gutters[:4] if _flanked_by_columns(lines, g)][:4]


def _flanked_by_columns(lines: list[dict], gutter: tuple[int, int]) -> bool:
    """Whether a strip of nothing has a column of text on each side of it.

    An empty vertical strip is only a gutter if it separates columns, and a
    column is text that stands opposite other text: a page in two columns has row
    after row with something in the left margin and something at the same height
    in the right margin. That is what tells the two apart, because on a screen
    that is not a page - a game menu, a results board, anything laid out in
    whatever place each thing wanted - an empty strip is just where nothing
    happened to be, and cutting a line of text there is how "New Best!!" became
    "New" and "Best!!".

    So there have to be text on one side and text on the other at matching
    heights, more than once. Once is a coincidence of where things sit; three or
    more is a layout. Overlaps are counted across the strip only - two boxes both
    on the left, at the same height, say nothing about what is on the right.
    """
    gx0, gx1 = gutter
    left: list[tuple[int, int]] = []
    right: list[tuple[int, int]] = []
    for p in lines:
        try:
            x1, y1, x2, y2 = (int(v) for v in p["box"])
        except Exception:
            continue
        if x2 <= x1:
            continue
        if x2 <= gx0:
            left.append((y1, y2))
        elif x1 >= gx1:
            right.append((y1, y2))
    if not left or not right:
        return False
    height = median(
        [b[1] - b[0] for b in left + right if b[1] > b[0]] or [1]
    )
    pairs = [
        (ly1, ly2)
        for ly1, ly2 in left
        for ry1, ry2 in right
        if min(ly2, ry2) - max(ly1, ry1) > height * 0.4
    ]
    if not pairs:
        return False
    # Distinct heights, so one tall box on each side does not pass for a column.
    spans = sorted(pairs)
    merged = 0
    reach = -1
    for top, bottom in spans:
        if top >= reach:
            merged += 1
            reach = bottom
        else:
            reach = max(reach, bottom)
    return merged >= 2


def gap_hits_gutter(left_x2: int, right_x1: int, gutters: list[tuple[int, int]]) -> bool:
    if right_x1 < left_x2:
        return False
    for g0, g1 in gutters:
        # зазор между боксами пересекает пустой желоб
        if left_x2 <= g1 and right_x1 >= g0:
            return True
    return False


def box_crosses_gutter(x1: int, x2: int, gutters: list[tuple[int, int]]) -> list[int]:
    """Центры желобов, которые бокс реально перекрывает (текст слева и справа)."""
    cuts: list[int] = []
    for g0, g1 in gutters:
        gc = (g0 + g1) // 2
        if x1 < g0 - 2 and x2 > g1 + 2:
            cuts.append(gc)
    return cuts


def split_text_by_segment_widths(text: str, widths: list[int]) -> list[str]:
    """Грубо режет строку по долям ширин колонок (слова целиком)."""
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    if len(widths) <= 1 or len(words) <= 1:
        return [text.strip()] if text.strip() else []
    total_w = max(1, sum(max(1, w) for w in widths))
    budgets = [max(1, int(round(len(words) * (w / total_w)))) for w in widths]
    # поправить сумму бюджетов
    while sum(budgets) > len(words):
        i = max(range(len(budgets)), key=lambda k: budgets[k])
        if budgets[i] <= 1:
            break
        budgets[i] -= 1
    while sum(budgets) < len(words):
        i = max(range(len(budgets)), key=lambda k: widths[k] / max(1, budgets[k]))
        budgets[i] += 1
    out: list[str] = []
    i = 0
    for n in budgets:
        chunk = words[i : i + n]
        i += n
        if chunk:
            out.append(" ".join(chunk))
    if i < len(words) and out:
        out[-1] = (out[-1] + " " + " ".join(words[i:])).strip()
    return out or ([text.strip()] if text.strip() else [])


def split_cross_column_merges(lines: list[dict], rw: int = 0) -> list[dict]:
    """Режет OCR-строки, которые перепрыгнули через пустой gutter колонок."""
    if not lines:
        return lines
    if rw <= 0:
        rw = max((int(p["box"][2]) for p in lines), default=0) + 8
    gutters = page_column_gutters(lines, rw)
    if not gutters:
        return lines
    out: list[dict] = []
    tlog(f"split-cross-column-enter n={len(lines)} gutters={len(gutters)}")
    for par in lines:
        x1, y1, x2, y2 = (int(v) for v in par["box"])
        # A paragraph that was deliberately joined is one box on screen and gets
        # one card. Its middle looks exactly like a gutter - the gap between two
        # wrapped lines - and cutting there put a card on each half with a gap
        # between them, which is the defect the join was made to remove.
        if par.get("pin_box"):
            out.append(par)
            continue
        # Не резать короткие подписи/заголовки: они — одна логическая единица.
        # "NEW ILLUSTRATION IS NOW UNLOCKED!!" разбивался на фрагменты,
        # каждый переводился отдельно, и "IS NOW UNLOCKED!!" оставался на английском.
        src_text = str(par.get("text", "")).strip()
        # Не резать если: нет переносов строк И текст короткий (< 120 симв) И нет явных табуляций
        if (chr(10) not in src_text and chr(13) not in src_text
                and len(src_text) < 120
                and chr(9) not in src_text):
            tlog(f"split-cross-column-skip single-line len={len(src_text)} '{src_text[:40]}'")
            out.append(par)
            continue
        tlog(f"split-cross-column-process len={len(src_text)} nl={chr(10) in src_text} cr={chr(13) in src_text} tab={chr(9) in src_text} len_ok={len(src_text) < 120} '{src_text[:40]}'")
        cuts = box_crosses_gutter(x1, x2, gutters)
        if not cuts:
            out.append(par)
            continue
        bounds = [x1] + cuts + [x2]
        widths = [max(8, bounds[i + 1] - bounds[i]) for i in range(len(bounds) - 1)]
        parts = split_text_by_segment_widths(str(par.get("text", "")), widths)
        if len(parts) < 2:
            out.append(par)
            continue
        # выровнять число кусков и сегментов
        while len(parts) < len(widths):
            parts.append("")
        parts = parts[: len(widths)]
        for i, piece in enumerate(parts):
            if not piece.strip():
                continue
            sx1 = bounds[i] + (4 if i > 0 else 0)
            sx2 = bounds[i + 1] - (4 if i < len(widths) - 1 else 0)
            if sx2 <= sx1 + 8:
                continue
            copy = dict(par)
            copy["text"] = piece.strip()
            copy["box"] = (sx1, y1, sx2, y2)
            copy["line_height"] = int(par.get("line_height", max(8, y2 - y1)))
            out.append(copy)
        tlog(
            f"split-columns n={len(parts)} gutters={len(cuts)} "
            f"w={x2 - x1} src_chars={len(str(par.get('text', '')))}"
        )
        tdetail(f"split-columns src={str(par.get('text', ''))[:40]!r}")
    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return out


def image_column_gutters(region_img: Image.Image) -> list[tuple[int, int]]:
    """Белые вертикальные желоба МЕЖДУ колонками (не пустоты справа у left-align текста)."""
    try:
        import numpy as np
    except Exception:
        return []
    w, h = region_img.size
    if not wide_enough_for_columns(w, h) or h < 160:
        return []
    # зона абзаца; если кроп уже без картинок — берём почти весь
    y0 = int(h * (0.08 if h < 480 else 0.32))
    y1 = int(h * 0.88)
    if y1 - y0 < 40:
        return []
    band = region_img.convert("RGB").crop((0, y0, w, y1))
    arr = np.asarray(band, dtype=np.float32)
    lum = arr.mean(axis=2)
    col_mean = lum.mean(axis=0)
    ink = col_mean < 210.0
    white = col_mean >= 242.0
    margin = max(48, int(w * 0.06))
    min_gutter = max(36, int(w * 0.035))
    min_ink_run = max(40, int(w * 0.08))
    gutters: list[tuple[int, int]] = []
    x = margin
    while x < w - margin:
        if bool(white[x]):
            x0 = x
            while x < w - margin and bool(white[x]):
                x += 1
            x1 = x
            if x1 - x0 < min_gutter:
                continue

            def ink_run(a: int, b: int, reverse: bool = False) -> int:
                xs = range(b - 1, a - 1, -1) if reverse else range(a, b)
                n = 0
                for i in xs:
                    if ink[i]:
                        n += 1
                    elif n:
                        break
                return n

            left_ink = ink_run(margin, x0, reverse=True)
            right_ink = ink_run(x1, w - margin, reverse=False)
            if left_ink >= min_ink_run and right_ink >= min_ink_run:
                gutters.append((int(x0), int(x1)))
        else:
            x += 1
    if len(gutters) > 3:
        gutters = sorted(gutters, key=lambda g: g[1] - g[0], reverse=True)[:2]
        gutters.sort()
    if gutters:
        tlog(f"img-gutters n={len(gutters)} " + ",".join(f"{a}-{b}" for a, b in gutters[:4]))
    return gutters[:3]


def column_bounds_from_centers(lines: list[dict], rw: int) -> list[tuple[int, int]] | None:
    """Кластер X-центров узких OCR-боксов → границы колонок."""
    xs: list[float] = []
    for p in lines:
        try:
            x1, _y1, x2, _y2 = (int(v) for v in p["box"])
        except Exception:
            continue
        bw = x2 - x1
        if bw < 24 or bw > max(280, int(rw * 0.36)):
            continue
        t = str(p.get("text", "")).strip()
        if len(t) < 5:
            continue
        xs.append((x1 + x2) * 0.5)
    if len(xs) < 5:
        return None
    xs.sort()
    gaps: list[tuple[float, float]] = []
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        if gap >= max(70.0, rw * 0.07):
            gaps.append((gap, (a + b) * 0.5))
    if not gaps:
        return None
    gaps.sort(reverse=True)
    cuts = sorted(int(mid) for _g, mid in gaps[:3])
    cleaned: list[int] = []
    for c in cuts:
        if not cleaned or c - cleaned[-1] >= max(100, int(rw * 0.12)):
            cleaned.append(c)
    if not cleaned:
        return None
    edges = [0] + cleaned + [rw]
    bounds = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    bounds = [(a, b) for a, b in bounds if b - a >= max(120, int(rw * 0.12))]
    if len(bounds) < 2:
        return None
    tlog("center-cols n=%d %s" % (len(bounds), ",".join(f"{a}-{b}" for a, b in bounds)))
    return bounds


def normalize_column_bounds(bounds: list[tuple[int, int]], rw: int) -> list[tuple[int, int]]:
    """Если 3 колонки сильно неровные — выровнять (типичный веб-grid)."""
    if len(bounds) != 3 or not wide_enough_for_columns(rw, max(1, rw // 2)):
        return bounds
    widths = [b - a for a, b in bounds]
    if min(widths) <= 0:
        return bounds
    if max(widths) <= min(widths) * 1.45:
        return bounds
    t = rw // 3
    pad = max(16, int(rw * 0.012))
    even = [
        (0, t + pad),
        (t - pad, 2 * t + pad),
        (2 * t - pad, rw),
    ]
    tlog(f"col-bounds-even {widths} -> thirds")
    return even


def column_x_bounds(rw: int, gutters: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cuts = [0] + [(g0 + g1) // 2 for g0, g1 in gutters] + [rw]
    out: list[tuple[int, int]] = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        if b - a >= 90:
            out.append((a, b))
    return normalize_column_bounds(out, rw)


def finalize_web_column_lines(lines: list[dict], w: int, h: int) -> list[dict]:
    """После col-OCR: kind/body + stitch внутри колонки, без subtitle-пути."""
    ui = filter_game_ui_lines(lines) or [dict(p) for p in lines]
    for p in ui:
        t = str(p.get("text", "")).strip()
        if not t:
            continue
        if looks_like_ui_prompt(t) or looks_like_section_header(t) or len(t) <= 28:
            p.setdefault("kind", "ui")
            p["wrap"] = False
        else:
            p["kind"] = "body"
            p["wrap"] = True
            p["pin_box"] = True
    ui = stitch_ui_body_paragraphs(ui)
    ui = dedupe_near_ui_lines(ui)
    # приоритет: длинные/крупные блоки раньше — быстрее «видимый» перевод
    ui.sort(
        key=lambda p: (
            -(int(p["box"][2]) - int(p["box"][0])) * max(8, int(p["box"][3]) - int(p["box"][1])),
            p["box"][1],
            p["box"][0],
        )
    )
    return ui[:36]


def stitch_same_baseline(lines: list[dict]) -> list[dict]:
    """Склеивает обрывки одной реплики субтитров в одну строку.
    Не склеивает соседние колонки веб-страницы через пустой gutter."""
    if len(lines) < 2:
        return lines
    rw = max((int(p["box"][2]) for p in lines), default=0) + 8
    gutters = page_column_gutters(lines, rw)
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    out: list[dict] = []
    # A row that was deliberately broken apart stays broken apart. Marked pieces
    # are separate controls - a nav bar read as one line and split by its gaps -
    # and this pass would otherwise put them back into the single label they
    # came from. The check is on both sides of the pair: either end being marked
    # is enough, because the fragment that follows a marked one is a neighbour,
    # not a continuation of it.
    cur = dict(ordered[0])
    if cur.get("no_stitch"):
        out.append(cur)
        cur = None
    for nxt in ordered[1:]:
        if cur is None:
            cur = dict(nxt)
            if cur.get("no_stitch"):
                out.append(cur)
                cur = None
            continue
        if nxt.get("no_stitch"):
            out.append(cur)
            cur = dict(nxt)
            out.append(cur)
            cur = None
            continue
        cx1, cy1, cx2, cy2 = cur["box"]
        nx1, ny1, nx2, ny2 = nxt["box"]
        lh = max(8.0, (cur["line_height"] + nxt["line_height"]) / 2.0)
        same_row = abs(((cy1 + cy2) / 2) - ((ny1 + ny2) / 2)) <= lh * 0.6
        h_gap = nx1 - cx2
        jp = bool(RE_JPN.search(str(cur.get("text", "")) + str(nxt.get("text", ""))))
        # субтитры/JP — шире; латиница/веб — уже, иначе 3 колонки → одна строка
        max_gap = max(56, lh * 3.0) if jp else max(22, lh * 1.25)
        span = nx2 - cx1
        if (
            not jp
            and gutters
            and gap_hits_gutter(cx2, nx1, gutters)
        ) or not jp and span >= max(280, int(rw * 0.45)) and h_gap > max(16, lh * 0.9):
            merge_ok = False
        else:
            merge_ok = same_row and -8 <= h_gap <= max_gap
        if merge_ok:
            cur["text"] = f"{cur['text'].rstrip()} {nxt['text'].lstrip()}"
            cur["box"] = (min(cx1, nx1), min(cy1, ny1), max(cx2, nx2), max(cy2, ny2))
            cur["line_height"] = int(round((cur["line_height"] + nxt["line_height"]) / 2))
            cur["conf"] = (float(cur.get("conf", 0)) + float(nxt.get("conf", 0))) / 2
        else:
            out.append(cur)
            cur = dict(nxt)
    if cur is not None:
        out.append(cur)
    return out


def stitch_word_gaps(lines: list[dict]) -> list[dict]:
    """Rejoins one label the recogniser returned as separate words.

    Interface text is not stitched by row, because a row is often several
    controls. A word space is not the gap between two controls, though: it is a
    third of the line's height, and controls in a row stand at least a line
    apart. "Magical" and "Seals" eight pixels apart on a 25-pixel line came
    back as two cards in two sizes, with the second word translated on its own.
    """
    if len(lines) < 2:
        return lines
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    used = [False] * len(ordered)
    out: list[dict] = []
    for i, base in enumerate(ordered):
        if used[i]:
            continue
        used[i] = True
        cur = dict(base)
        grown = True
        while grown and not cur.get("no_stitch") and not RE_JPN.search(str(cur.get("text", ""))):
            grown = False
            cx1, cy1, cx2, cy2 = cur["box"]
            ch = max(1, cy2 - cy1)
            for j in range(i + 1, len(ordered)):
                if used[j]:
                    continue
                nxt = ordered[j]
                if nxt.get("no_stitch") or RE_JPN.search(str(nxt.get("text", ""))):
                    continue
                nx1, ny1, nx2, ny2 = nxt["box"]
                nh = max(1, ny2 - ny1)
                lh = max(8.0, min(ch, nh))
                if abs(ch - nh) > lh * 0.2:
                    continue
                if abs((cy1 + cy2) - (ny1 + ny2)) * 0.5 > lh * 0.25:
                    continue
                gap = nx1 - cx2
                if not (-2 <= gap <= lh * 0.45):
                    continue
                cur["text"] = f"{str(cur['text']).rstrip()} {str(nxt['text']).lstrip()}"
                cur["box"] = (min(cx1, nx1), min(cy1, ny1), max(cx2, nx2), max(cy2, ny2))
                cur["conf"] = (float(cur.get("conf", 0)) + float(nxt.get("conf", 0))) / 2
                used[j] = True
                grown = True
                break
        out.append(cur)
    return out


def is_static_ui_overlay(blocks: list[dict] | None) -> bool:
    """Много лейблов меню/настроек — нельзя hide-речекать как VN-диалог."""
    blocks = list(blocks or [])
    if len(blocks) >= 6:
        return True
    ui_n = 0
    dlg_n = 0
    short_n = 0
    for b in blocks:
        kind = str(b.get("kind", "") or "")
        src = str(b.get("source") or b.get("text") or "")
        compact = re.sub(r"\s+", "", src)
        if kind in ("dialogue", "dialogue-line", "body"):
            dlg_n += 1
        else:
            ui_n += 1
        if 1 <= len(compact) <= 32:
            short_n += 1
    if ui_n >= 4 and ui_n >= dlg_n:
        return True
    return short_n >= 5 and len(blocks) >= 5


def is_menu_or_nav_line(text: str) -> bool:
    """Отдельные кнопки/пункты меню — не склеивать в один абзац."""
    t = text.strip()
    if not t or len(t) > 58:
        return False
    if t[0].islower():
        return False
    if re.match(
        r"^(How|What|Why|When|Where|List|New|Home|Grammar|Phonetics|Phrases|"
        r"Vocabulary|Idioms|Writing|Miscellany|Kids|Hobby|Messages|A Sample)\b",
        t,
        re.I,
    ):
        return True
    words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9'-]+", t)]
    if not words:
        return False
    # навбар: 1–4 коротких слова с заглавной
    if len(words) <= 4 and all(w[0].isupper() for w in words if w[:1].isalpha()) and len(t) <= 40:
        return True
    # сайдбар-ссылки вроде "Russian and English Proverbs"
    if len(words) <= 7 and len(t) <= 48 and not t.endswith((",", "—")):
        if t.startswith(("How ", "What ", "List ", "New ")):
            return True
    return False


def is_mostly_target(text: str, target_lang: str | None = None) -> bool:
    """Строка написана в основном на языке назначения.

    Порог выше, чем в `skip_source`, и намеренно: здесь отвечают «это перевод
    или это эхо/мусор», а не «стоит ли переводить». Мусор на кириллице и мусор
    на латинице выглядят одинаково, и оба должны отсеиваться.

    Язык назначения не задан - берётся из состояния. Слой разметки ниже
    сессии и не может её спросить, не сделав цикл.
    """
    lang = target_lang if target_lang is not None else target_language()
    script = script_of(lang)
    if script is None:
        return False
    return share_in_script(text, script) >= 0.72


def is_mostly_russian(text: str) -> bool:
    """Остаётся для мест, где кириллица - свойство текста, а не язык.

    Например: русская локализация игры на экране. Она «по-русски» независимо
    от того, на какой язык настроен перевод, и её распознавание должно работать
    одинаково при любом `target_lang`.
    """
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 2:
        return False
    cyr = sum(1 for c in letters if RE_CYR.match(c))
    return (cyr / len(letters)) >= 0.72


def is_desktop_chrome(text: str) -> bool:
    """Wayland/desktop notifications, screenshot paths, YouTube chrome — not game UI."""
    t = text.strip()
    low = t.casefold()
    needles = (
        "just now", "скрин", "screen captured", "pictures/screen",
        "весь экран", "becb 3kpa", "весъ экран", "/home/",
        "shirrako", "hactponkm", "hactponk", "ckpmh",
        "xaktnohkm", "xaktponkm", "pactures", "scree",
        "becb sxpa", "gameplay walkthrough", "walkthrough full",
    )
    if any(n in low for n in needles):
        return True
    # SHIRRA / SHIRRAKO watermark (OCR: SHIRRA, SHIRRAS, SH1RRA, HIRR4, ISHIRRAS…)
    compact_alnum = re.sub(r"[^a-z0-9]", "", low.replace("1", "i").replace("4", "a"))
    if "shirr" in compact_alnum or compact_alnum in {"hirra", "hirras", "shirra", "shirras", "shirrao"}:
        return True
    if re.search(r"\bsh[i1l]?rr?a+[sko4]*\b", low):
        return True
    if re.fullmatch(r"sh[i1l]?rr?[ao0sk4]+", low.replace(" ", "")):
        return True
    # Screenshot notification paths (any user): /home/<user>/Pictures/...
    if re.search(r"/home/\w+/Pictures", t):
        return True
    if re.search(r"→\s*/", t):
        return True
    compact = re.sub(r"\s+", "", t)
    if 5 <= len(compact) <= 14 and RE_LAT.fullmatch(compact):
        vowels = sum(1 for c in compact.lower() if c in "aeiouy")
        if vowels <= 1 and not re.search(
            r"(obtained|head|town|tips|new|tap|member|story|live|gacha|quest|combat)", low
        ):
            return True
    # длинный YouTube-тайтл поверх геймплея
    if len(t) >= 36 and t.isupper() and " " in t and sum(c.isalpha() for c in t) >= 24:
        if any(w in low for w in ("online", "gameplay", "walkthrough", "full", "episode")):
            return True
    return False


def is_known_gameplay_hud_phrase(text: str) -> bool:
    """HUD-фразы, которые is_garbage_ocr иначе убивает (Switch / 350 x Col)."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if re.match(
        r"^(Obtained|Got|Received|Waiting|Exploring|Blinded|Head to|Go to|In Combat|"
        r"Quest|Objective|Proceed|Search for|Guard|Map|Settings|Change Action|"
        r"Main Menu|Standard)\b",
        t,
        re.I,
    ):
        return True
    if re.search(r"\b\d+\s*x\s*Col\.?\b", t, re.I):
        return True
    if re.search(r"\bwaiting\s+to\s+switch\b", t, re.I):
        return True
    if re.search(r"\bsearch\s+for\s+the\b", t, re.I):
        return True
    if t.lower() in {"guard", "map", "settings", "argo", "iori"}:
        return True
    return False


def merge_paragraphs(lines: list[dict]) -> list[dict]:
    """Склеивает строки абзаца. Кнопки меню и пункты списка не трогает."""
    if not lines:
        return []
    out: list[dict] = []
    cur = {
        "text": lines[0]["text"],
        "box": lines[0]["box"],
        "line_height": int(lines[0]["line_height"]),
        "conf": float(lines[0].get("conf", 0)),
        "engine": lines[0].get("engine", "tesseract"),
        "_n": 1,
    }
    for nxt in lines[1:]:
        cx1, cy1, cx2, cy2 = cur["box"]
        nx1, ny1, nx2, ny2 = nxt["box"]
        gap = ny1 - cy2
        # The height of one line, not of the box. line_height is the box height
        # when a block already spans several lines and one line's worth when it
        # does not, so comparing the two field values compares a three-line box
        # against a single line and always fails: a paragraph whose first line
        # was recognised as one tall box never continued into the next.
        cur_lh = max(8.0, min(float(cur["line_height"]), max(8, cy2 - cy1)))
        nxt_lh = max(8.0, min(float(nxt["line_height"]), max(8, ny2 - ny1)))
        lh = max(8.0, (cur_lh + nxt_lh) / 2.0)
        cur_w = max(1, cx2 - cx1)
        nxt_w = max(1, nx2 - nx1)
        left_ok = abs(cx1 - nx1) <= max(18, lh * 1.0)
        height_ok = abs(cur_lh - nxt_lh) <= max(5, lh * 0.45)
        col_ok = abs((cx1 + cx2) / 2 - (nx1 + nx2) / 2) <= max(cur_w, nxt_w) * 0.5
        width_ok = nxt_w >= cur_w * 0.4 and nxt_w <= cur_w * 1.8
        menu_block = is_menu_or_nav_line(cur["text"]) or is_menu_or_nav_line(nxt["text"])
        # Продолжение абзаца: со строчной, после запятой - или когда строка
        # заполнена до края и кончается точкой. Полнота говорит, что это
        # перенос, а не новая мысль: список правил, разбитый на две строки в
        # одну панель, заканчивался точкой посреди предложения, и вторая
        # половина уезжала отдельной карточкой с зазором посередине.
        cur_r = cur["text"].rstrip()
        cont = (
            nxt["text"][:1].islower()
            or cur_r.endswith((",", "—", "-", "/"))
            or (cur_r.endswith((".", "!", "?")) and cur_w >= lh * 2.6)
        )
        too_tall = (ny2 - cy1) > lh * 6
        if (
            gap >= 0
            and gap <= lh * 0.55
            and left_ok
            and height_ok
            and col_ok
            and width_ok
            and cont
            and not menu_block
            and not too_tall
            and cur["_n"] < 5
        ):
            cur["text"] = f"{cur['text'].rstrip()} {nxt['text'].lstrip()}"
            cy1, cy2 = min(cy1, ny1), max(cy2, ny2)
            cur["box"] = (min(cx1, nx1), cy1, max(cx2, nx2), cy2)
            n = cur["_n"]
            # Averaged per line, not per box: after the merge the box is taller
            # than any single line, so averaging the raw values would let the
            # height drift upward with every line added.
            cur["line_height"] = int(round((cur_lh * n + nxt_lh) / (n + 1)))
            cur["conf"] = (cur["conf"] * n + float(nxt.get("conf", 0))) / (n + 1)
            engines = set(str(cur.get("engine", "tesseract")).split(","))
            engines.add(str(nxt.get("engine", "tesseract")))
            cur["engine"] = ",".join(sorted(e for e in engines if e))
            cur["_n"] = n + 1
        else:
            out.append({k: v for k, v in cur.items() if k != "_n"})
            cur = {
                "text": nxt["text"],
                "box": nxt["box"],
                "line_height": int(nxt["line_height"]),
                "conf": float(nxt.get("conf", 0)),
                "engine": nxt.get("engine", "tesseract"),
                "_n": 1,
            }
    out.append({k: v for k, v in cur.items() if k != "_n"})
    return out


def looks_like_section_header(text: str) -> bool:
    """Заголовок секции/скилла: Twin Embrace / Learn with free resources."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) < 2 or len(t) > 52:
        return False
    if re.search(r"[.!?:,;]$", t):
        return False
    words = [w for w in re.findall(r"[A-Za-z']+", t)]
    if not (1 <= len(words) <= 7):
        return False
    if looks_like_spoken_line(t) and len(t) >= 40:
        return False
    # Title Case / короткое имя навыка
    caps = sum(1 for w in words if w[:1].isupper())
    if caps >= max(1, len(words) - (1 if words[-1].islower() and len(words[-1]) <= 3 else 0)):
        return True
    # marketing H2: "Learn with free resources" / "Join us for exclusive benefits"
    if (
        t[:1].isupper()
        and 3 <= len(words) <= 7
        and not re.match(r"^(The|A|An|I|We|You|This|That|It|There)\b", t)
        and not looks_like_spoken_line(t)
    ):
        return True
    return False


def looks_like_panel_body_line(text: str) -> bool:
    """Строка описания в панели (не заголовок/кнопка): mid-sentence / long prose."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) < 18:
        return False
    if looks_like_ui_prompt(t) or looks_like_section_header(t):
        return False
    # A dialogue choice is a short reply on its own bar, not a paragraph of a
    # tip panel. Treating a short in-character reply as body sized it alone at
    # 40 while the choice above stayed at 24.
    from ..ocr.engine import looks_like_dialogue_choice

    if looks_like_dialogue_choice(t):
        return False
    if looks_like_spoken_line(t) and re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?:", t):
        return False
    # "Damage Multiplier:" is a field's label in a stat list - the word
    # "damage" alone made it a paragraph and gave it a size of its own.
    if re.fullmatch(r"[A-Z][\w'-]*(?:\s+[\w'-]+){0,3}:", t):
        return False
    # продолжение: lowercase / mid-sentence / ends without final punct mid-flow
    if t[:1].islower():
        return True
    if re.search(
        r"\b(the|a|an|to|of|and|or|with|your|for|from|into|which|that|more|higher|"
        r"deals|creates|restored|damage|enemies|assistance|gradually|period|time)\b",
        t,
        re.I,
    ) and (len(t) >= 18 or not re.search(r"[.!?]\s*$", t) or t[:1].islower()):
        return True
    if len(t) >= 36 and not looks_like_section_header(t):
        return True
    return False


def split_title_banner_merges(lines: list[dict]) -> list[dict]:
    """«Start Quest Begin an incomplete…» — title chip + banner, Rapid склеил в одну строку."""
    out: list[dict] = []
    for par in lines:
        raw = str(par.get("text", "")).strip()
        # "Chip then banner" is a shape two chips on one line make when the
        # recogniser joins them. A paragraph is not that shape: every sentence
        # starts with a capital, so the first two words of any entry look like a
        # chip followed by a banner. On a game codex screen this cut "Inactive
        # Safe" off the front of a four-line entry and moved the remainder 140px
        # right, so the left of every line stayed uncovered.
        if len(par.get("line_boxes") or []) >= 2 or "\n" in raw:
            out.append(par)
            continue
        text = re.sub(r"\s+", " ", raw)
        m = re.match(
            r"^((?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+){0,2})\s+([A-Z][a-z][\w'].{18,})$",
            text,
        )
        if not m:
            out.append(par)
            continue
        head, rest = m.group(1).strip(), m.group(2).strip()
        # title короткий; rest — полноценное предложение/инструкция
        if len(head) > 22 or len(rest) < 20:
            out.append(par)
            continue
        # только реальный title-chip (со пробелом) или известный ярлык — не «Selectthe»
        known = {
            "start quest", "start game", "new game", "load game", "main menu",
            "continue", "options", "settings", "select partner",
        }
        if head.casefold() not in known and not (
            " " in head and (looks_like_ui_prompt(head) or looks_like_section_header(head))
        ):
            out.append(par)
            continue
        if looks_like_section_header(rest) and len(rest) <= 36:
            out.append(par)
            continue
        x1, y1, x2, y2 = par["box"]
        total_w = max(1, x2 - x1)
        cut = x1 + max(56, min(int(total_w * 0.22), len(head) * max(7, (y2 - y1) - 2)))
        cut = min(cut, x2 - 120)
        out.append(
            {
                **dict(par),
                "text": head,
                "box": (x1, y1, cut, y2),
                "kind": "ui",
                "oneline": True,
                "wrap": False,
            }
        )
        out.append(
            {
                **dict(par),
                "text": rest,
                "box": (cut + 6, y1, x2, y2),
                "kind": "ui",
                "oneline": True,
                "wrap": len(rest) > 40,
            }
        )
        tdetail(f"split-title-banner '{head}' | '{rest[:42]}'")
    return out


def stitch_ui_body_paragraphs(lines: list[dict]) -> list[dict]:
    """Склеивает соседние строки описания панели в один wrap-блок (вместо стопки карточек)."""
    if len(lines) < 2:
        return lines
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    out: list[dict] = []
    i = 0
    while i < len(ordered):
        cur = dict(ordered[i])
        t0 = str(cur.get("text", "")).strip()
        if not looks_like_panel_body_line(t0):
            out.append(cur)
            i += 1
            continue
        cluster = [cur]
        skipped: list[dict] = []
        j = i + 1
        while j < len(ordered):
            nxt = ordered[j]
            tb = str(nxt.get("text", "")).strip()
            prev = cluster[-1]
            ax1, ay1, ax2, ay2 = prev["box"]
            bx1, by1, bx2, by2 = nxt["box"]
            gap = by1 - ay2
            lh = max(12, ay2 - ay1)
            # Set beside the line just taken rather than below it: they share
            # vertical ground and do not share horizontal extent. A title printed
            # to the left of a description reads as a section header on its own,
            # and ending the paragraph there split the body text in two and drew
            # the tail as its own card. Position, not wording, decides this.
            beside = (
                min(ay2, by2) - max(ay1, by1) > 0
                and min(ax2, bx2) - max(ax1, bx1)
                <= max(8, int(min(ay2 - ay1, by2 - by1) * 0.5))
            )
            if not beside and (
                looks_like_section_header(tb)
                or (
                    looks_like_ui_prompt(tb)
                    and not tb[:1].islower()
                    and len(tb) <= 24
                )
            ):
                break
            if not looks_like_panel_body_line(tb) and not (
                tb[:1].islower()
                or re.match(
                    r"^(enemies|more|higher|gradually|restored|time|the|a|an|of|to|with)\b",
                    tb,
                    re.I,
                )
            ):
                # Something else shares this vertical band: a heading set to the
                # left of a paragraph, a label beside it. The paragraph does not
                # end there, so keep looking rather than ending the cluster.
                # What ends it is the position, which the checks below measure.
                if not beside and gap > max(14, int(lh * 1.35)):
                    break
                # Held back, not dropped: the index moves past it when the
                # cluster is emitted, so it has to be kept to be written out.
                # A title set beside a description is its own line of the layout
                # and still needs its own card.
                skipped.append(dict(nxt))
                j += 1
                continue
            # разные веб-колонки — не склеивать
            if prev.get("col") is not None and nxt.get("col") is not None:
                if prev.get("col") != nxt.get("col"):
                    break
            # одна колонка: похожий left, плотный вертикальный шаг
            if abs(ax1 - bx1) > max(36, int((ax2 - ax1) * 0.18)):
                break
            # Two columns side by side must not be read as one paragraph. The
            # test is how much of the two lines share horizontally, not how wide
            # either is: an absolute cap rejected every long paragraph, because
            # the first line of one is wide by definition and the second line
            # is short. "A modern nation..." and its continuation were split
            # here, and the tail was drawn as its own card.
            overlap = min(ax2, bx2) - max(ax1, bx1)
            if overlap <= 0:
                break
            if overlap < min((ax2 - ax1), (bx2 - bx1)) * 0.28:
                break
            if gap > max(14, int(lh * 1.35)) or gap < -6:
                break
            # новый заголовок секции после точки — не клеить
            if re.search(r"[.!?]\s*$", str(prev.get("text", ""))) and looks_like_section_header(tb):
                break
            cluster.append(dict(nxt))
            j += 1
        if len(cluster) == 1:
            out.append(cur)
            i += 1
            continue
        texts = [str(p.get("text", "")).strip() for p in cluster]
        box = (
            min(p["box"][0] for p in cluster),
            min(p["box"][1] for p in cluster),
            max(p["box"][2] for p in cluster),
            max(p["box"][3] for p in cluster),
        )
        merged = dict(cluster[0])
        # One paragraph, one card. The lines join into one string and the
        # per-line boxes are dropped: keeping them as line_boxes makes the
        # translation be split back across them, which put a card on each half
        # of a wrapped sentence with a gap between the two. The panel is one box
        # on screen, so it takes one card, laid out from the merged box and
        # wrapping its own text.
        #
        # What is kept is where the lines were, as rows. A card that does not
        # know the original had two rows lays the translation out on however
        # many lines it happens to wrap to, and the game sheet's two-line
        # description came out as one condensed line of unreadable type across
        # the top of a box built for two. The rows are geometry, not line_boxes:
        # nothing splits this paragraph into several cards.
        merged["text"] = " ".join(texts)
        merged["box"] = box
        merged.pop("line_boxes", None)
        merged["src_rows"] = [
            {
                "text": str(p.get("text", "")).strip(),
                "box": tuple(p["box"]),
                "line_height": int(p.get("line_height", 0) or 0),
            }
            for p in cluster
        ]
        merged["line_height"] = max(10, int((box[3] - box[1]) / max(1, len(cluster))))
        merged["kind"] = "body"
        merged["wrap"] = True
        merged["pin_box"] = True
        merged["oneline"] = False
        tdetail(f"stitch-body n={len(cluster)} y={box[1]}-{box[3]} '{texts[0][:36]}'")
        out.append(merged)
        out.extend(skipped)
        i = j
    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return out


def split_false_nav_merges(lines: list[dict]) -> list[dict]:
    """Только меню File/Edit/View/Help. New Chat / Search НЕ режем — иначе точки в узких боксах.

    Также: «History Are you sure…» — сайдбар + попап, склеенные RapidOCR в один bbox.
    """
    menu_tokens = {
        "file", "edit", "view", "help", "go", "run", "terminal", "selection",
    }
    sidebar_heads = (
        "History", "Achievements", "Preferences", "Main Menu", "Language",
        "About", "Quit", "Return", "Display", "Skip", "Window", "Start Quest",
    )
    out: list[dict] = []
    for par in lines:
        text = par["text"].strip()
        # сайдбар + модалка на одной строке OCR
        split_dialog = None
        for head in sidebar_heads:
            if text.casefold().startswith(head.casefold() + " "):
                rest = text[len(head) :].strip()
                if len(rest) >= 18 and (
                    looks_like_spoken_line(rest)
                    or rest[:1].isupper()
                    or rest.casefold().startswith(("are ", "do ", "this ", "you ", "begin ", "select "))
                ):
                    split_dialog = (head, rest)
                    break
        if split_dialog:
            head, rest = split_dialog
            x1, y1, x2, y2 = par["box"]
            total_w = max(1, x2 - x1)
            # левый кусок ≈ ширина head, правый — фраза попапа
            cut = x1 + max(48, min(int(total_w * 0.18), len(head) * max(8, (y2 - y1))))
            cut = min(cut, x2 - 80)
            out.append(
                {
                    **dict(par),
                    "text": head,
                    "box": (x1, y1, cut, y2),
                    "kind": "ui",
                    "oneline": True,
                    "wrap": False,
                }
            )
            out.append(
                {
                    **dict(par),
                    "text": rest,
                    "box": (cut + 4, y1, x2, y2),
                    "kind": "ui",
                    "oneline": True,
                    "wrap": len(rest) > 36,
                }
            )
            tdetail(f"split-sidebar-dialog '{head}' | '{rest[:40]}'")
            continue
        words = text.split()
        # меню IDE: File Edit View Help / View Help — резать
        menu_merge = (
            len(words) >= 2
            and len(words) <= 8
            and all(re.sub(r"[^A-Za-z]", "", w).casefold() in menu_tokens for w in words)
        )
        if not menu_merge:
            out.append(par)
            continue
        x1, y1, x2, y2 = par["box"]
        total_w = max(1, x2 - x1)
        weights = [max(2, len(w)) for w in words]
        sw = sum(weights)
        cursor = x1
        for i, w in enumerate(words):
            ww = int(total_w * weights[i] / sw)
            right = x2 if i == len(words) - 1 else cursor + max(12, ww)
            out.append(
                {
                    "text": w,
                    "box": (cursor, y1, right, y2),
                    "line_height": par["line_height"],
                    "conf": par.get("conf", 0),
                    "engine": par.get("engine"),
                    "kind": "ui",
                    "oneline": True,
                }
            )
            cursor = right
    return out


def split_vn_speaker_line(
    par: dict, region_img: Image.Image | None
) -> list[dict]:
    """Имя говорящего и реплика на одной базовой линии - разные элементы.

    Распознаватель отдаёт их одним боксом: обе строки стоят на одной высоте,
    и между ними нет переноса, за который можно было бы зацепиться. Живой
    случай, 2026-10-04:

        Distant Voice      For making you suffer again...

    Два бокса от OCR по узкой полосе, один - по полному кадру. Дальше имя
    уезжало в перевод: «Далекий голос, за то, что заставил тебя снова
    страдать» - одна карточка вместо двух.

    Различать нечем по словам: «Distant Voice For making you suffer again» -
    обычная английская фраза. Различимо по промежутку. Замерено на том же
    кадре: между буквами 3px, между словами 12-14px, между именем и репликой
    79px. Промежуток между элементами в разы шире пробела между словами в
    той же строке, и это единственный признак, который есть и у кнопок, и у
    имени.

    Резать можно не всякий широкий промежуток: у перенесённой фразы разрывы
    между словами одинаковые, и самый широкий из них ничего не значит. Поэтому
    сравниваются два самых широких разрыва: граница элементов выделяется тем,
    что она заметно шире остальных, а не тем, что она просто есть.
    """
    if region_img is None:
        return [par]
    text = str(par.get("text", "")).strip()
    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) < 3:
        return [par]
    x1, y1, x2, y2 = (int(v) for v in par["box"])
    region_w, region_h = region_img.size
    crop_box = (
        max(0, min(x1, region_w - 1)),
        max(0, min(y1, region_h - 1)),
        max(0, min(x2, region_w)),
        max(0, min(y2, region_h)),
    )
    if crop_box[2] - crop_box[0] < 40 or crop_box[3] - crop_box[1] < 10:
        return [par]
    ink_cols = _ink_column_profile(region_img.crop(crop_box))
    if not ink_cols:
        return [par]
    gaps = _empty_column_runs(ink_cols)
    if len(gaps) < 2:
        return [par]
    widths = sorted((g[1] - g[0] + 1 for g in gaps), reverse=True)
    widest, runner_up = widths[0], widths[1]
    median_letter = sorted(g[1] - g[0] + 1 for g in gaps)[len(gaps) // 2]
    # starting threshold: gap > max(1.5 * glyph height, 16).
    from .name_gap import name_dialogue_gap_threshold

    glyph_h = max(8.0, float(y2 - y1) * 0.55)
    name_gap_floor = name_dialogue_gap_threshold(glyph_h)
    # Граница элементов: заметно шире второго разрыва и не межбуквенный шум.
    # Also must clear the name/dialogue floor so a slightly wide word gap never splits.
    if (
        widest < max(8, median_letter * 4, name_gap_floor)
        or widest < runner_up * 2.5
    ):
        return [par]
    cut = next(g for g in gaps if g[1] - g[0] + 1 == widest)
    left_lo, right_hi = cut[0], cut[1] + 1
    if not any(ink_cols[:left_lo]) or not any(ink_cols[right_hi:]):
        return [par]
    abs_lo, abs_hi = crop_box[0] + left_lo, crop_box[0] + right_hi
    head, tail = _split_words_at_gap(words, left_lo, right_hi, len(ink_cols))
    # Слева должен быть именно говорящий, причём написанный с заглавной.
    # `looks_like_speaker_prefix` проверяет форму слова перед двоеточием и там
    # снисходительна намеренно: двоеточие уже отделяет имя от реплики, и
    # «obtained the» проходит. Здесь двоеточия нет, строка сама себе имя, и
    # без заглавной это просто текст с большим пробелом посередине.
    if not head or not tail or not head[:1].isupper():
        return [par]
    if not looks_like_speaker_prefix(head):
        return [par]
    # Границы берутся по чернилам, а не с отступом: карточка обязана закрыть
    # оригинал целиком. `left_lo` - первый пустой столбец, то есть последний
    # столбец с чернилами плюс один; `right_hi` - первый столбец с чернилами
    # после разрыва. Отступ в 4 пикселя уходил внутрь и оставлял на экране
    # 6px английского «Voice» справа и 14px буквы «F» слева - тонкие полоски
    # рядом с переводом.
    return [
        {**dict(par), "text": head, "box": (x1, y1, abs_lo, y2), "kind": "ui",
         "oneline": True, "wrap": False},
        {**dict(par), "text": tail, "box": (abs_hi, y1, x2, y2)},
    ]


def _split_words_at_gap(
    words: list[str], gap_lo: int, gap_hi: int, total_w: int
) -> tuple[str, str]:
    """Режет список слов по разрыву, ближайшему к середине промежутка.

    Доли ширин колонок не хватает: они берут среднее слово на всю ширину и
    «Distant Voice | For making you suffer again» делили на «Distant Voice»,
    «For» и «making you suffer again» - хвост терялся. Ширина слова
    оценивается по числу букв, граница ищется та, что ближе всех к центру
    разрыва; на том же кадре это граница после «Voice».
    """
    if len(words) < 2 or gap_hi <= gap_lo:
        return "", ""
    chars = [len(w) for w in words]
    total_chars = sum(chars) or 1
    centre = (gap_lo + gap_hi) / 2.0
    best_at = None
    best_dist = None
    acc = 0
    for i, n in enumerate(chars[:-1]):
        acc += n
        edge = acc / total_chars * total_w
        dist = abs(edge - centre)
        if best_dist is None or dist < best_dist:
            best_at, best_dist = i, dist
    if best_at is None:
        return "", ""
    return " ".join(words[: best_at + 1]), " ".join(words[best_at + 1:])


def split_button_rows(
    lines: list[dict], region_img: Image.Image | None
) -> list[dict]:
    """Разбивает строку кнопок на отдельные, если промежутки между ними пустые.

    Ряд навигации в углу экрана - это несколько самостоятельных кнопок, а не одна
    надпись. Распознаватель отдаёт их одной строкой, и перевод приходил на всё
    сразу: "AUTO OFF SKIP" читалось как один ярлык, а на экране оставалось
    "АУТ" - обрезок первой кнопки плюс весь перевод, наложенный на остальные.

    Что отличает кнопки от фразы - не слова, а промежутки. У надписи пробелы
    внутри строки узкие, у кнопок они широкие, потому что каждая занимает
    отдельное место. Меряем это по пикселям: пустой вертикальный столбец
    шире среднего пробела и заметно выше кегля - это граница между кнопками.
    """
    if region_img is None or not lines:
        return lines
    out: list[dict] = []
    for par in lines:
        text = str(par.get("text", "")).strip()
        words = text.split()
        x1, y1, x2, y2 = (int(v) for v in par["box"])
        # три слова - минимум для ряда; две кнопки читаются как фраза
        if (
            len(words) < 3
            or len(words) > 8
            or y2 - y1 < 10
            or not is_menu_or_nav_line(text)
            or looks_like_spoken_line(text)
        ):
            out.append(par)
            continue
        region_w, region_h = region_img.size
        crop_box = (
            max(0, min(x1, region_w - 1)),
            max(0, min(y1, region_h - 1)),
            max(0, min(x2, region_w)),
            max(0, min(y2, region_h)),
        )
        if crop_box[2] - crop_box[0] < 20 or crop_box[3] - crop_box[1] < 6:
            out.append(par)
            continue
        crop = region_img.crop(crop_box)
        ink_cols = _ink_column_profile(crop)
        if not ink_cols:
            out.append(par)
            continue
        gaps = _empty_column_runs(ink_cols)
        # Базовый масштаб - самые узкие разрывы, то есть пробелы между буквами
        # внутри слова. Промежуток между кнопками в разы шире: у "AUTO OFF SKIP"
        # буквенные разрывы 4-7 пикселей, между кнопками 43 и 46.
        widths = sorted(g[1] - g[0] + 1 for g in gaps)
        letter_gap = widths[len(widths) // 2] if widths else 0
        box_w = max(1, x2 - x1)
        # Высота бокса не годится как мера: распознаватель возвращает его с
        # запасом, и кегль получается завышенным. Порог от буквенного разрыва
        # и от ширины строки - то, что не зависит от того, насколько широко
        # распознаватель обвёл надпись.
        min_gap = max(10, int(letter_gap * 3.5), int(box_w * 0.06))
        cuts = [g for g in gaps if (g[1] - g[0] + 1) >= min_gap]
        # обрез у края строки - не граница между кнопками
        cuts = [g for g in cuts if g[0] > 2 and g[1] < len(ink_cols) - 3]
        if not cuts:
            out.append(par)
            continue
        # Края берём по чернилам, а не по боксу: распознаватель возвращает
        # прямоугольник с запасом, и без обрезки последняя кнопка получала
        # кусок пустоты впридачу, а её текст уезжал в соседний кусок.
        ink_x = [i for i, has in enumerate(ink_cols) if has]
        line_lo = crop_box[0] + ink_x[0]
        line_hi = crop_box[0] + ink_x[-1] + 1
        # режем по центрам промежутков. Промежутки заданы в координатах кропа,
        # а карточка живёт на экране.
        edges = (
            [line_lo]
            + [crop_box[0] + int((g[0] + g[1]) / 2) + 1 for g in cuts]
            + [line_hi]
        )
        pieces: list[dict] = []
        for a, b in zip(edges, edges[1:]):
            piece = text_by_x_range(words, line_lo, line_hi, a, b)
            if not piece:
                continue
            # Бокс куска - по чернилам этого куска, а не по границам промежутков.
            # Иначе карточка закрывает пустое поле и уезжает на соседнюю
            # надпись: "АВТО" рисовалось правее, чем стояло "AUTO", и не
            # накрывало его, а "ПРОПУСТИТЬ" вылезало на строку выше.
            lo = a - crop_box[0]
            hi = b - crop_box[0]
            own = [i for i in range(max(0, lo), min(len(ink_cols), hi)) if ink_cols[i]]
            if own:
                a = crop_box[0] + own[0]
                b = crop_box[0] + own[-1] + 1
            pieces.append(
                {
                    **dict(par),
                    "text": piece,
                    "box": (a, y1, b, y2),
                    "kind": "ui",
                    "oneline": True,
                    "wrap": False,
                    # Пропуск склейки: строка кнопок уже разделена, и проход,
                    # который собирает обрывки одной реплики, склеивает их
                    # обратно - как он и склеивал три кнопки в одну до того,
                    # как их разделили.
                    "no_stitch": True,
                }
            )
        if len(pieces) < 2:
            out.append(par)
            continue
        tdetail(f"split-button-row n={len(pieces)} '{text[:32]}'")
        out.extend(pieces)
    return out


def stitch_paragraph_lines(lines: list[dict]) -> list[dict]:
    """Join the lines of a paragraph, which arrive one box at a time.

    This is its own pass and runs before the row grouping rather than inside it,
    and that ordering is the whole point: the two questions are different and
    answering them in the same loop answers both wrongly. The row grouping asks
    "what sits side by side", and a paragraph is not side by side with anything.
    Asking it "and what continues below" as well put the paragraph's lines and a
    column of labels into one row, sorted left to right and joined end to end - a
    label and a paragraph concatenated into one sentence, worse than either
    defect alone.

    Two boxes are consecutive lines of one flow when they share most of their
    width, the second is a good fraction of a line height below the first, the gap
    is smaller than a line is tall, and the first does not end a sentence. The
    fraction of a line height is what keeps two items side by side out of it,
    since those are a hair apart vertically.
    """
    if len(lines) < 2:
        return lines
    order = sorted(range(len(lines)), key=lambda i: lines[i]["box"][1])
    used = [False] * len(lines)
    out: list[dict] = []
    for i in order:
        if used[i]:
            continue
        used[i] = True
        group = [lines[i]]
        grew = True
        while grew:
            grew = False
            for j in order:
                if used[j]:
                    continue
                if any(_continues_flow(lines[j], g) for g in group):
                    group.append(lines[j])
                    used[j] = True
                    grew = True
                    break
        out.append(group[0] if len(group) == 1 else _merge_flow(group))
    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return out


def split_leading_name(region_img: Image.Image | None, par: dict) -> list[dict]:
    """Pull a speaker's name off the front of the line it stands beside.

    A nameplate and the sentence it labels are two elements, and the recogniser
    gives them back as one: a name plate followed by the line it labels arrives
    as a single box, and translating it as one sentence puts the character's
    name in the middle of her own line and translates it as if it were part of
    what she said.

    What is inside the box gives it away. The name and the sentence it labels
    have a gap between them, and it is a wide one - a person's name set in the
    margin, then space, then the line - while the gaps between words inside the
    sentence are narrow. On that box the widest gap is 79 pixels against a line
    63 tall, and it is the only one anywhere near that size; the line above has
    none at all.

    The name itself has to look like a name: one or two words, short, and with no
    punctuation in it. Without that, any sentence with a wide gap in it would be
    cut in half.
    """
    if region_img is None:
        return [par]
    if abs(float(par.get("angle", 0.0) or 0.0)) >= 1.0:
        # A slanted row is one line, and its geometry already knows where the
        # lines are. Cutting one here would cut a label out of a row of labels.
        return [par]
    text = str(par.get("text", "")).strip()
    box = par["box"]
    height = box[3] - box[1]
    if height < 16 or " " not in text:
        return [par]
    crop = _metrics_crop(region_img, box)
    if crop is None:
        return [par]
    try:
        mask, _med = _ink_mask(crop)
        if mask is None or not mask.any():
            return [par]
        cols = mask.any(axis=0)
        runs: list[tuple[int, int]] = []
        start = None
        for i, on in enumerate(cols):
            if on and start is None:
                start = i
            elif not on and start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, len(cols)))
        gaps = [
            (runs[i][1], runs[i + 1][0])
            for i in range(len(runs) - 1)
            if runs[i + 1][0] - runs[i][1] >= max(8, height * 0.3)
        ]
        if not gaps:
            return [par]
        gx0, gap_end = min(gaps, key=lambda g: g[0])
        # A heavy display face spaces its words at a third of the line height,
        # so that alone does not make a gap a name's margin: on a list of
        # "NEW ILLUSTRATION IS NOW UNLOCKED!!" rows it cut "NEW" off as a
        # speaker. The margin is the gap that stands out from the line's own
        # word spaces; with no other space to compare, it has to be wide.
        word_gaps = [
            runs[i + 1][0] - runs[i][1]
            for i in range(len(runs) - 1)
            if runs[i + 1][0] - runs[i][1] >= max(3, height * 0.15)
            and runs[i][1] != gx0
        ]
        if word_gaps:
            if gap_end - gx0 < 1.8 * float(median(word_gaps)):
                return [par]
        elif gap_end - gx0 < height * 0.6:
            return [par]
        ink_w = int(runs[-1][1] - runs[0][0])
        letters = len(text.replace(" ", ""))
        if letters < 4 or ink_w < 8:
            return [par]
        per = ink_w / float(letters)
        # Where the gap falls, counted in characters, has to land on a space. If
        # it lands in the middle of a word then the gap is something inside the
        # text and not the space between a name and what it labels.
        first_gap_at = gx0 - runs[0][0]
        spaces = [i for i, ch in enumerate(text) if ch == " "]
        if not spaces:
            return [par]
        cut = min(spaces, key=lambda i: abs(i * per - first_gap_at))
        if abs(cut * per - first_gap_at) > per * 0.9:
            return [par]
        name = text[:cut].strip()
        rest = text[cut:].strip()
        if not name or not rest:
            return [par]
        if len(name) > 22 or len(name.split()) > 2:
            return [par]
        if any(ch in name for ch in ".?!,;:…\u00bb\"'"):
            return [par]
        # Режем по самому разрыву, а не по оценке ширины букв. Разрыв известен
        # точно - он измерен в пикселях, - и оценка по средней ширине символа
        # копила ошибку на все длины строки: тело уезжало влево, а табличка с
        # именем наезжала на него, потому что считать тут нечего.
        gap_lo = box[0] + gx0
        gap_hi = box[0] + gap_end
        left = dict(par)
        left["text"] = name
        left["box"] = (box[0], box[1], gap_lo, box[3])
        left["kind"] = "name"
        left["line_height"] = height
        right = dict(par)
        right["text"] = rest
        right["box"] = (gap_hi, box[1], box[2], box[3])
        right["line_height"] = height
        right.pop("kind", None)
        return [left, right]
    except Exception:
        return [par]


def _merge_flow(group: list[dict]) -> dict:
    """One paragraph out of the boxes its lines arrived in."""
    group = sorted(group, key=lambda p: p["box"][1])
    x1 = min(p["box"][0] for p in group)
    y1 = min(p["box"][1] for p in group)
    x2 = max(p["box"][2] for p in group)
    y2 = max(p["box"][3] for p in group)
    base = dict(group[0])
    angles = [
        float(p.get("angle", 0.0) or 0.0)
        for p in group
        if float(p.get("angle", 0.0) or 0.0) != 0.0
    ]
    base.update(
        {
            "text": " ".join(str(p.get("text", "")).strip() for p in group).strip(),
            "box": (x1, y1, x2, y2),
            # The block is as tall as its lines, so the height of a line is what
            # the total is divided by - the same division the layout will make.
            "line_height": int(round((y2 - y1) / max(1, len(group)))),
            "conf": min(float(p.get("conf", 0.0) or 0.0) for p in group),
            "band": max(int(p.get("band", 0) or 0) for p in group),
            "angle": (sum(angles) / len(angles)) if angles else 0.0,
            "wrapped_lines": len(group),
        }
    )
    return base


def filter_game_ui_lines(lines: list[dict]) -> list[dict]:
    """Оставляем осмысленные фразы UI, режем чипы статов и мусор."""
    skip_exact = {
        "STR", "VIT", "DEX", "END", "AGI", "MND", "INT", "ATK", "DEF", "HP", "SP",
        "X", "O",
    }
    out = []
    for p in lines:
        t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
        t = re.sub(r"^[?¿]+", "", t).strip()
        if not t or is_desktop_chrome(t) or is_hud_spam(t):
            continue
        if t.upper() in skip_exact or re.fullmatch(r"\d+(/\d+)?", t):
            continue
        if t.casefold() in {"yes", "no", "ok", "on", "off", "back", "buy", "use", "map", "all"}:
            copy = dict(p)
            copy["text"] = t
            copy["kind"] = "ui"
            copy["wrap"] = False
            out.append(copy)
            continue
        if len(t) <= 3 and t.isupper():
            continue
        # короткие чипы вроде "Lv. 9" / "LvIO" OCR
        if re.fullmatch(r"Lv\.?\s*\d+[O0]?", t, re.I):
            continue
        if re.fullmatch(r"LvI[O0]", t, re.I):
            continue
        if len(RE_LAT.findall(t)) < 4 and not re.search(r"[.!?]", t):
            # Skip/Test/Window — коротко, но это меню, не чип статов
            if not (
                is_known_gameplay_hud_phrase(t)
                or looks_like_ui_prompt(t)
                or re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", t)
            ):
                continue
        copy = dict(p)
        copy["text"] = t
        # body/pin — абзац панели; dialogue — VN; иначе ui (длинный wrap ≠ сразу dialogue)
        if str(p.get("kind", "")) == "body" or p.get("pin_box"):
            copy["kind"] = "body"
            copy["wrap"] = True
            copy["pin_box"] = True
        elif str(p.get("kind", "")) == "dialogue" or "\n" in t:
            copy["kind"] = "dialogue"
            copy["wrap"] = True
        else:
            copy["kind"] = "ui"
            copy["wrap"] = len(t) > 28
        if p.get("incomplete"):
            copy["incomplete"] = True
        if p.get("line_boxes"):
            copy["line_boxes"] = p["line_boxes"]
        out.append(copy)
    uniq = dedupe_near_ui_lines(out)
    return cap_ui_lines(uniq)


def extract_vn_dialogue_from_band(lines: list[dict], w: int, h: int) -> list[dict]:
    """Быстрый путь для VN-пузыря: нижняя Rapid-полоса уже дала реальные строки.

    Не гоняем full OCR/ink-band повторно и не ждём "полноты", если есть 2+ строки
    одного нижнего блока. Меню-попапы с кнопками сюда не должны попадать.
    """
    if len(lines) < 2 or len(lines) > 4:
        return []
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    cleaned: list[dict] = []
    for p in ordered:
        t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
        if not t or is_desktop_chrome(t) or is_hud_spam(t):
            continue
        if re.fullmatch(r"REMASTERED|SOFTMONEY|Version\s+\d+(?:\.\d+)?", t, re.I):
            continue
        copy = dict(p)
        copy["text"] = t
        cleaned.append(copy)
    if len(cleaned) < 2:
        return []
    prompt_n = sum(1 for p in cleaned if looks_like_ui_prompt(str(p.get("text", ""))))
    if prompt_n >= 2:
        return []
    centers = [((p["box"][1] + p["box"][3]) * 0.5) for p in cleaned]
    if min(centers) < h * 0.48:
        return []
    x1 = min(p["box"][0] for p in cleaned)
    x2 = max(p["box"][2] for p in cleaned)
    if (x2 - x1) < w * 0.24:
        return []
    ok_stack = True
    for a, b in zip(cleaned, cleaned[1:]):
        if not vn_lines_should_merge(a, b):
            ok_stack = False
            break
    if not ok_stack:
        return []
    block = _merge_vn_band_cluster(cleaned)
    text = str(block.get("text", ""))
    letters = len(RE_LAT.findall(text)) + len(RE_CJK.findall(text))
    if letters < 28:
        return []
    block["kind"] = "dialogue"
    block["wrap"] = True
    block.pop("incomplete", None)
    tlog(
        f"vn-band-direct n={len(cleaned)} "
        + " || ".join(str(p.get("text", ""))[:42] for p in cleaned)
    )
    return [block]
