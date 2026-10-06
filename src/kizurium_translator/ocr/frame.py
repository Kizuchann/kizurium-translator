"""Один кадр целиком: какой проход читать и что из него оставить.

Тела перенесены из `live/session.py` дословно. `read_frame` — единственная
дверь, через которую цикл кадра получает строки.
"""
from __future__ import annotations

import re
import time

from PIL import Image

from ..core.scale import wide_enough_for_columns
from ..core.text import (
    RE_LAT,
    SCRIPT_JA,
    SCRIPT_MIXED,
    UI_LINES_MAX,
    _merge_language_halves,
    block_script,
    box_overlap_ratio,
    cap_ui_lines,
    clean_ocr_text,
    game_ui_score,
    is_hud_spam,
    log_eng_ui_lines,
    looks_like_spoken_line,
    normalize_subtitle_ocr,
    ocr_coverage_stats,
    script_cjk_score,
    tdetail,
    tlog,
)
from ..layout.dialogue import merge_spoken_with_hud
from ..layout.grouping import (
    extract_vn_dialogue_from_band,
    filter_game_ui_lines,
    finalize_web_column_lines,
    image_column_gutters,
    is_desktop_chrome,
    looks_like_section_header,
    merge_paragraphs,
    split_cross_column_merges,
)
from ..live.reconcile import cyrillic_probe_due, rapid_ocr_lines, rapid_ocr_lines_cyrillic
from ..ocr.engine import (
    collect_subtitle_blocks,
    english_ocr_quality,
    extract_dialogue_choice_lines,
    is_garbage_ocr,
    is_overlay_echo_ocr,
    looks_like_subtitle_continue,
    normalize_japanese_text,
    stitch_subtitle_fragments,
    subtitle_ocr_lines,
)
from ..ocr.passes import (
    extend_dialogue_with_bottom_pass,
    finish_incomplete_vn_lines,
    merge_rapid_prefer_dialogue,
    ocr_center_modal_panel,
    ocr_eng_ui_boost,
    ocr_region_by_columns,
    ocr_right_panel_toasts,
)
from ..translation.service import (
    filter_plausible_lines,
    finish_mixed_frame,
    is_speaker_name,
    japanese_ocr_quality,
    refine_japanese_blocks,
    require_ocr,
)
from ..typography.metrics import (
    RE_JPN,
    _merge_vn_band_cluster,
    dedupe_near_ui_lines,
    looks_like_game_ui,
    looks_like_ui_prompt,
    merge_subtitle_cluster,
    ui_text_fingerprint,
    unglue_english,
    vn_lines_should_merge,
)


def region_text(region_img, hint: str | None = None) -> str:
    """Plain text for a captured region.

    Uses the same pipeline as the live overlay, so OCR fixes apply to both modes
    and there is no second recognition system to keep in sync.
    """
    require_ocr()
    lines, mode = ocr_image(region_img, hint)
    if not lines:
        return ""

    keep = [p for p in lines if str(p.get("text", "")).strip() and not is_garbage_ocr(str(p.get("text", "")))]
    if not keep:
        return ""

    # Reading order: top to bottom, and left to right inside a line.
    keep.sort(key=lambda p: (p["box"][1] // 12, p["box"][0]))
    paragraphs = merge_paragraphs([dict(p) for p in keep])
    chunks: list[str] = []
    for par in paragraphs or keep:
        text = clean_ocr_text(str(par.get("text", "")))
        text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
        if text:
            chunks.append(text)
    return "\n\n".join(chunks).strip()


def read_frame(region_img: Image.Image, hint: str | None = None) -> tuple[list[dict], str]:
    """Прочитать кадр, а если он пуст - спросить ещё и кириллицу.

    Зонд стоит здесь, а не внутри `ocr_image`, потому что кадр может оказаться
    пустым на любом из десятка ранних выходов, и каждый из них возвращает
    список без единой строки. Здесь виден результат целиком.

    Плата за зонд: полный проход tesseract на кадре 1920x1080 - полторы-две
    секунды. Поэтому он идёт не на каждый кадр, а через
    `CYRILLIC_PROBE_COOLDOWN_S`, и на игровом кадре не срабатывает вовсе -
    там `ocr_image` возвращает строки.
    """
    lines, mode = ocr_image(region_img, hint)
    if lines or not cyrillic_probe_due():
        return lines, mode
    t0 = time.monotonic()
    cyr = rapid_ocr_lines_cyrillic(region_img)
    kept = filter_plausible_lines(cyr, "eng-ui")
    tlog(
        f"cyrillic-probe read={len(cyr)} kept={len(kept)} "
        f"ocr_ms={int((time.monotonic()-t0)*1000)}"
    )
    if not kept:
        return lines, mode
    log_eng_ui_lines("ocr-ui", kept)
    tlog(f"cyrillic-pass n={len(kept)} en={english_ocr_quality(kept):.1f}")
    return cap_ui_lines(kept), mode


def _enrich_if_sparse(region_img: Image.Image, lines: list[dict]) -> list[dict]:
    """Extra recogniser passes only when the first read came back thin.

    A dense menu already has its lines. The boost, the toast pass and the
    modal pass are three more full reads, and that is what makes a busy
    screen sit for several seconds before the first card appears. A thin
    read still gets them: that is the case they exist for.
    """
    if len(lines) >= 16:
        tlog(f"ocr-enrich-skip n={len(lines)}")
        return lines
    lines = ocr_eng_ui_boost(region_img, lines)
    lines = ocr_right_panel_toasts(region_img, lines)
    lines = ocr_center_modal_panel(region_img, lines)
    return lines


def ocr_image(region_img: Image.Image, hint: str | None = None) -> tuple[list[dict], str]:
    t0 = time.monotonic()
    w, h = region_img.size
    # EN/видео: сначала только Rapid (+нижняя полоса). Meiki дорогой и на YouTube не нужен.
    prefer_en = hint in ("eng-ui", "eng-subtitle", "eng", "eng+rus") or hint is None
    video_hint = hint in ("eng-subtitle",) or (isinstance(hint, str) and hint.startswith("eng"))
    band_lines: list[dict] = []
    rapid: list[dict] = []
    # Every unfiltered read of this frame, in one list. The English paths below
    # are allowed to return early; the Japanese that the same multilingual pass
    # already read is taken from here on the way out, so an early return no
    # longer means "and throw the other language away".
    raw_all: list[dict] = []
    # широкий сайт с реальными белыми желобами: колонки. IDE/тёмный UI — обычный OCR.
    if prefer_en and w >= 1000 and h >= 260:
        gutters = image_column_gutters(region_img)
        if gutters:
            col_lines = ocr_region_by_columns(region_img, [])
            raw_all.extend(col_lines)
            if col_lines and len(col_lines) >= 6:
                ui = finalize_web_column_lines(col_lines, w, h)
                log_eng_ui_lines("ocr-ui", ui)
                tlog(
                    f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
                    f"ui=1 col-first cjk={script_cjk_score(ui)} "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}"
                )
                return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)
            tlog("col-first miss -> band")
        else:
            tlog("col-first skip: no web gutters")

    if h >= 260:
        # letterbox-субтитры + choice-меню в середине — нижние ~70%
        # Узкие области и eng-ui меню: с самого верха (Preferences/Display/Achievements header).
        # 260: выделенная колонка сайта (~400h) тоже 2× — иначе RapidOCR теряет строки абзаца.
        y0 = 0 if (w < 720 or hint != "eng-subtitle") else int(h * 0.30)
        band = region_img.crop((0, y0, w, h))
        # 2× только для субтитров/узких кропов — на широком eng-ui это ×4 пикселей и тормоза
        scale_b = 2 if (hint == "eng-subtitle" or w < 720 or h < 420) else 1
        work_band = band if scale_b == 1 else band.resize(
            (band.width * scale_b, band.height * scale_b),
            Image.Resampling.LANCZOS,
        )
        band_raw = rapid_ocr_lines(work_band)
        raw_all.extend(band_raw)
        band_lines = []
        for p in band_raw:
            x1, y1, x2, y2 = p["box"]
            copy = dict(p)
            copy["box"] = (
                int(x1 / scale_b),
                int(y0 + y1 / scale_b),
                int(x2 / scale_b),
                int(y0 + y2 / scale_b),
            )
            copy["line_height"] = max(8, int(p["line_height"] / scale_b))
            if "angle" in p:
                copy["angle"] = p["angle"]
            band_lines.append(copy)
        # На видео сначала только низ — в 2 раза быстрее полный кадр
        band_only = merge_rapid_prefer_dialogue([], band_lines)
        band_only = filter_plausible_lines(band_only, "eng-ui")
        band_only = [
            p
            for p in band_only
            if not is_hud_spam(str(p.get("text", ""))) and not is_desktop_chrome(str(p.get("text", "")))
        ]
        # починка склеек Name:Text + lori→Iori
        for p in band_only:
            p["text"] = unglue_english(normalize_subtitle_ocr(str(p.get("text", ""))))
        # широкий сайт 2–3 колонки: только если есть желоба (не IDE)
        if w >= 1000 and image_column_gutters(region_img):
            col_lines = ocr_region_by_columns(region_img, band_only)
            raw_all.extend(col_lines)
            if col_lines and len(col_lines) >= max(6, len(band_only) // 2):
                ui = finalize_web_column_lines(col_lines, w, h)
                log_eng_ui_lines("ocr-ui", ui)
                tlog(
                    f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
                    f"ui=1 band-use-col-ocr cjk={script_cjk_score(ui)} "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}"
                )
                return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)
            band_only = split_cross_column_merges(band_only, w)
        band_only = stitch_subtitle_fragments(band_only)
        preview = " || ".join(str(p.get("text", ""))[:40] for p in band_only[:6])
        tdetail(f"band-raw={len(band_only)} {preview}")
        # Инвентарь/статы попали в нижнюю полосу — это eng-ui, НЕ субтитры
        ui_hits, short_n, spoken_n = game_ui_score(band_only)
        cjk_n = script_cjk_score(band_only)
        spoken_here = sum(1 for p in band_only if looks_like_spoken_line(str(p.get("text", ""))))
        # EN Name: реплика важнее залипшего jpn-game hint
        allow_en_fast = (hint != "jpn-game" or spoken_here >= 1) and cjk_n < 8
        if allow_en_fast and spoken_here == 0 and not looks_like_game_ui(band_only):
            direct_vn = extract_vn_dialogue_from_band(band_only, w, h)
            if direct_vn:
                log_eng_ui_lines("ocr-ui", direct_vn)
                tlog(
                    f"score en={english_ocr_quality(direct_vn):.1f}({len(direct_vn)}) "
                    f"jp=skip ui=1 vn-band-direct cjk={cjk_n} "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}"
                )
                return finish_mixed_frame(cap_ui_lines(direct_vn), raw_all, region_img, "eng-ui", UI_LINES_MAX)
        if allow_en_fast and looks_like_game_ui(band_only) and spoken_here == 0:
            ui = filter_game_ui_lines(band_only)
            # меню/кнопки уже читаются с нижней полосы — full+panels только если мало строк
            if len(ui) >= 3 and english_ocr_quality(ui) >= 18:
                # добирать full кадр только если полоса дала мало строк
                if len(ui) < 10:
                    full = rapid_ocr_lines(region_img, max_side=1280)
                    raw_all.extend(full)
                    full = filter_plausible_lines(full, "eng-ui")
                    full = [
                        p
                        for p in full
                        if not is_desktop_chrome(str(p.get("text", "")))
                        and not is_hud_spam(str(p.get("text", "")))
                    ]
                    if script_cjk_score(full) < 8:
                        extra = filter_game_ui_lines(full)
                        if extra:
                            have = {
                                ui_text_fingerprint(str(p.get("text", "")))
                                for p in ui
                            }
                            for p in extra:
                                fp = ui_text_fingerprint(str(p.get("text", "")))
                                if fp and fp not in have:
                                    ui.append(p)
                                    have.add(fp)
                            tlog(f"ui-merge-full {len(band_only)}->{len(ui)}")
                ui = ocr_center_modal_panel(region_img, ui)
                ui = [p for p in ui if not is_overlay_echo_ocr(str(p.get("text", "")))]
                ui = dedupe_near_ui_lines(ui)
                log_eng_ui_lines("ocr-ui", ui)
                tlog(f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
                    f"ui=1 band-fast hits={ui_hits} short={short_n} spoken={spoken_n} cjk={cjk_n} "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}")
                return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)
            full = rapid_ocr_lines(region_img)
            raw_all.extend(full)
            full = filter_plausible_lines(full, "eng-ui")
            full = [p for p in full if not is_desktop_chrome(str(p.get("text", ""))) and not is_hud_spam(str(p.get("text", "")))]
            if script_cjk_score(full) >= 8:
                tlog(f"ui-abort cjk={script_cjk_score(full)} -> meiki "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}")
            else:
                ui = filter_game_ui_lines(full) or ui
                ui = _enrich_if_sparse(region_img, ui)
                ui = [p for p in ui if not is_overlay_echo_ocr(str(p.get("text", "")))]
                ui = dedupe_near_ui_lines(ui)
                log_eng_ui_lines("ocr-ui", ui)
                tlog(f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
                    f"ui=1 hits={ui_hits} short={short_n} spoken={spoken_n} cjk={cjk_n} "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}")
                return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)

        # VN/диалог снизу без Name: — длинная строка с полосы, не гоняем full OCR
        if allow_en_fast and spoken_here == 0 and band_only and not looks_like_game_ui(band_only):
            # если на полосе уже куча отдельных лейблов — НЕ vn-merge (ломает меню+попап)
            prompt_n = sum(
                1 for p in band_only if looks_like_ui_prompt(str(p.get("text", "")))
            )
            if prompt_n >= 4 or len(band_only) >= 8:
                ui = filter_game_ui_lines(band_only)
                ui = split_cross_column_merges(ui, w)
                # широкие 2–3 колонки: не ранний выход без добора покрытия
                if wide_enough_for_columns(w, h):
                    ui = ocr_eng_ui_boost(region_img, ui)
                    # если колонок мало строк — полный col-ocr
                    if ocr_coverage_stats(ui, w, h)["sparse"]:
                        col_lines = ocr_region_by_columns(region_img, ui)
                        if col_lines:
                            ui = filter_game_ui_lines(col_lines) or col_lines
                ui = dedupe_near_ui_lines(ui)
                if len(ui) >= 3:
                    log_eng_ui_lines("ocr-ui", ui)
                    tlog(
                        f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
                        f"ui=1 menu-band prompts={prompt_n} cjk={cjk_n} "
                        f"ocr_ms={int((time.monotonic()-t0)*1000)}"
                    )
                    return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)
            long_lines = [
                p for p in band_only
                if len(str(p.get("text", "")).strip()) >= 12
                and not is_garbage_ocr(str(p.get("text", "")))
                and not re.fullmatch(r"REMASTERED|SOFTMONEY", str(p.get("text", "")).strip(), re.I)
                and not looks_like_ui_prompt(str(p.get("text", "")))
            ]
            if long_lines and english_ocr_quality(long_lines) >= 14:
                long_lines = sorted(long_lines, key=lambda p: (p["box"][1], p["box"][0]))
                merged_ui: list[dict] = []
                cluster: list[dict] = [long_lines[0]]
                for p in long_lines[1:]:
                    prev = cluster[-1]
                    if vn_lines_should_merge(prev, p):
                        cluster.append(p)
                    else:
                        merged_ui.append(_merge_vn_band_cluster(cluster))
                        cluster = [p]
                merged_ui.append(_merge_vn_band_cluster(cluster))
                ui = finish_incomplete_vn_lines(region_img, merged_ui)
                ui = filter_game_ui_lines(ui) or ui
                # filter_game_ui_lines сбрасывает kind — вернём dialogue для cover/размера
                for p in ui:
                    if len(str(p.get("text", ""))) >= 24 or "\n" in str(p.get("text", "")):
                        p["kind"] = "dialogue"
                        p["wrap"] = True
                ui = dedupe_near_ui_lines(ui)
                # заголовки/кнопки рядом с абзацем (LearnEnglish / Get started)
                have = {ui_text_fingerprint(str(p.get("text", ""))) for p in ui}
                for p in band_only:
                    t = str(p.get("text", "")).strip()
                    if not t or len(t) > 48:
                        continue
                    if is_garbage_ocr(t) or is_desktop_chrome(t) or is_hud_spam(t):
                        continue
                    fp = ui_text_fingerprint(t)
                    if not fp or fp in have:
                        continue
                    # короткие лейблы / кнопки / title-case шапки
                    words = t.split()
                    if len(words) <= 6 and (
                        looks_like_ui_prompt(t)
                        or looks_like_section_header(t)
                        or (t[:1].isupper() and len(t) >= 4)
                        or re.fullmatch(r"[A-Za-z][A-Za-z0-9]+(?:[A-Z][a-z0-9]+)+", t)  # LearnEnglish
                    ):
                        copy = dict(p)
                        copy["kind"] = "ui"
                        ui.append(copy)
                        have.add(fp)
                ui = dedupe_near_ui_lines(ui)
                ui.sort(key=lambda p: (p["box"][1], p["box"][0]))
                # выделенная колонка/абзац: RapidOCR на 2× band всё равно дырявит — добор
                if w < 720 or ocr_coverage_stats(ui, w, h)["sparse"]:
                    ui = ocr_eng_ui_boost(region_img, ui)
                    ui = dedupe_near_ui_lines(ui)
                log_eng_ui_lines("ocr-ui", ui)
                tlog(f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
                    f"ui=1 vn-band-fast cjk={cjk_n} "
                    f"ocr_ms={int((time.monotonic()-t0)*1000)}")
                return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)

        blocks = collect_subtitle_blocks(band_only) if allow_en_fast else []
        if not blocks and allow_en_fast and spoken_here >= 1:
            # OCR вернул Name: но collect не собрал — берём как есть
            spoken_lines = [p for p in band_only if looks_like_spoken_line(str(p.get("text", "")))]
            blocks = [merge_subtitle_cluster([p]) for p in spoken_lines[:2]]
            tlog(f"sub-direct n={len(blocks)}")
        # НЕ склеиваем fallback'ом весь банд — это превращает Pouch/статы в кашу
        if blocks:
            blocks = extend_dialogue_with_bottom_pass(region_img, blocks)
            for b in blocks:
                lbs = list(b.get("line_boxes") or [])
                if len(lbs) >= 2:
                    for lb in lbs:
                        lb["text"] = normalize_subtitle_ocr(str(lb.get("text", "")))
                    b["line_boxes"] = lbs
                    b["text"] = "\n".join(str(lb.get("text", "")) for lb in lbs)
                else:
                    b["text"] = normalize_subtitle_ocr(b.get("text", ""))
                kept_text = str(b.get("text", ""))
                tlog(
                    f"sub-keep-nl={kept_text.count(chr(10))} "
                    f"line_boxes={len(lbs)} chars={len(kept_text)}"
                )
                tdetail(f"sub-keep preview={kept_text[:70]!r}")
            # Name: внизу + пункты выбора слева / Confirm — OCR уже в band, не выкидывать
            choices = extract_dialogue_choice_lines(band_only, blocks, w, h)
            # если choice выше 45% кадра — добрать средней полосой
            if not choices and h >= 500:
                y_mid0 = int(h * 0.22)
                y_mid1 = int(h * 0.62)
                if y_mid1 > y_mid0 + 40:
                    mid = region_img.crop((0, y_mid0, w, y_mid1))
                    mid2 = mid.resize((mid.width * 2, mid.height * 2), Image.Resampling.LANCZOS)
                    mid_raw = rapid_ocr_lines(mid2)
                    mid_lines = []
                    for p in mid_raw:
                        x1, y1, x2, y2 = p["box"]
                        copy = dict(p)
                        copy["box"] = (
                            int(x1 / 2),
                            int(y_mid0 + y1 / 2),
                            int(x2 / 2),
                            int(y_mid0 + y2 / 2),
                        )
                        copy["line_height"] = max(8, int(p.get("line_height", 14) / 2))
                        copy["text"] = unglue_english(
                            normalize_subtitle_ocr(str(p.get("text", "")))
                        )
                        mid_lines.append(copy)
                    mid_lines = [
                        p
                        for p in mid_lines
                        if not is_hud_spam(str(p.get("text", "")))
                        and not is_desktop_chrome(str(p.get("text", "")))
                    ]
                    choices = extract_dialogue_choice_lines(mid_lines, blocks, w, h)
            bx = blocks[0]["box"]
            tlog(
                f"score en={english_ocr_quality(blocks + choices):.1f}"
                f"({len(blocks)}+{len(choices)}) jp=skip "
                f"spoken_blocks={len(blocks)} box={bx[2]-bx[0]}x{bx[3]-bx[1]} "
                f"spoken_band={spoken_here} choices={len(choices)} "
                f"ocr_ms={int((time.monotonic()-t0)*1000)} band=1"
            )
            if choices:
                for b in blocks:
                    b.setdefault("kind", "dialogue")
                # реплика + choice + HUD (квест/Guard/Map) — не выкидывать UI
                return merge_spoken_with_hud(region_img, blocks[:2], band_only, choices)
            return merge_spoken_with_hud(region_img, blocks[:3], band_only, [])
        if band_only:
            tlog(f"band-no-spoken n={len(band_only)} "
                f"hits={ui_hits} short={short_n} cjk={cjk_n} spoken={spoken_here} "
                f"{'fallthrough' if allow_en_fast else 'jp-prefer'} ocr_ms={int((time.monotonic()-t0)*1000)}")
        # letterbox-only: BREAKING NEWS / HUD перебили реплику в широкой полосе
        if allow_en_fast and not blocks and (hint in ("eng-subtitle", None) or spoken_here == 0):
            y_deep = int(h * 0.72)
            if y_deep > y0 + 20:
                deep = region_img.crop((0, y_deep, w, h))
                deep2 = deep.resize((deep.width * 2, deep.height * 2), Image.Resampling.LANCZOS)
                deep_raw = rapid_ocr_lines(deep2)
                deep_lines = []
                for p in deep_raw:
                    x1, y1, x2, y2 = p["box"]
                    copy = dict(p)
                    copy["box"] = (
                        int(x1 / 2),
                        int(y_deep + y1 / 2),
                        int(x2 / 2),
                        int(y_deep + y2 / 2),
                    )
                    copy["line_height"] = max(8, int(p["line_height"] / 2))
                    copy["text"] = normalize_subtitle_ocr(str(p.get("text", "")))
                    deep_lines.append(copy)
                deep_lines = [
                    p
                    for p in deep_lines
                    if not is_hud_spam(str(p.get("text", "")))
                    and not is_desktop_chrome(str(p.get("text", "")))
                    and (looks_like_spoken_line(str(p.get("text", ""))) or looks_like_subtitle_continue(str(p.get("text", ""))))
                ]
                deep_lines = stitch_subtitle_fragments(deep_lines)
                deep_blocks = collect_subtitle_blocks(deep_lines)
                if not deep_blocks and deep_lines:
                    deep_blocks = [
                        merge_subtitle_cluster([p])
                        for p in deep_lines
                        if looks_like_spoken_line(str(p.get("text", "")))
                    ][:2]
                if deep_blocks:
                    tlog(f"letterbox-deep n={len(deep_blocks)} "
                        f"chars={len(deep_blocks[0]['text'])} ocr_ms={int((time.monotonic()-t0)*1000)}")
                    tdetail(f"letterbox-deep preview={deep_blocks[0]['text'][:70]!r}")
                    return merge_spoken_with_hud(region_img, deep_blocks[:3], band_only, [])
        # видео-субтитры: пустая полоса / fade — НЕ гоняем Meiki на полный кадр
        if hint == "eng-subtitle" and not band_only and cjk_n < 2:
            tlog(f"band-empty hold hint={hint} ocr_ms={int((time.monotonic()-t0)*1000)}")
            return [], hint

    # полный кадр — для HUD/UI или если снизу тишина
    # узкий/невысокий кроп: 2× перед RapidOCR (иначе абзац сайта → дырявые 3–4 строки)
    ocr_src = region_img
    ocr_scale = 1
    if prefer_en and (w < 720 or h < 520):
        ocr_scale = 2
        ocr_src = region_img.resize(
            (max(1, w * ocr_scale), max(1, h * ocr_scale)),
            Image.Resampling.LANCZOS,
        )
        tlog(f"ocr-upscale {w}x{h} -> {ocr_src.width}x{ocr_src.height}")
    rapid = rapid_ocr_lines(ocr_src, max_side=0 if ocr_scale > 1 else 1280)
    if ocr_scale > 1:
        scaled = []
        for p in rapid:
            x1, y1, x2, y2 = p["box"]
            copy = dict(p)
            copy["box"] = (
                int(x1 / ocr_scale),
                int(y1 / ocr_scale),
                int(x2 / ocr_scale),
                int(y2 / ocr_scale),
            )
            copy["line_height"] = max(8, int(p.get("line_height", 14) / ocr_scale))
            scaled.append(copy)
        rapid = scaled
    raw_all.extend(rapid)
    rapid = merge_rapid_prefer_dialogue(rapid, band_lines)
    rapid = filter_plausible_lines(rapid, "eng-ui")
    rapid = [p for p in rapid if not is_hud_spam(str(p.get("text", "")))]
    en_q = english_ocr_quality(rapid)
    spoken = [p for p in rapid if looks_like_spoken_line(str(p.get("text", "")))]
    rapid_game = [
        p for p in rapid
        if not is_desktop_chrome(str(p.get("text", ""))) and not is_garbage_ocr(str(p.get("text", "")))
    ]
    en_q_game = english_ocr_quality(rapid_game)

    # Быстрый путь: катсцена/YouTube-диалоги — без Meiki
    if looks_like_game_ui(rapid_game) and script_cjk_score(rapid_game) < 8:
        ui = filter_game_ui_lines(rapid_game)
        ui = _enrich_if_sparse(region_img, ui)
        ui = [p for p in ui if not is_overlay_echo_ocr(str(p.get("text", "")))]
        ui = dedupe_near_ui_lines(ui)
        log_eng_ui_lines("ocr-ui", ui)
        uh, sh, sp = game_ui_score(rapid_game)
        tlog(f"score en={english_ocr_quality(ui):.1f}({len(ui)}) jp=skip "
            f"ui=1 hits={uh} short={sh} spoken={sp} cjk={script_cjk_score(rapid_game)} "
            f"ocr_ms={int((time.monotonic()-t0)*1000)}")
        return finish_mixed_frame(cap_ui_lines(ui), raw_all, region_img, "eng-ui", UI_LINES_MAX)

    if spoken and en_q_game >= 10 and script_cjk_score(rapid_game) < 6:
        blocks = collect_subtitle_blocks(rapid_game)
        if not blocks:
            keep = []
            for p in rapid_game:
                t = str(p.get("text", ""))
                if looks_like_spoken_line(t) or (
                    spoken
                    and p["box"][1] >= spoken[0]["box"][1] - 8
                    and p["box"][1] <= spoken[0]["box"][3] + max(36, spoken[0]["line_height"] * 3.2)
                ):
                    if looks_like_spoken_line(t) or looks_like_subtitle_continue(t):
                        keep.append(p)
            blocks = collect_subtitle_blocks(keep) if keep else []
            if not blocks and keep:
                blocks = [merge_subtitle_cluster(keep)]
        if blocks:
            blocks = extend_dialogue_with_bottom_pass(region_img, blocks)
        tlog(f"score en={en_q_game:.1f}({len(blocks)}) jp=skip spoken={len(spoken)} "
            f"ocr_ms={int((time.monotonic()-t0)*1000)}")
        sp = (blocks or spoken)[:3]
        return merge_spoken_with_hud(region_img, sp, rapid_game, [])

    if prefer_en and en_q_game >= 18 and not (hint == "jpn-game") and script_cjk_score(rapid_game) < 8:
        rapid_game = _enrich_if_sparse(region_img, rapid_game)
        rapid_game = filter_game_ui_lines(rapid_game) or rapid_game
        rapid_game = dedupe_near_ui_lines(rapid_game)
        log_eng_ui_lines("ocr-ui", rapid_game)
        tlog(f"score en={english_ocr_quality(rapid_game):.1f}({len(rapid_game)}) jp=skip "
            f"cjk={script_cjk_score(rapid_game)} ocr_ms={int((time.monotonic()-t0)*1000)}")
        return finish_mixed_frame(cap_ui_lines(rapid_game), raw_all, region_img, "eng-ui", UI_LINES_MAX)

    # Japanese first, from the same multilingual pass as the Latin text; Meiki
    # only refines the doubtful elements.
    japanese = [
        ln
        for ln in (rapid or [])
        if block_script(str(ln.get("text", ""))) in (SCRIPT_JA, SCRIPT_MIXED)
    ]
    jp_engine = "rapid"
    if japanese:
        japanese, _refined = refine_japanese_blocks(region_img, japanese, time.monotonic())
        jp_engine = str(japanese[0].get("engine", "rapid")) if japanese else "rapid"
    if not japanese:
        tlog(f"jp-fallback=skip (no-jp-blocks en_q={en_q:.1f})")
    japanese = filter_plausible_lines(japanese, "jpn-game")
    jp_q = japanese_ocr_quality(japanese)
    tlog(f"score en={en_q:.1f}({len(rapid)}) jp={jp_q:.1f}({len(japanese)}) "
        f"engine_jp={jp_engine if japanese else 'none'} ocr_ms={int((time.monotonic()-t0)*1000)}")

    # Чистый игровой JP (имя+реплика / tips body) важнее нотификаций Hyprland в RapidOCR
    jp_has_dialogue = any(
        is_speaker_name(str(p.get("text", ""))) or len(RE_JPN.findall(str(p.get("text", "")))) >= 8
        for p in japanese
    )

    # Английский UI/HUD выигрывает только если это реальный игровой EN, не chrome
    if en_q_game >= 18 and en_q_game >= jp_q * 0.85 and not (jp_has_dialogue and jp_q >= 20):
        rapid_game = _enrich_if_sparse(region_img, rapid_game)
        rapid_game = filter_game_ui_lines(rapid_game) or rapid_game
        rapid_game = dedupe_near_ui_lines(rapid_game)
        if japanese:
            # English scored higher, but a frame can hold both. Returning the
            # English half alone silently dropped the Japanese half, which is
            # exactly the mixed screen this has to handle.
            merged = _merge_language_halves(rapid_game, japanese)
            log_eng_ui_lines("ocr-ui", merged)
            tlog(f"mixed-frame en={len(rapid_game)} jp={len(japanese)} out={len(merged)}")
            return merged[:60], "jpn-game"
        log_eng_ui_lines("ocr-ui", rapid_game)
        return finish_mixed_frame(cap_ui_lines(rapid_game), raw_all, region_img, "eng-ui", UI_LINES_MAX)

    if japanese and jp_q >= 12:
        eng_extra = rapid or subtitle_ocr_lines(region_img)
        for item in eng_extra:
            text = normalize_japanese_text(str(item.get("text", "")))
            if re.fullmatch(r"[QO0]?TIPS", text, flags=re.I):
                text = "TIPS"
            if len(RE_LAT.findall(text)) < 2:
                continue
            if is_garbage_ocr(text) and text.upper() not in {"TAP!", "NEW!", "TIPS"}:
                continue
            if any(box_overlap_ratio(item["box"], jp["box"]) > 0.25 for jp in japanese):
                continue
            copy = dict(item)
            copy["text"] = text
            copy["engine"] = "rapidocr-eng"
            japanese.append(copy)
        for item in japanese:
            item.setdefault("engine", jp_engine)
        japanese.sort(key=lambda p: (p["box"][1], p["box"][0]))
        return japanese, "jpn-game"

    # hint=jpn-game + пустой Meiki: не скатываемся в Rapid-кашу (оверлей/RU на кадре)
    if hint == "jpn-game":
        tlog(f"jpn-hold meiki-empty en={en_q:.1f} ocr_ms={int((time.monotonic()-t0)*1000)}")
        return [], "jpn-game"

    # Узкая полоса субтитров / letterbox (пороги от ширины, не «только ≤720»).
    aspect = region_img.width / max(1, region_img.height)
    short_h = max(200, int(region_img.width * 0.18))
    medium_h = max(720, int(region_img.width * 0.45))
    if (
        (region_img.height <= short_h and aspect >= 2.2)
        or (region_img.width >= 500 and region_img.height <= medium_h and aspect >= 1.25)
    ):
        sub = subtitle_ocr_lines(region_img)
        if sub:
            return sub, "eng-subtitle"

    if rapid_game and en_q_game >= 8:
        return finish_mixed_frame(cap_ui_lines(rapid_game), raw_all, region_img, "eng-ui", UI_LINES_MAX)
    if rapid and en_q >= 8:
        rapid = [p for p in rapid if not is_desktop_chrome(str(p.get("text", "")))]
        if rapid:
            return finish_mixed_frame(cap_ui_lines(rapid), raw_all, region_img, "eng-ui", UI_LINES_MAX)

    # Hysteresis: только что был eng-ui / субтитры — не прыгаем в Tesseract-кашу
    # на одном слабом кадре (видео, motion blur).
    if hint in ("eng-ui", "eng-subtitle", "eng+rus") and en_q_game < 8:
        tlog(f"empty-hold hint={hint} en_q={en_q_game:.1f}")
        return [], hint

    # Полноэкранный Tesseract jpn+eng даёт 1-буквенные «плашки на полэкрана» —
    # для игр/видео больше не используем. Пустой результат → clear overlay.
    # Кириллица сюда не встроена намеренно: кадр может уйти и по раннему
    # выходу, а этот код — только последний из десятка. Зонд стоит в
    # `read_frame`, где видно любой пустой исход.
    tlog(f"ocr-empty (no tess kitchen-sink) en={en_q:.1f} jp={jp_q:.1f}")
    return [], hint or "eng-ui"

