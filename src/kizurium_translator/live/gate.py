"""Решение о кадре: нужен ли на этом кадре распознаватель.

Шаг «detect change» конвейера. Он решает одну вещь — стоит ли читать
кадр заново, — и решает её по дешёвым признакам: тикнула ли миниатюра,
похожи ли два соседних кадра, есть ли на экране живой диалог.

Почему это отдельный модуль. Внутри тела цикла решение занимало 226 строк
и одиннадцать `continue`: каждый означал «с этим кадром всё, ждём следующий».
Чтобы понять порядок шагов, приходилось читать все одиннадцать выходов и
восстанавливать, какой к какому решению относится. Здесь выход один -
`FrameGate.closed`, и он называет причину.

Состояние, которое блок менял по месту, раньше протекало в тело цикла через
восемь имён. Теперь оно живёт в `FrameGate` и возвращается целиком.

Четыре функции, которыми этот блок пользуется, живут в других слоях и
сюда только переиспользованы, а не перевезены: они нужны и шагу адресного
перечитывания, и основному пути. Импорт отложенный - иначе `session` и
`gate` начали бы ссылаться друг на друга при импорте.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ..core.text import tdetail, tlog
from ..layout.grouping import is_static_ui_overlay
from ..ocr.engine import (
    collect_subtitle_blocks,
    is_overlay_echo_ocr,
    live_dialogue_cards,
    subtitle_ocr_lines,
)
from .change import frame_changed, probe, probe_changed
from .reconcile import rapid_ocr_lines
from .runtime import SETTINGS
from .state import TrackedBlock
from .tracking import dialogue_keys_similar


@dataclass
class FrameGate:
    """Решение по кадру и то, что оно изменило.

    `closed` - кадр закрыт: снимок уже взят и разобран, дальше по нему делать
    нечего, ждём следующий. Одиннадцать `continue` внутри тела цикла были
    ровно этим, но выглядели одиннадцатью разными решениями.
    """

    closed: bool = False

    # Что блок прежде менял на месте.
    need_clean: bool = False
    force_ocr: bool = False
    hold_hidden: bool = False
    last_dirty: object | None = None
    last_key: str = ""
    motion_hits: int = 0
    pending_key: str = ""
    pending_hits: int = 0


def decide_frame(
    state,
    cyc,
    blocks_now: list[dict],
    tracked: list[TrackedBlock],
    last_dirty,
    last_key: str,
    motion_hits: int,
    pending_key: str,
    pending_hits: int,
    need_clean: bool,
    force_ocr: bool,
    hold_hidden: bool,
    last_ocr_lang: str | None,
    last_clean_check: float,
    stable: bool,
    videoish: bool,
    loop_sleep: float,
    geom: str,
    region: tuple[int, int, int, int],
    rw: int,
    rh: int,
    now: float,
) -> FrameGate:
    """Нужен ли на этом кадре распознаватель. Побочный эффект: снимок.

    Тело перенесено дословно. Изменена только форма возврата: `continue`
    стал `closed = True` плюс `return`, а восемь переменных, которые блок
    прежде менял на месте, едут в `FrameGate` и возвращаются с ним.
    """
    from .snap import grim_region

    gate = FrameGate(
        need_clean=need_clean,
        force_ocr=force_ocr,
        hold_hidden=hold_hidden,
        last_dirty=last_dirty,
        last_key=last_key,
        motion_hits=motion_hits,
        pending_key=pending_key,
        pending_hits=pending_hits,
    )

    # Порог для полосы под оверлеем. В теле цикла он жил ВЫШЕ этого блока и
    # поэтому был виден обеим ветвям: stable-ветка переопределяет его ниже на
    # свой `probe_iv`, а видео-ветка читает ровно это значение. Перенеси его
    # внутрь одной ветки - и видео начнёт читать либо чужой порог, либо
    # непрочитанное имя; в первом случае полоса перечитывается слишком часто,
    # во втором - цикл падает. Pyright ловит второе, но не первое.
    dirty_probe_ok = (now - float(cyc.last_dirty_probe)) >= (0.9 if videoish else 0.35)

    if not need_clean:
        # eng-ui + уже есть карточки: dirty-кадр ВСЕГДА «меняется» из‑за наших плашек
        # → ложный need_clean → OCR 99% CPU. Только редкий clean-check.
        # НО dialogue/баббл: обязательно dirty-probe — иначе старый RU мигает, пока EN уже другой.
        if stable and last_ocr_lang == "eng-ui":
            dlg_cards = live_dialogue_cards(blocks_now)
            static_menu = is_static_ui_overlay(blocks_now)
            # VN Name: реплика vs обычный сайт/абзац без Name:
            vn_named = any(
                re.match(r"^[^:]{2,28}:\s+\S", str(b.get("source") or ""))
                for b in (dlg_cards or [])
            )
            # A page is re-read only when the pixels outside our cards move. That is the
            # right trade for a page, and the wrong one for a game scene that
            # happens to have no speaker name: it is not that such a scene is
            # static, it is that nothing about its layout says so. Deriving
            # this from the absence of a name made every nameless scene a
            # page, and a page on a changed frame was still resolved by
            # re-reading the boxes already tracked - which belong to the
            # previous scene.
            #
            # A scene change is therefore decided by the pixels, not by the
            # classification, and it takes the full path with the tracker
            # cleared.
            page_static = static_menu and not videoish
            if page_static and not dlg_cards and not vn_named and tracked:
                page_static = False
            ui_recheck = (
                999.0  # статичное меню/сайт: только по motion
                if page_static
                else (
                    0.45
                    if dlg_cards
                    else (1.05 if len(blocks_now) <= 10 else SETTINGS.interval_ui_stable)
                )
            )
            due = now - last_clean_check >= ui_recheck
            motion_force = False
            probe_iv = 1.0 if page_static else 0.35
            dirty_probe_ok = (now - float(cyc.last_dirty_probe)) >= probe_iv

            if page_static and dirty_probe_ok:
                dirty = grim_region(geom)
                if dirty is not None:
                    cyc.last_dirty_probe = now
                    if gate.last_dirty is not None and probe_changed(
                        gate.last_dirty,
                        probe(dirty),
                        blocks_now,
                        (rw, rh),
                        SETTINGS.change_mean * 1.25,
                    ):
                        motion_force = True
                    gate.last_dirty = probe(dirty)
                if not motion_force:
                    time.sleep(min(0.55, loop_sleep))
                    gate.closed = True
                    return gate
                gate.need_clean = True
                gate.force_ocr = True
                # hide нужен чтобы OCR не читал свои плашки, но только при реальном motion
                gate.hold_hidden = True
                tlog("ui-page-recheck (motion)")
            elif static_menu and dirty_probe_ok:
                # меню: трогаем ТОЛЬКО если кадр реально сменился
                dirty = grim_region(geom)
                if dirty is not None:
                    cyc.last_dirty_probe = now
                    if gate.last_dirty is not None and probe_changed(
                        gate.last_dirty,
                        probe(dirty),
                        blocks_now,
                        (rw, rh),
                        SETTINGS.change_mean * 1.15,
                    ):
                        motion_force = True
                        tlog("ui-menu-motion → clean")
                    gate.last_dirty = probe(dirty)
                if not motion_force:
                    time.sleep(min(0.5, loop_sleep))
                    gate.closed = True
                    return gate
                gate.need_clean = True
                gate.force_ocr = True
                tlog("ui-menu-recheck (motion)")
            elif dlg_cards and dirty_probe_ok:
                dirty = grim_region(geom)
                if dirty is not None:
                    cyc.last_dirty_probe = now
                    if gate.last_dirty is not None and probe_changed(
                        gate.last_dirty,
                        probe(dirty),
                        blocks_now,
                        (rw, rh),
                        SETTINGS.change_mean * 0.85,
                    ):
                        motion_force = True
                        tlog("ui-dlg-motion → hide-clean")
                    gate.last_dirty = probe(dirty)
                if not due and not motion_force:
                    time.sleep(min(0.22, loop_sleep))
                    gate.closed = True
                    return gate
                # таймер без motion на VN — всё ещё можно глянуть, но без лишнего clear
                gate.need_clean = True
                gate.force_ocr = True
                # A still frame (gwenview, paused game) is not a scene change.
                # Hiding on the timer left the original English on screen and
                # made Win+Shift+T look like the overlay did nothing.
                if motion_force:
                    gate.hold_hidden = True
                tlog("ui-dlg-recheck hide" + (" (motion)" if motion_force else ""))
            else:
                if not due:
                    time.sleep(min(0.45, loop_sleep))
                    gate.closed = True
                    return gate
                gate.need_clean = True
                gate.force_ocr = True
        else:
            return _when_capture_is_due(
                gate, state, cyc, blocks_now, last_ocr_lang, videoish,
                dirty_probe_ok, loop_sleep, geom, region, now,
            )

    return gate


def _when_capture_is_due(
    gate, state, cyc, blocks_now, last_ocr_lang, videoish,
    dirty_probe_ok, loop_sleep, geom, region, now,
):
    """Кадр уже пора снимать: грязный снимок решает, прятать ли карточки.

    Тело перенесено из `decide_frame` без изменения порядка веток. Функция
    решения остаётся короткой: здесь только разбор уже снятого грязного кадра.
    """
    from ..ocr.passes import extend_dialogue_with_bottom_pass, offset_boxes
    from .snap import grim_region, soft_probe_is_real_dialogue

    dirty = grim_region(geom)
    if dirty is None:
        time.sleep(loop_sleep)
        gate.closed = True
        return gate
    # dirty OCR только для субтитров/видео/JP
    dirty_key = ""
    if videoish or last_ocr_lang in ("eng-subtitle", "jpn-game"):
        dirty_sub = subtitle_ocr_lines(dirty)
        dirty_key = " ".join(p["text"] for p in dirty_sub) if dirty_sub else ""
    dirty_changed = (
        gate.last_dirty is not None
        and frame_changed(
            gate.last_dirty, probe(dirty), SETTINGS.change_mean_videoish
        )
    )
    if (
        dirty_changed
        and dirty_key
        and gate.last_key
        and len(dirty_key) >= 12
        and soft_probe_is_real_dialogue(dirty_key, last_ocr_lang)
        and not dialogue_keys_similar(dirty_key, gate.last_key)
    ):
        tlog(f"dirty-key-change hud-keep '{dirty_key[:48]}'")
        state.replace_keeping_hud(region)
        gate.last_key = ""
        gate.pending_key = ""
        gate.pending_hits = 0
        gate.need_clean = True
        gate.force_ocr = True
        gate.hold_hidden = not bool(state.peek_blocks())
        gate.last_dirty = probe(dirty)
    elif dirty_changed and dirty_key and is_overlay_echo_ocr(dirty_key):
        # эхо плашки при смене пикселей — всё равно нужен clean OCR
        tlog("dirty-key echo → clean")
        gate.need_clean = True
        gate.force_ocr = True
        gate.last_dirty = probe(dirty)
    elif gate.last_dirty is None:
        gate.last_dirty = probe(dirty)
        time.sleep(loop_sleep)
        gate.closed = True
        return gate
    elif not frame_changed(
        gate.last_dirty,
        probe(dirty),
        min(SETTINGS.change_mean_videoish, SETTINGS.change_mean * 1.2),
    ):
        # видео-диалоги: даже под оверлеем глянем нижнюю полосу dirty-кадра
        if videoish and dirty.height >= 400 and dirty_probe_ok:
            cyc.last_dirty_probe = now
            y0 = int(dirty.height * 0.42)
            band = dirty.crop((0, y0, dirty.width, dirty.height))
            try:
                quick = offset_boxes(rapid_ocr_lines(band), dy=y0)
            except Exception:
                quick = []
            blocks = collect_subtitle_blocks(quick, dirty.width, dirty.height)
            if blocks:
                blocks = extend_dialogue_with_bottom_pass(dirty, blocks)
            qkey = "|".join(b["text"] for b in blocks) if blocks else ""
            if qkey and is_overlay_echo_ocr(qkey):
                # под оверлеем не видно EN — не «всё ок», копим к clean
                echo_n = int(cyc.dirty_echo) + 1
                cyc.dirty_echo = echo_n
                gate.last_dirty = probe(dirty)
                if echo_n >= 2:
                    tlog("dirty-dialogue echo → clean")
                    cyc.dirty_echo = 0
                    gate.need_clean = True
                    gate.force_ocr = True
                else:
                    tlog("dirty-dialogue echo wait")
                    time.sleep(loop_sleep)
                    gate.closed = True
                    return gate
            elif (
                qkey
                and gate.last_key
                and soft_probe_is_real_dialogue(qkey, last_ocr_lang)
                and not dialogue_keys_similar(qkey, gate.last_key)
            ):
                cyc.dirty_echo = 0
                tdetail(f"dirty-dialogue-hit hud-keep {blocks[0]['text'][:56]!r}")
                state.replace_keeping_hud(region)
                gate.last_key = ""
                gate.pending_key = ""
                gate.pending_hits = 0
                gate.need_clean = True
                gate.force_ocr = True
                gate.hold_hidden = True
                gate.last_dirty = probe(dirty)
            elif not qkey and gate.last_key and blocks_now:
                cyc.dirty_echo = 0
                # полоса пустая под оверлеем — подозрение что реплика ушла
                d_empty = int(cyc.dirty_empty) + 1
                cyc.dirty_empty = d_empty
                if d_empty >= 2:
                    tlog("dirty-empty drop-dialogue")
                    state.replace_keeping_hud(region)
                    gate.last_key = ""
                    gate.pending_key = ""
                    gate.pending_hits = 0
                    cyc.dirty_empty = 0
                    gate.need_clean = True
                    gate.force_ocr = True
                    gate.hold_hidden = True
                gate.last_dirty = probe(dirty)
                if not gate.need_clean:
                    time.sleep(loop_sleep)
                    gate.closed = True
                    return gate
            else:
                cyc.dirty_echo = 0
                cyc.dirty_empty = 0
                gate.last_dirty = probe(dirty)
                gate.motion_hits = max(0, gate.motion_hits - 1)
                time.sleep(loop_sleep)
                gate.closed = True
                return gate
        else:
            gate.last_dirty = probe(dirty)
            gate.motion_hits = max(0, gate.motion_hits - 1)
            time.sleep(loop_sleep)
            gate.closed = True
            return gate
        if not gate.need_clean:
            time.sleep(loop_sleep)
            gate.closed = True
            return gate
    # что-то на экране поехало — нужна чистая проверка
    gate.need_clean = True
    gate.force_ocr = True
    gate.last_dirty = probe(dirty)
    return gate
