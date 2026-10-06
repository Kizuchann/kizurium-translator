"""Сцена или реплика: удержать стабильное, обновить изменившееся.

Ядро live-режима, то, без чего релиз не выходит: жалоба, ради которой всё
затевалось, звучит как «перевод слезает, когда меняется соседний текст».

    кадр 1: стабильная A + меняющийся B1
    кадр 2: стабильная A + меняющийся B2

    A остаётся видимой непрерывно, B обновляется, глобального clear нет

и отдельно сцена сменилась целиком - старое уходит;
сменилась частично - остальное остаётся.

Тело вынесено из `session.worker` без изменения логики, кроме формы
возврата: шесть `continue` стали `handled = True` плюс `return`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ..core.text import looks_like_spoken_line, tdetail, tlog
from ..layout.grouping import is_static_ui_overlay
from ..ocr.engine import live_dialogue_cards, ui_line_fingerprints
from .state import CycleCounters
from .tracking import (
    changed_matched_blocks,
    dialogue_keys_similar,
    keys_similar,
    significant_word_diff,
)


@dataclass
class SceneDecision:
    """Что кадр говорит о сцене и что с этим делать.

    `handled` - кадр закрыт: сцена или реплика не изменилась, ждём
    следующего. В теле цикла шесть выходов из одного места читались как шесть
    разных решений, хотя это одно решение с разными причинами; причины
    остались в логе.
    """

    handled: bool = False
    last_key: str = ""
    pending_key: str = ""
    pending_hits: int = 0
    empty_hits: int = 0
    blocks_now: list | None = None


def decide_scene(
    state,
    cyc: CycleCounters,
    blocks_now: list,
    pairs: list,
    lines: list,
    key: str,
    last_key: str,
    empty_hits: int,
    pending_key: str,
    pending_hits: int,
    region: tuple,
    eng_ui_mode: bool,
    dialogue_mode: bool,
    hold_hidden: bool,
    loop_sleep: float,
) -> SceneDecision:
    """Удержать стабильное, обновить изменившееся, снести ушедшую сцену.

    Тело ниже перенесено из тела цикла `session.worker` скриптом.
    """
    # `is_map_legend_label` живёт в `session`, а `session` зовёт `decide_scene`.
    # На уровне модуля это круговая ссылка, поэтому импорт локальный. Он не в
    # горячем пути: функция зовётся раз в кадр.
    from .snap import is_map_legend_label
    d = SceneDecision(
        last_key=last_key,
        pending_key=pending_key,
        pending_hits=pending_hits,
        empty_hits=empty_hits,
        blocks_now=blocks_now,
    )

    has_dlg_cards = bool(live_dialogue_cards(d.blocks_now))
    need_empty_hits = 1 if (dialogue_mode or has_dlg_cards) else (2 if eng_ui_mode else 2)
    if eng_ui_mode and d.blocks_now and len(d.blocks_now) >= 8:
        need_empty_hits = 1  # карта/меню закрыли — сразу гасить
    if not key:
        d.empty_hits += 1
        if eng_ui_mode and d.blocks_now and d.empty_hits < need_empty_hits and not has_dlg_cards:
            tlog(f"ui-empty-keep hits={d.empty_hits}")
            time.sleep(loop_sleep)
            d.handled = True
            return d
        if d.last_key and d.empty_hits < need_empty_hits:
            time.sleep(loop_sleep)
            d.handled = True
            return d
        tlog(f"clear-empty hits={d.empty_hits}")
        state.clear(region, reason="empty_scene")
        d.last_key = ""
        d.pending_key = ""
        d.pending_hits = 0
        d.empty_hits = 0
        cyc.last_line_n = 0
        time.sleep(loop_sleep)
        d.handled = True
        return d
    d.empty_hits = 0

    prev_n = cyc.last_line_n
    cur_n = len(lines)
    cyc.ui_scene_change = False
    # eng-ui: тосты появились/пропали — сразу обновлять (не keep Blinded поверх Obtained)
    if eng_ui_mode and d.last_key:
        old_fp = ui_line_fingerprints(d.last_key)
        new_fp = ui_line_fingerprints(key)
        added = new_fp - old_fp
        removed = old_fp - new_fp
        if added:
            tdetail(f"ui-lines-added +{len(added)} {list(added)[:3]}")
            cyc.ui_scene_change = True
        if removed:
            tdetail(f"ui-lines-gone -{len(removed)} {list(removed)[:3]}")
            cyc.ui_scene_change = True
    # карта (много легенды) → геймплей (миссия) — жёсткий сброс
    if eng_ui_mode and d.blocks_now and len(d.blocks_now) >= 8:
        map_n = sum(1 for p in lines if is_map_legend_label(str(p.get("text", ""))))
        play_n = sum(
            1
            for p in lines
            if re.search(
                r"\b(Head to|Obtained|Exploring|Waiting|In Combat|Blinded)\b",
                str(p.get("text", "")),
                re.I,
            )
        )
        if map_n <= 2 and play_n >= 1:
            tlog(f"map-to-play map={map_n} play={play_n} force-clear")
            cyc.ui_scene_change = True
    # eng-ui: лёгкая просадка OCR — keep только если ТЕКСТ тот же; иначе replace
    if eng_ui_mode and prev_n >= 4 and cur_n < prev_n and not cyc.ui_scene_change:
        lost = prev_n - cur_n
        scene_change = cur_n <= max(1, int(prev_n * 0.40)) or lost >= 3
        if scene_change:
            tlog(f"hud-scene-change {prev_n}->{cur_n} replace")
            cyc.ui_scene_change = True
        elif cur_n <= max(1, prev_n - 2) and keys_similar(key, d.last_key, 0.90):
            tlog(f"hud-shrink {prev_n}->{cur_n} keep-prev")
            time.sleep(loop_sleep)
            d.handled = True
            return d
        elif cur_n <= max(1, prev_n - 2):
            tlog(f"hud-shrink-diff {prev_n}->{cur_n} replace")
            cyc.ui_scene_change = True
    cyc.last_line_n = cur_n

    # частичный провал перевода — не залипаем, ретраим
    force_retry = bool(cyc.force_retry)
    if force_retry:
        cyc.force_retry = False
        d.last_key = ""

    key_same = (
        dialogue_keys_similar(key, d.last_key)
        if dialogue_mode
        else keys_similar(key, d.last_key)
    )
    # VN/eng-ui баббл: одно слово ≠ «то же» — иначе старый RU мигает поверх новой реплики
    # Меню с кучей лейблов — НЕ vn_live (OCR-шум иначе clear+мигание каждые 3с)
    vn_live = eng_ui_mode and not is_static_ui_overlay(d.blocks_now) and (
        has_dlg_cards
        or (
            any(str(p.get("kind", "")) == "dialogue" for p in lines)
            and any(looks_like_spoken_line(str(p.get("text", ""))) for p in lines)
        )
    )
    if vn_live and d.last_key:
        if significant_word_diff(key, d.last_key) or not keys_similar(key, d.last_key, 0.96):
            key_same = False
    if eng_ui_mode and cyc.ui_scene_change:
        key_same = False
    # статичное меню + почти тот же набор лейблов — не clear/retranslate из-за OCR jitter
    if (
        eng_ui_mode
        and is_static_ui_overlay(d.blocks_now)
        and d.last_key
        and key
        and keys_similar(key, d.last_key, 0.90)
        and not significant_word_diff(key, d.last_key)
    ):
        key_same = True
    # Per block, not per frame. The tracker matched each element in this
    # frame to the element it was in the last one, and each of them has its
    # own answer about whether its text changed. When one of them says it
    # did, that answer is the one that counts: the frame key is a join of
    # every line with a separator, so one changed counter among forty
    # unchanged menu labels moves it by a fraction of a percent and reads as
    # the same screen. Letting that happen is how a counter stayed on its
    # first value while everything around it moved normally.
    #
    # Only matched elements count. An unmatched line is a new element, which
    # is a scene-level question, and scene classification is what the frame
    # key is for.
    changed_elements = changed_matched_blocks(pairs)
    if changed_elements:
        tlog(
            f"block-changed {len(changed_elements)} "
            + " | ".join(b.text[:14] for b in changed_elements[:4])
        )
        key_same = False

    # тот же текст (OCR чуть пляшет) — не трогаем оверлей
    if key_same:
        d.pending_key = ""
        d.pending_hits = 0
        # более полная строка той же реплики — обновим перевод
        if key and len(key) > len(d.last_key) + 8:
            tlog(f"grow-line {len(d.last_key)}->{len(key)}")
            d.last_key = ""  # fall through to translate
        else:
            if key and len(key) > len(d.last_key):
                d.last_key = key
            if hold_hidden:
                # очистили по soft empty/change, а кадр снова та же реплика — вернуть
                state.show()
            time.sleep(loop_sleep)
            d.handled = True
            return d

    # смена реплики: убрать ТОЛЬКО dialogue, HUD оставить (без full wipe)
    ui_scene = bool(eng_ui_mode and cyc.ui_scene_change)
    if d.last_key and d.blocks_now and (dialogue_mode or vn_live):
        tlog("sub-drop-dialogue" if dialogue_mode else "vn-drop-dialogue")
        state.replace_keeping_hud(region)
        d.blocks_now = state.peek_blocks()
    if d.last_key and d.blocks_now and ui_scene and not (dialogue_mode or vn_live):
        # сцена UI сменилась целиком — тогда уже clear
        gone = ui_line_fingerprints(d.last_key) - ui_line_fingerprints(key)
        if len(gone) >= max(3, len(ui_line_fingerprints(d.last_key)) // 2):
            tlog("ui-clear-on-scene")
            state.clear(region, reason="scene_transition")
            d.blocks_now = []
        else:
            tlog("ui-scene-incr (no full clear)")
            d.blocks_now = state.peek_blocks()

    # подтверждение смены: eng-ui / тосты — 1 кадр (иначе pending вечно сбрасывается)
    if d.last_key:
        sim_pending = (
            dialogue_keys_similar(key, d.pending_key)
            if dialogue_mode
            else keys_similar(key, d.pending_key)
        )
        if sim_pending:
            d.pending_hits += 1
        else:
            d.pending_key = key
            d.pending_hits = 1
        need_hits = 1 if (dialogue_mode or eng_ui_mode or ui_scene) else 2
        if d.pending_hits < need_hits:
            tlog(f"pending-hit {d.pending_hits}/{need_hits}")
            time.sleep(loop_sleep)
            d.handled = True
            return d

    d.pending_key = ""
    d.pending_hits = 0

    # Последний путь: сцена или реплика изменилась, кадр не закрыт. Раньше
    # тело просто заканчивалось и управление падало в остаток цикла, поэтому
    # явного `return` здесь не было; вне функции он обязателен.
    return d
