"""Шаги кадра, которые решают «этот кадр закрыт» до перевода.

Тела перенесены из `worker` дословно. `continue` цикла стал возвратом:
вызывающий делает один шаг назад к следующему кадру, а не держит ветки внутри.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from PIL import Image

from ..core.text import (
    SCRIPT_JA,
    SCRIPT_MIXED,
    block_script,
    clamp,
    clean_ocr_text,
    is_hud_spam,
    log_eng_ui_lines,
    looks_like_spoken_line,
    tdetail,
    tlog,
)
from ..layout.dialogue import (
    drop_target_columns,
    group_japanese_blocks,
    pair_bilingual_and_dedupe,
)
from ..layout.grouping import (
    is_desktop_chrome,
    looks_like_panel_body_line,
    merge_paragraphs,
    split_button_rows,
    split_cross_column_merges,
    split_false_nav_merges,
    split_title_banner_merges,
    split_vn_speaker_line,
    stitch_same_baseline,
    stitch_ui_body_paragraphs,
    stitch_word_gaps,
)
from ..ocr.engine import (
    collect_subtitle_blocks,
    frame_already_in_target,
    is_garbage_ocr,
    is_overlay_echo_ocr,
    looks_like_dialogue_choice,
    looks_like_ocr_mojibake_of_russian,
    skip_source,
)
from ..ocr.passes import finish_incomplete_vn_lines, offset_boxes
from ..translation.service import (
    drop_bad_ocr_boxes,
    filter_plausible_lines,
    is_garbage_japanese,
    is_speaker_name,
)
from ..typography.metrics import (
    dedupe_near_ui_lines,
    drop_false_spaces,
    is_ink_band_garbage,
    looks_like_symbol_field,
    looks_like_ui_prompt,
)
from .change import frame_changed, probe
from .echo import overlay_texts, same_text
from .reconcile import filter_echo_of_overlay_cards, rapid_ocr_lines
from .runtime import SETTINGS
from .snap import grim_region, soft_probe_is_real_dialogue
from .tracking import any_block_text_changed, dialogue_keys_similar, scene_changed


@dataclass
class _Soft:
    skip: bool
    last_clean_check: float
    last_dirty: object
    last_key: str
    pending_key: str
    pending_hits: int
    hold_hidden: bool


@dataclass
class _Echo:
    skip: bool
    lines: list
    last_key: str
    last_clean: Image.Image | None


def soft_recheck(
    periodic_recheck, videoish, last_key, need_clean, geom, last_dirty, now,
    cyc, loop_sleep, tracked, rw, rh, last_ocr_lang, state, region,
    pending_key, pending_hits, hold_hidden, last_clean_check,
) -> _Soft:
    """Грязная полоса субтитров до того, как карточки спрячутся ради снимка."""
    if periodic_recheck and videoish and last_key and need_clean:
        dirty = grim_region(geom)
        min_band_h = max(120, int(rh * 0.35)) if rh else 400
        if dirty is not None and dirty.height >= min_band_h:
            y0 = int(dirty.height * 0.42)
            band = dirty.crop((0, y0, dirty.width, dirty.height))
            try:
                quick = offset_boxes(rapid_ocr_lines(band), dy=y0)
            except Exception:
                quick = []
            blocks = collect_subtitle_blocks(quick, dirty.width, dirty.height)
            qkey = "|".join(b["text"] for b in blocks) if blocks else ""
            if qkey and is_overlay_echo_ocr(qkey):
                # Эхо RU на стабильном кадре ≠ смена реплики. hide→show = мерцание.
                pix_move = (
                    last_dirty is not None
                    and frame_changed(last_dirty, probe(dirty), SETTINGS.change_mean_videoish * 0.85)
                )
                if not pix_move:
                    tlog("soft-recheck echo stable")
                    last_clean_check = now
                    last_dirty = probe(dirty)
                    time.sleep(loop_sleep)
                    return _Soft(True, last_clean_check, last_dirty, last_key, pending_key, pending_hits, hold_hidden)
                since_echo = now - float(cyc.last_echo_clean)
                if since_echo < 3.8:
                    tlog("soft-recheck echo motion-throttle")
                    last_clean_check = now
                    last_dirty = probe(dirty)
                    time.sleep(loop_sleep)
                    return _Soft(True, last_clean_check, last_dirty, last_key, pending_key, pending_hits, hold_hidden)
                tlog("soft-recheck echo → hide-clean (motion)")
                cyc.last_echo_clean = now
                last_dirty = probe(dirty)
                # fall through — key_same оставит карточки, смена обновит
            elif qkey and scene_changed(qkey, last_key):
                tlog("soft-recheck scene differs → full")
                # fall through to the capture and the per-block decision
            elif qkey and dialogue_keys_similar(qkey, last_key):
                # The scene reads the same, so a full capture is not obviously
                # needed. But two elements can swap their text and leave the
                # scene key identical, and asking here is cheap: the per-block
                # tracker is the authority on whether a given element changed,
                # and a scene-level "looks the same" must not overrule it.
                if SETTINGS.incremental and tracked and not any_block_text_changed(
                    dirty, last_dirty, tracked, rw, rh
                ):
                    tlog("soft-recheck same skip-hide")
                    last_clean_check = now
                    last_dirty = probe(dirty)
                    time.sleep(loop_sleep)
                    return _Soft(True, last_clean_check, last_dirty, last_key, pending_key, pending_hits, hold_hidden)
                tlog("soft-recheck scene same but a block changed")
                last_clean_check = now
                last_dirty = probe(dirty)
                # fall through: the per-block path decides what to re-read
            elif (
                qkey
                and soft_probe_is_real_dialogue(qkey, last_ocr_lang)
                and not dialogue_keys_similar(qkey, last_key)
            ):
                # НЕ full clear — только реплики; HUD (Map/Guard/атаки) остаётся
                tlog(f"soft-recheck change hud-keep '{qkey[:48]}'")
                state.replace_keeping_hud(region)
                last_key = ""
                pending_key = ""
                pending_hits = 0
                last_dirty = probe(dirty)
                # HUD остаётся на экране; hide только если HUD пуст
                hold_hidden = not bool(state.peek_blocks())
                # fall through → hide + clean OCR
            elif qkey and not soft_probe_is_real_dialogue(qkey, last_ocr_lang):
                # мусор/каша — peek под оверлеем БЕЗ clear (не мигать HUD)
                tlog(f"soft-recheck junk → hide-clean '{qkey[:40]}'")
                last_dirty = probe(dirty)
                hold_hidden = False
                # fall through
            elif not qkey:
                # пустая полоса под плашкой часто = плашка закрыла EN; peek, не clear сразу
                empty_n = int(cyc.soft_empty) + 1
                cyc.soft_empty = empty_n
                if empty_n >= 3:
                    tlog("soft-recheck empty → drop-dialogue")
                    state.replace_keeping_hud(region)
                    last_key = ""
                    pending_key = ""
                    pending_hits = 0
                    cyc.soft_empty = 0
                    hold_hidden = not bool(state.peek_blocks())
                else:
                    tlog(f"soft-recheck empty → hide-clean n={empty_n}")
                    hold_hidden = False
                last_dirty = probe(dirty)
                # fall through
            if qkey:
                cyc.soft_empty = 0
    return _Soft(False, last_clean_check, last_dirty, last_key, pending_key, pending_hits, hold_hidden)


def drop_self_echo(
    lines, stable, last_ocr_lang, hold_hidden, state, region, last_key,
    region_img, loop_sleep, last_clean,
) -> _Echo:
    """Кадр, который прочитал собственные карточки, не становится новым текстом."""
    # читаем свои же RU-карточки (hide не успел) — не обновлять, иначе места пляшут
    if lines and (stable and last_ocr_lang == "eng-ui" or last_ocr_lang in ("eng-subtitle", "jpn-game")):
        joined_echo = "|".join(str(p.get("text", "")) for p in lines)
        has_real_ui = any(
            re.search(
                r"\b(Obtained|Exploring|Head to|Go to|In Combat|Waiting|Confirm|Map|"
                r"Zoom|Location|Quest|Waypoint|Blinded)\b",
                str(p.get("text", "")),
                re.I,
            )
            or looks_like_spoken_line(str(p.get("text", "")))
            or looks_like_dialogue_choice(str(p.get("text", "")))
            for p in lines
        )
        if is_overlay_echo_ocr(joined_echo) and not has_real_ui:
            tlog(f"skip-self-echo dialogue '{joined_echo[:40]}'")
            # peek под плашкой провалился — не держим hold_hidden вечно
            if hold_hidden:
                state.clear(region, reason="empty_scene")
                last_key = ""
            else:
                state.show()
            last_clean = region_img
            time.sleep(loop_sleep)
            return _Echo(True, lines, last_key, last_clean)
        if is_overlay_echo_ocr(joined_echo) and has_real_ui:
            before = len(lines)
            lines = [p for p in lines if not is_overlay_echo_ocr(str(p.get("text", "")))]
            tdetail(f"echo-filter {before}->{len(lines)}")
            if not lines:
                if hold_hidden:
                    state.clear(region, reason="empty_scene")
                    last_key = ""
                last_clean = region_img
                time.sleep(loop_sleep)
                return _Echo(True, lines, last_key, last_clean)
        if last_ocr_lang == "eng-ui":
            # Кадр отбрасывается, если это наш собственный оверлей: движок
            # сфотографировал собственные карточки и принял их за текст
            # игры. Раньше здесь считалась доля кириллицы, и это было
            # верно только для одного случая - оверлея, который рисует
            # по-русски. При любом другом языке назначения русский текст
            # на экране выглядел для движка как его собственное эхо, и
            # весь кадр уходил в никуда: ноль карточек при полностью
            # рабочем переводе, проверено на двуязычной странице.
            #
            # Теперь сравниваем с тем, что оверлей уже нарисовал. Это
            # верно при любом языке и не требует угадывать алфавит.
            ours = overlay_texts(state)
            if ours:
                same = sum(
                    1
                    for p in lines
                    if same_text(str(p.get("text") or ""), ours)
                )
                if same >= max(2, int(len(lines) * 0.45)) and not has_real_ui:
                    tlog(f"skip-self-echo own={same}/{len(lines)}")
                    if hold_hidden:
                        state.show()
                    last_clean = region_img
                    time.sleep(loop_sleep)
                    return _Echo(True, lines, last_key, last_clean)
    return _Echo(False, lines, last_key, last_clean)


def arrange_lines(
    lines, region_img, rw, rh, blocks_now, japanese_mode, subtitle_mode,
    eng_ui_mode, last_ocr_lang,
) -> list:
    """Склейки, разрезы и роли строк одного кадра."""
    for par in lines:
        par["text"] = clean_ocr_text(par["text"])
    lines = [p for p in lines if not is_desktop_chrome(p["text"]) and not is_hud_spam(p["text"])]
    # A Russian (or other target) page comes back as Latin soup from RapidOCR.
    # Overlaying a translation of that soup - or of the few real English chips
    # still on the page - paints over UI that is already in the player's
    # language. Leave the frame alone.
    if eng_ui_mode and frame_already_in_target(lines):
        tlog(f"eng-ui skip: already-target n={len(lines)}")
        return []
    # A row of buttons is wide for its own reasons, and the box-geometry pass
    # reads that width as sparse text and squeezes it back to the size of the
    # words. The gaps are the evidence that the line is several controls, so
    # it has to be asked before the geometry is trimmed - otherwise the gaps
    # are gone by the time anyone looks for them.
    lines = split_button_rows(lines, region_img)
    lines = drop_bad_ocr_boxes(lines, rw, rh)
    lines = drop_target_columns(lines)
    lines = filter_plausible_lines(lines, last_ocr_lang or "")
    lines = [
        p
        for p in lines
        if (japanese_mode or not skip_source(p["text"]))
        and not is_garbage_ocr(p["text"])
        and not looks_like_ocr_mojibake_of_russian(p["text"])
        and not looks_like_symbol_field(region_img, p["box"], p["text"])
        and (not japanese_mode or not is_garbage_japanese(p["text"]) or is_speaker_name(p["text"]) or p.get("kind") in ("chip", "latin", "menu", "name"))
    ]
    n_before_split = len(lines)
    lines = split_false_nav_merges(lines)
    lines = split_title_banner_merges(lines)
    # веб 2–3 колонки: не держать уже слитые через gutter строки
    tlog(f"before-split-cross n={len(lines)}")
    lines = split_cross_column_merges(lines, rw)
    tlog(f"after-split-cross n={len(lines)}")
    # eng-ui / субтитры: НЕ склеивать обратно (у субтитров line_boxes по строкам)
    if not eng_ui_mode and not subtitle_mode:
        lines = stitch_same_baseline(lines)
    # UI/субтитры уже размечены — merge_paragraphs склеивает Pouch+статы в кашу
    if not eng_ui_mode and not subtitle_mode:
        lines = merge_paragraphs(lines)
        lines = stitch_same_baseline(lines)
        lines = split_cross_column_merges(lines, rw)
    if eng_ui_mode:
        lines = stitch_word_gaps(lines)
        for par in lines:
            par["text"] = drop_false_spaces(region_img, par["box"], str(par.get("text", "")))
        lines = filter_echo_of_overlay_cards(lines, blocks_now)
        # Имя говорящего и реплика приходят одним боксом - режем по
        # промежутку, иначе имя уезжает в перевод вместе с репликой.
        lines = [
            piece
            for par in lines
            for piece in split_vn_speaker_line(par, region_img)
        ]
        # separate OCR boxes already left|gap|right — mark, don't merge.
        from ..layout.name_gap import (
            annotate_side_by_side_name_body,
            annotate_stacked_name_body,
            is_single_line_block,
        )
        from ..ocr.roles import annotate_roles
        from ..ocr.speakers import detect_speakers

        lines = annotate_side_by_side_name_body(lines)
        lines = annotate_stacked_name_body(lines)
        lines = annotate_roles(lines, frame_size=(rw, rh))
        for hit in detect_speakers(lines):
            target = lines[hit.line_index]
            target["kind"] = "name"
            target["semantic_role"] = "SPEAKER"
            target["oneline"] = True
            target["wrap"] = False
        lines = dedupe_near_ui_lines(lines)
        lines = [
            p
            for p in lines
            if not is_ink_band_garbage(str(p.get("text", "")))
        ]
        # Профиль сказал, что здесь не текст: полоса глифов читается как
        # латиница, а переводчик транслитерирует её в карточки поверх самих
        # глифов. Универсальный путь отличить символы от букв не может — это не
        # его выдумка, а факт из профиля, поэтому и решение здесь, а не в core.
        lines = [p for p in lines if str(p.get("kind") or "") != "decor"]
        lines = stitch_ui_body_paragraphs(lines)
        lines = split_cross_column_merges(lines, rw)
        # ink-band только для настоящего VN-пузыря внизу — НЕ для описаний скиллов
        vn_cands: list[dict] = []
        other: list[dict] = []
        for par in lines:
            t = str(par.get("text", "")).strip()
            y1, y2 = int(par["box"][1]), int(par["box"][3])
            cy = (y1 + y2) * 0.5
            bottom = cy >= rh * 0.70
            spokenish = looks_like_spoken_line(t) or (
                str(par.get("kind", "")) == "dialogue" and ":" in t[:18]
            )
            longish = len(t) >= 28 or "\n" in t or len(par.get("line_boxes") or []) >= 2
            menuish = looks_like_ui_prompt(t) and len(t) <= 24
            panel_body = (
                str(par.get("kind", "")) == "body"
                or looks_like_panel_body_line(t)
                or (par.get("pin_box") and par.get("wrap"))
            )
            if bottom and longish and spokenish and not menuish and not panel_body:
                par = dict(par)
                par["kind"] = "dialogue"
                par["wrap"] = True
                vn_cands.append(par)
            else:
                other.append(par)
        if vn_cands:
            vn_cands = finish_incomplete_vn_lines(region_img, vn_cands)
            lines = other + vn_cands
            lines.sort(key=lambda p: (p["box"][1], p["box"][0]))
        for par in lines:
            t = str(par.get("text", ""))
            # VN-реплика (имя: текст / пузырь) — dialogue
            if str(par.get("semantic_role") or "") == "SPEAKER" or str(
                par.get("kind") or ""
            ) == "name":
                # Имя — это одна строка. Блок с несколькими строками, который
                # кто-то назвал SPEAKER (роль по зоне, соседний блок, эвристика
                # докладчика), абзацем остаётся: описание квеста в
                # 1012x145 с пятью строками уходило в `name`/`oneline=True`, и
                # карточка брала кегль из 145-пиксельного бокса - 134 пикселя
                # на строке меню - и уходила на 300 пикселей влево от текста.
                if not is_single_line_block(par):
                    par.pop("semantic_role", None)
                    par["kind"] = "body"
                    par["wrap"] = True
                    par["pin_box"] = True
                    par["oneline"] = False
                    continue
                par["kind"] = "name"
                par["semantic_role"] = "SPEAKER"
                par["oneline"] = True
                par["wrap"] = False
                continue
            if str(par.get("semantic_role") or "") == "DIALOGUE" and str(
                par.get("kind") or ""
            ) == "dialogue":
                par["wrap"] = True
                par["oneline"] = False
                continue
            if (
                str(par.get("kind", "")) == "dialogue"
                and looks_like_spoken_line(t)
                and not looks_like_panel_body_line(t)
            ):
                par["wrap"] = True
                par["oneline"] = False
                box_h = max(10, int(par["box"][3] - par["box"][1]))
                nln = max(1, t.count("\n") + 1)
                par["line_height"] = clamp(int(box_h / nln), 14, 34)
                continue
            # абзац панели: плотно в OCR-боксе, без раздувания как у субтитров
            if (
                str(par.get("kind", "")) == "body"
                or par.get("pin_box")
                or looks_like_panel_body_line(t)
                or (
                    par.get("wrap")
                    and len(t) >= 28
                    and not looks_like_ui_prompt(t)
                    and not looks_like_dialogue_choice(t)
                )
            ):
                par["kind"] = "body"
                par["wrap"] = True
                par["pin_box"] = True
                par["oneline"] = False
                box_h = max(10, int(par["box"][3] - par["box"][1]))
                nln = max(1, t.count("\n") + 1)
                par["line_height"] = clamp(int(box_h / nln), 11, 28)
                continue
            par["kind"] = "ui"
            par["oneline"] = True
            par["wrap"] = False
            par.pop("pin_box", None)
            box_h = max(10, int(par["box"][3] - par["box"][1]))
            par["line_height"] = clamp(int(box_h * 0.85), 10, 32)
    if japanese_mode:
        lines = pair_bilingual_and_dedupe(lines)
        # Group only the Japanese elements. A frame with English and
        # Japanese side by side must not lose the English half to a
        # section-gap rule that was written for a single-language screen.
        jp_lines = [
            p for p in lines if block_script(str(p.get("text", ""))) in (SCRIPT_JA, SCRIPT_MIXED)
        ]
        other_lines = [p for p in lines if p not in jp_lines]
        if jp_lines:
            grouped = {
                id(g): g for g in group_japanese_blocks(jp_lines, region_img)
            }
            jp_lines = list(grouped.values())
        lines = jp_lines + other_lines
        lines = pair_bilingual_and_dedupe(lines)
    for par in lines:
        par["text"] = clean_ocr_text(par["text"])
    lines = filter_plausible_lines(lines, last_ocr_lang or "")
    lines = [
        p
        for p in lines
        if (japanese_mode or not skip_source(p["text"]))
        and not is_garbage_ocr(p["text"])
        and (
            not japanese_mode
            or not is_garbage_japanese(p["text"])
            or is_speaker_name(p["text"])
            or p.get("kind") in ("chip", "latin", "menu", "name")
        )
    ]
    if eng_ui_mode:
        log_eng_ui_lines("final-ui", lines)
        if len(lines) != n_before_split:
            tlog(f"ui-pipeline {n_before_split}->{len(lines)}")
    return lines
