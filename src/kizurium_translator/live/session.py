"""Живой перевод: цикл кадра, состояние, детект изменений.

Верхний слой. Смотрит на все остальные и сам не смотрит ни на кого выше.
Сюда живёт то, что нельзя назвать ни текстом, ни геометрией, ни
переводом: когда кадр считать изменившимся, какой блок пометить грязным,
когда остановиться.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

# Before Gtk loads: a11y bus is often masked; GTK itself suggests this.
os.environ.setdefault("GTK_A11Y", "none")

from gi.repository import Gdk, GLib, Gtk
from PIL import Image

from .. import autotrace
from .. import trace as trace_mod
from .. import watch as watch_mod
from ..config import (  # noqa: F401
    Config,
)
from ..core import text  # noqa: F401
from ..core.models import (  # noqa: F401
    box_center_offset,
    box_iou,
    box_size_ratio,
    boxes_overlap,
)
from ..core.scripts import (  # noqa: F401
    pattern_of,
    script_of,
    target_language,
)
from ..core.text import (  # noqa: F401
    PATHS,
    RE_CJK,
    RE_CYR,
    RE_HAN,
    RE_KANA,
    RE_LAT,
    SCRIPT_EN,
    SCRIPT_JA,
    SCRIPT_MIXED,
    SCRIPT_UNKNOWN,
    UI_LINES_MAX,
    UI_SHORT_LABELS,
    _core_read_beats,
    _crop_box,
    _english_word_set,
    _iou,
    _merge_language_halves,
    _reading_quality,
    _reads_as_prose,
    block_lang,
    block_script,
    box_overlap_ratio,
    cap_ui_lines,
    clamp,
    clean_ocr_text,
    dialogue_looks_incomplete,
    engine_enabled,
    game_ui_score,
    is_hud_spam,
    is_subtitle_junk_line,
    log_eng_ui_lines,
    log_ocr_coverage,
    looks_like_spoken_line,
    normalize_for_compare,
    normalize_subtitle_ocr,
    ocr_coverage_stats,
    quad_angle_deg,
    quad_depth,
    script_cjk_score,
    soft_descan_panel,
    strip_watermark_tail,
    tdetail,
    tlog,
)
from ..layout import grouping  # noqa: F401
from ..layout.grouping import (
    is_desktop_chrome,
    is_static_ui_overlay,
    split_vn_speaker_line,  # noqa: F401
)
from ..ocr import engine  # noqa: F401
from ..ocr.engine import (  # noqa: F401
    _OCR_LOCK,
    CORE_REREAD_BELOW,
    DET_SIDE_LEN,
    OCR_THREADS,
    RAPID_INIT_FAILED,
    RAPID_OCR,
    _rapid_rows,
    collect_subtitle_blocks,
    english_ocr_quality,
    extract_dialogue_choice_lines,
    is_garbage_ocr,
    is_overlay_echo_ocr,
    live_dialogue_cards,
    looks_like_dialogue_choice,
    looks_like_gameplay_hud_line,
    looks_like_ocr_mojibake_of_russian,
    looks_like_subtitle_continue,
    normalize_japanese_text,
    skip_source,
    stitch_rows_by_baseline,
    stitch_subtitle_fragments,
    subtitle_ocr_lines,
    tess_fill_sparse_ui,
    ui_line_fingerprints,
)
from ..ocr.frame import read_frame
from ..paths import (  # noqa: F401
    Paths,
    default_paths,
)
from ..render import cards  # noqa: F401
from ..render.cards import (  # noqa: F401
    click_through,
    make_block,
)
from ..render.overlay import (  # noqa: F401
    draw_blocks,
)
from ..threads import budget
from ..translation import service  # noqa: F401
from ..translation.batch import translate_many
from ..translation.service import (  # noqa: F401
    GLOSSARY,
    TRANSLATION_DISABLED,
    drop_bad_ocr_boxes,
    filter_plausible_lines,
    finish_mixed_frame,
    glossary_translation,
    is_furigana_reading,
    is_garbage_japanese,
    is_speaker_name,
    is_translation_error,
    japanese_ocr_quality,
    parse_marked_translation,
    refine_japanese_blocks,
    require_ocr,
    same_line,
)
from ..typography.metrics import (  # noqa: F401
    RE_JPN,
    _merge_vn_band_cluster,
    clear_measurement_cache,
    dedupe_near_ui_lines,
    is_ink_band_garbage,
    is_latin_label,
    looks_like_game_ui,
    looks_like_ui_prompt,
    merge_subtitle_cluster,
    reread_core_ink,
    ui_text_fingerprint,
    unglue_english,
    vn_lines_should_merge,
)
from .build import build_blocks
from .change import (  # noqa: F401
    _scene_swap_is_likely,
    change_outside_blocks_is_text,
    dirty_block_ids,
    frame_changed,
    needs_full_reanchor,
    probe,
    probe_changed,
)
from .gate import decide_frame
from .incremental import read_dirty_regions
from .prepare import (
    arrange_lines,
    drop_self_echo,
    soft_recheck,
)
from .reconcile import (  # noqa: F401
    _box_has_ink,
    _merge_cards_by_position,
    _reading_key,
    cyrillic_probe_due,
    filter_echo_of_overlay_cards,
    merge_incremental_cards,
    merge_overlapping_duplicates,
    pack_translation_to_line_boxes,
    rapid_ocr_lines,
    rapid_ocr_lines_cyrillic,
    reset_cyrillic_probe,
)
from .runtime import (  # noqa: F401
    SETTINGS,
    TRANSLATION,
    shutdown,
    translator,
)
from .scene import decide_scene
from .scheduler import (  # noqa: F401
    CYCLE_DUTY_CAP,
    GAME_RECHECK_S,
    SLOW_CYCLE_S,
    VIDEO_RECHECK_S,
    confirm_gone,
    gone_grace,
)
from .snap import clean_capture, grim_region
from .state import (  # noqa: F401
    CycleCounters,
    State,
    TrackedBlock,
    _FrameQueue,
    block_source_lang,
    source_lang_for,
)
from .stats import SessionStats  # noqa: F401
from .tracking import (  # noqa: F401
    any_block_text_changed,
    block_content_changed,
    changed_matched_blocks,
    dialogue_keys_similar,
    keys_similar,
    scene_changed,
    significant_word_diff,
    track_blocks,
    tracking_match_rate,
    update_dirty_blocks,
)

GUI_ERROR = ""


APP_ID_OVERLAY = "ru.kizurium.translator.overlay"


# Пороги кадра лежат здесь, а не в модульных переменных: при разделении
# `live.py` на слои каждый получил свою копию, и `configure()` писал в одну, а
# остальные продолжали читать исходную. Настройка применялась и не действовала.


# Состояние перевода: переводчик и пул задаются при настройке и
# принадлежат слою перевода, а не сессии.
TRANSLATION.pool = ThreadPoolExecutor(max_workers=budget().translation_executor)


LAYER_NAMESPACE = "kizurium-translator"


TRANSLATE_BUDGET = 96


SCRIPT_LANGS = {
    "Japanese": "jpn+eng",
    "Hangul": "kor+eng",
    "Korean": "kor+eng",
    "Han": "chi_sim+eng",
    "HanS": "chi_sim+eng",
    "HanT": "chi_tra+eng",
    "Chinese": "chi_sim+eng",
    "Cyrillic": "rus+eng",
    "Latin": "eng+rus",
}


def _lock_paths() -> tuple[Path, Path]:
    """Lock and pid file of the paths in force.

    These were constants built from the default paths when this module was
    imported, so `configure(paths=...)` moved the data directory while the
    overlay went on watching a lock nobody held and writing its pid somewhere
    the `--stop` side, which reads the paths it was given, never looked.
    """
    return text.PATHS.lock, text.PATHS.pid


class _Pix:
    """Ответ шага «пиксели не изменились»: пропустить кадр или читать дальше."""

    def __init__(self, skip: bool, motion_hits: int, last_clean: Image.Image | None) -> None:
        self.skip = skip
        self.motion_hits = motion_hits
        self.last_clean = last_clean


def skip_unchanged_pixels(
    last_clean, motion_hits, force_ocr, stable, region_img, tracked, rw, rh,
    hold_hidden, state, loop_sleep, last_ocr_lang, videoish, blocks_now,
) -> _Pix:
    """Пиксели почти те же — полный проход не нужен."""
    if last_clean is not None:
        thr = SETTINGS.change_mean_videoish if motion_hits >= 3 else SETTINGS.change_mean
        probe_old = probe(last_clean)
        probe_new = probe(region_img)
        content_changed = frame_changed(probe_old, probe_new, thr)
        if content_changed:
            motion_hits = min(12, motion_hits + 1)
        elif stable and not force_ocr:
            # Пиксели «почти те же» — но НЕ пропускаем OCR на periodic_recheck:
            # Tips A→B почти идентичны (global≈0.8), иначе вечный старый перевод + мигание.
            motion_hits = max(0, motion_hits - 1)
            if hold_hidden:
                state.show()
            time.sleep(loop_sleep)
            return _Pix(True, motion_hits, last_clean)
        elif (
            content_changed
            and SETTINGS.incremental
            and tracked
            and not force_ocr
        ):
            # Something moved and none of the tracked elements moved with
            # it. Before reading the frame again, ask what the movement is:
            # a cursor, a bar and a video behind the panel are movement
            # without ink, and they were costing a full pass every frame.
            # New text is ink, and it is ink outside every box we know.
            # The periodic recheck is excluded on purpose - it is the safety
            # net, and it has to be able to overrule this.
            looks_text, area, ink = change_outside_blocks_is_text(
                probe_old,
                probe_new,
                tracked,
                rw,
                rh,
                temporal_persistence=float(motion_hits),
            )
            if not looks_text:
                tlog(f"probe-skip motion area={area:.3f} ink={ink:.3f}")
                motion_hits = max(0, motion_hits - 1)
                if hold_hidden:
                    state.show()
                time.sleep(loop_sleep)
                return _Pix(True, motion_hits, last_clean)
        if force_ocr and not content_changed:
            tlog("recheck-ocr (soft UI change)")
            # сайт/статья: ложный motion или шум — не дёргать OCR/плашки
            if (
                last_ocr_lang == "eng-ui"
                and stable
                and not videoish
                and not any(
                    re.match(r"^[^:]{2,28}:\s+\S", str(b.get("source") or ""))
                    for b in (blocks_now or [])
                )
            ):
                if hold_hidden:
                    state.show()
                last_clean = region_img
                time.sleep(loop_sleep)
                return _Pix(True, motion_hits, last_clean)
    return _Pix(False, motion_hits, last_clean)


def require_gui() -> None:
    if GUI_ERROR:
        raise RuntimeError(
            f"GUI dependencies unavailable ({GUI_ERROR}). "
            "Install gtk4, gtk4-layer-shell, python-gobject and python-cairo, "
            "then run: kizurium-translator --doctor"
        )


def worker(state: State, geom: str, rx: int, ry: int, rw: int, rh: int, stop: threading.Event) -> None:
    cache: dict[str, str] = {}
    last_key = ""
    last_clean: Image.Image | None = None
    last_dirty: Image.Image | None = None
    last_ocr_lang: str | None = None
    region = (rx, ry, rw, rh)
    motion_hits = 0
    pending_key = ""
    pending_hits = 0
    empty_hits = 0
    last_clean_check = 0.0
    # Сводка кадра. Одна строка на кадр, и она пишется в начале следующего
    # цикла: цикл кончается в десятке разных выходах, и искать единственную
    # точку выхода в них - значит каждый раз их переписывать. Так сводка
    # приходит целой всегда, а читается ровно там, где нужна: когда смотришь
    # на экран и хочешь понять, что только что произошло.
    frame_sum = watch_mod.FrameSummary()
    tracked: list[TrackedBlock] = []
    next_block_id = 1
    last_full_ocr = 0.0
    # Счётчики цикла живут в объекте, а не полями на этой функции: состояние
    # между кадрами должно быть видно, а не спрятано в атрибутах функции.
    cyc = CycleCounters()
    # what the session has cost so far. Separate from `cyc`, which is
    # state that decides what to do next; this only measures.
    stats = SessionStats()

    # Single-slot frame queue: latest frame wins. A new frame overwrites any
    # waiting one; the OCR worker checks the version after processing and
    # discards its result if a newer frame arrived.
    frame_queue: _FrameQueue = _FrameQueue()
    # Frame version at which OCR started; if it changes during OCR, the
    # result is stale and must be discarded.
    ocr_frame_version = 0

    while not stop.is_set() and os.path.exists(_lock_paths()[0]):
        if frame_sum.dirty:
            frame_sum.emit()
            frame_sum = watch_mod.FrameSummary()
            autotrace.reset_counts()
        trace_mod.reset_claims()
        now = time.monotonic()
        blocks_now, _, _ = state.snapshot()
        stable = bool(last_key and blocks_now)
        videoish = last_ocr_lang in ("eng-subtitle", "jpn-game") or (
            last_ocr_lang == "eng-ui" and ":" in (last_key or "")[:40]
        )
        dialogue_live = last_ocr_lang in ("eng-subtitle", "jpn-game")
        loop_sleep = SETTINGS.interval_sub if videoish else (
            SETTINGS.interval_ui_stable if (stable and last_ocr_lang == "eng-ui") else SETTINGS.interval
        )
        hide_s = SETTINGS.hide_for_sub_s if videoish else SETTINGS.hide_for_capture_s

        # На паузе/стабильной реплике не мигаем hide каждый цикл:
        # сначала смотрим «грязный» кадр; чистый — редко или при движении.
        # Видео: реже force-OCR — иначе 700–2000ms OCR каждые 0.5с → 98% CPU / 80°C.
        # eng-ui: НЕ periodic soft-OCR — иначе вечный RapidOCR по статичному сайдбару.
        recheck_s = (
            0.7
            if dialogue_live
            else (GAME_RECHECK_S if last_ocr_lang == "jpn-game" else VIDEO_RECHECK_S * 1.6)
        )
        periodic_recheck = bool(
            dialogue_live
            and last_key
            and now - last_clean_check >= recheck_s
        )
        need_clean = (
            last_clean is None
            or not stable
            or periodic_recheck
            or (
                motion_hits >= 1
                and now - last_clean_check
                >= (1.8 if dialogue_live else VIDEO_RECHECK_S)
            )
        )
        force_ocr = periodic_recheck
        # periodic full re-anchor on a quiet UI (~5s), separate from
        # the faster dialogue soft-recheck above.
        if not force_ocr and last_full_ocr > 0.0:
            reanchor, reason = needs_full_reanchor(
                tracked_count=len(tracked),
                seconds_since_full=now - last_full_ocr,
            )
            if reanchor and reason == "periodic":
                force_ocr = True
                need_clean = True
                tlog(f"reanchor periodic after={now - last_full_ocr:.1f}s")
        hold_hidden = False
        # Порог для полосы под оверлеем уехал в `live.gate` вместе с решением,
        # которое его читает: в теле цикла он больше не используется.

        # A scene swap is decided here, outside every branch below and outside
        # the stable/eng-ui gate and the videoish test, because all three are
        # derived from the cards of the frame being replaced. Moving from a
        # prose scene to a dialogue one changes last_ocr_lang, which closes the
        # first gate, and a colon in the text marks it videoish, which closes
        # the second. Gating on either meant the check stopped running exactly
        # when it was needed, and the previous scene kept its cards.
        #
        # The reference is last_clean, not last_dirty: last_dirty is cleared
        # after every clean capture so the next pass records a fresh frame
        # without hiding, which left nothing to compare against and made the
        # check unreachable in practice.
        if tracked and last_clean is not None:
            fresh = grim_region(geom)
            if fresh is not None and _scene_swap_is_likely(
                probe(last_clean), probe(fresh), blocks_now, (rw, rh)
            ):
                tlog("scene-swap → drop tracked cards")
                tracked.clear()
                blocks_now = []
                # The drawn cards are what the next cycle reconciles against, not
                # only the tracker: merge_incremental_cards starts from whatever
                # is currently on screen, so clearing the tracker alone left the
                # previous scene's cards to be carried into the new one.
                state.set([], None, "")
                last_key = ""
                need_clean = True
                force_ocr = True
                hold_hidden = True
                # The reference frame becomes the new scene, so the next pass
                # compares against it rather than repeating the verdict against
                # the frame that just left.
                last_dirty = probe(fresh)
                last_clean_check = 0.0
                stable = False
                cyc.last_dirty_probe = now
                time.sleep(min(0.3, loop_sleep))
                continue

        # Detect change: стоит ли читать этот кадр заново. Решение и всё, что
        # оно меняет, живут в `live.gate`; здесь остаётся только раздать
        # результат обратно. Одиннадцать выходов этого блока стали одним
        # `closed` с именем причины.
        frame_gate = decide_frame(
            state,
            cyc,
            blocks_now,
            tracked,
            last_dirty,
            last_key,
            motion_hits,
            pending_key,
            pending_hits,
            need_clean,
            force_ocr,
            hold_hidden,
            last_ocr_lang,
            last_clean_check,
            stable,
            videoish,
            loop_sleep,
            geom,
            region,
            rw,
            rh,
            now,
        )
        need_clean = frame_gate.need_clean
        force_ocr = frame_gate.force_ocr
        hold_hidden = frame_gate.hold_hidden
        last_dirty = frame_gate.last_dirty
        last_key = frame_gate.last_key
        motion_hits = frame_gate.motion_hits
        pending_key = frame_gate.pending_key
        pending_hits = frame_gate.pending_hits
        if frame_gate.closed:
            continue

        # soft-recheck субтитров/JP: сначала dirty без hide — иначе старый RU мигает каждые ~1с
        checked = soft_recheck(
            periodic_recheck, videoish, last_key, need_clean, geom, last_dirty, now,
            cyc, loop_sleep, tracked, rw, rh, last_ocr_lang, state, region,
            pending_key, pending_hits, hold_hidden, last_clean_check,
        )
        last_clean_check = checked.last_clean_check
        last_dirty = checked.last_dirty
        last_key = checked.last_key
        pending_key = checked.pending_key
        pending_hits = checked.pending_hits
        hold_hidden = checked.hold_hidden
        if checked.skip:
            continue

        # Пока на экране живой VN-баббл — не возвращать старый RU до результата OCR.
        # Статичное меню (Preferences/Achievements): НЕ hold_hidden — иначе вечный hide/show.
        if (
            last_ocr_lang == "eng-ui"
            and live_dialogue_cards(blocks_now)
            and not is_static_ui_overlay(blocks_now)
        ):
            hold_hidden = True

        # paint-complete is the barrier; hide_s is only a safety fallback.
        paint_t = 0.16 if (videoish or last_ocr_lang in ("eng-subtitle", "eng-ui")) else 0.28
        # the capture is timed separately from the OCR. On a busy
        # screen it is the part that decides whether the overlay can keep up,
        # and it had no timer at all - only the sum of everything did.
        cap_t0 = time.monotonic()
        captured = clean_capture(
            state,
            geom,
            paint_timeout=paint_t,
            safety_sleep=hide_s,
            restore=False,
        )
        stats.capture.capture.add((time.monotonic() - cap_t0) * 1000.0)
        region_img = captured.image
        # Push the fresh frame into the single-slot queue; any waiting frame
        # is overwritten. The OCR worker will pick up the latest.
        frame_queue.put(region_img)
        if region_img is None:
            if not hold_hidden:
                state.show()
            time.sleep(loop_sleep)
            continue
        if hold_hidden:
            tlog("hold-hidden until set")
        else:
            state.show()
        last_clean_check = time.monotonic()
        last_dirty = None  # следующий цикл сначала запомнит dirty-кадр, без hide

        stayed = skip_unchanged_pixels(
            last_clean, motion_hits, force_ocr, stable, region_img, tracked, rw, rh,
            hold_hidden, state, loop_sleep, last_ocr_lang, videoish, blocks_now,
        )
        motion_hits, last_clean = stayed.motion_hits, stayed.last_clean
        if stayed.skip:
            continue

        # Fast path: if the thumbnail diff localises the change to a small area
        # and every previous element is accounted for, re-read only those boxes
        # and translate only what actually changed. A live UI that updates one
        # label then costs a fraction of a full-frame OCR.
        #
        # Шаг адресного перечита живёт в `live.incremental`: пока он был
        # внутри тела цикла, «решить» и «сделать» были одним куском, и
        # порядок шагов конвейера нельзя было прочитать. Здесь остаётся
        # только вызов и проверка, закрыл шаг кадр или нет.
        incremental_result = read_dirty_regions(
            region_img,
            state,
            tracked,
            last_clean,
            last_key,
            rx,
            ry,
            rw,
            rh,
            now,
        )
        tracked = incremental_result.tracked
        if incremental_result.handled:
            last_key = incremental_result.last_key
            last_clean = incremental_result.last_clean
            last_clean_check = incremental_result.last_clean_check
            last_dirty = incremental_result.last_dirty
            time.sleep(loop_sleep)
            continue

        # Get the latest frame from the queue and remember its version.
        # If a newer frame arrives during OCR, the result is stale and will
        # be discarded at the end of this iteration.
        region_img = frame_queue.take_latest().frame
        ocr_frame_version = frame_queue.version
        stats.capture.note_frame()
        # The card path asks the same box the same question several
        # times per draw, and re-measuring pixels is the most expensive thing in
        # it. The cache is keyed on the frame object, so this only has to keep
        # it from growing, and a new frame cannot read an old answer anyway.
        clear_measurement_cache()
        from ..translation.scheduler import SCHEDULER, ResultIdentity

        ocr_token = ResultIdentity(
            session_id=SCHEDULER.session_id,
            frame_revision=int(ocr_frame_version),
            state_revision=int(SCHEDULER.revision),
        )
        try:
            ocr_t0 = time.monotonic()
            lines, last_ocr_lang = read_frame(region_img, last_ocr_lang or "eng")
            # the full pass. `read_frame` returns the lines it kept,
            # so this is also where the session's line count comes from.
            stats.ocr.note_full((time.monotonic() - ocr_t0) * 1000.0, lines=len(lines or []))
        except Exception:
            if hold_hidden:
                state.show()
            time.sleep(loop_sleep)
            continue

        # discard when a newer frame arrived or the overlay state
        # was wiped while OCR ran.
        if (
            frame_queue.version != ocr_frame_version
            or ocr_token.state_revision != SCHEDULER.revision
            or ocr_token.session_id != SCHEDULER.session_id
        ):
            tlog(
                f"ocr-stale frame_version={ocr_frame_version} now={frame_queue.version} "
                f"state={ocr_token.state_revision}->{SCHEDULER.revision}"
            )
            if hold_hidden:
                state.show()
            time.sleep(loop_sleep)
            continue

        # Remember what is on screen so the next frame can track it per element.
        # The pairs are not decoration: they are the only record of which
        # element in this frame is which element in the last one, and every
        # per-block decision below is made through them. They used to be
        # discarded, which left the whole-frame key below as the only thing
        # deciding whether a changed element got translated.
        prev_tracked_n = len(tracked)
        trk_t0 = time.monotonic()
        tracked, pairs, next_block_id = track_blocks(
            tracked, lines, time.monotonic(), next_block_id
        )
        # which block is which block from one frame to the next. This
        # is what decides whether a stable card survives, so it is timed apart
        # from the OCR it follows.
        stats.tracking.tracking.add((time.monotonic() - trk_t0) * 1000.0)
        # A pair whose content revision moved is the "one dynamic block" case the
        # plan's regression is written about; the rest are what stayed. Both come
        # from the tracking pass itself rather than from a second guess here.
        _changed_now = sum(
            1
            for _prev, _block in pairs
            if int(getattr(_block, "content_revision", 0) or 0)
            > int(getattr(_prev, "content_revision", 0) or 0)
        )
        stats.tracking.note_outcome(
            reused=len(pairs) - _changed_now,
            changed=_changed_now,
            added=max(0, len(lines) - len(pairs)),
            removed=max(0, prev_tracked_n - len(pairs)),
        )
        last_full_ocr = time.monotonic()
        match_rate = tracking_match_rate(prev_tracked_n, len(pairs), len(lines))
        reanchor, reason = needs_full_reanchor(
            tracked_count=max(prev_tracked_n, 1),
            match_rate=match_rate if prev_tracked_n else None,
        )
        if reanchor and reason == "low_match":
            tlog(f"reanchor low_match rate={match_rate:.2f} prev={prev_tracked_n} lines={len(lines)}")
            cyc.force_retry = True

        echoed = drop_self_echo(
            lines, stable, last_ocr_lang, hold_hidden, state, region, last_key,
            region_img, loop_sleep, last_clean,
        )
        lines, last_key, last_clean = echoed.lines, echoed.last_key, echoed.last_clean
        if echoed.skip:
            continue

        # смена языка/режима (например, JP → англ.) — сразу чистим старые плашки
        prev_mode = cyc.prev_mode
        if prev_mode and prev_mode != last_ocr_lang:
            # subtitle↔ui: старая «колодец»-карточка диалога должна уйти сразу
            leaving_sub = prev_mode == "eng-subtitle" and last_ocr_lang != "eng-subtitle"
            soft_eng = (
                {prev_mode, last_ocr_lang} <= {"eng-ui", "eng-subtitle"}
                and not leaving_sub
            )
            if not soft_eng:
                state.clear(region, reason="scene_transition")
            tlog(f"mode-switch {prev_mode}->{last_ocr_lang}"
                f"{' soft' if soft_eng else ' clear'}")
        cyc.prev_mode = last_ocr_lang

        subtitle_mode = last_ocr_lang == "eng-subtitle"
        japanese_mode = last_ocr_lang == "jpn-game"
        dialogue_mode = subtitle_mode or japanese_mode
        eng_ui_mode = last_ocr_lang == "eng-ui"
        raw_preview = " | ".join(p.get("text", "")[:60] for p in lines[:6])
        engines = ",".join(sorted({p.get("engine", "tesseract") for p in lines})) or "none"
        tdetail(f"geom={rx},{ry} {rw}x{rh} mode={last_ocr_lang} ocr={engines} raw={len(lines)} {raw_preview}")
        for p in lines[:6]:
            bx1, by1, bx2, by2 = p["box"]
            tdetail(f"ocr-box '{p.get('text','')[:28]}' "
                f"local=({bx1},{by1})-({bx2},{by2}) screen=({rx+bx1},{ry+by1})")
        lay_t0 = time.monotonic()
        lines = arrange_lines(
            lines, region_img, rw, rh, blocks_now, japanese_mode, subtitle_mode,
            eng_ui_mode, last_ocr_lang or "",
        )
        # grouping lines into paragraphs and giving each a role. It
        # walks the whole frame and touches pixels, and on a dense panel
        # it is a visible share of a cycle that had no breakdown.
        stats.tracking.layout.add((time.monotonic() - lay_t0) * 1000.0)

        key = "|".join(p["text"] for p in lines)
        last_clean = region_img

        # субтитры/JP: 1 пустой кадр → убрать; eng-ui диалог тоже 1 hit (не мигать старым)
        scene = decide_scene(
            state, cyc, blocks_now, pairs, lines, key, last_key,
            empty_hits, pending_key, pending_hits, region,
            eng_ui_mode, dialogue_mode, hold_hidden, loop_sleep,
        )
        # Пять переменных, которые блок менял на месте, теперь возвращаются
        # решением. Условие `handled` закрывает кадр: после него в
        # оригинале ничего не было.
        blocks_now = scene.blocks_now
        last_key = scene.last_key
        empty_hits = scene.empty_hits
        pending_key = scene.pending_key
        pending_hits = scene.pending_hits
        if scene.handled:
            continue
        cyc.ui_scene_change = False
        # eng-ui: не clear→пусто→set (мигание EN), только атомарный set ниже
        # dialogue уже очищен выше при смене

        if not lines:
            state.clear(region, reason="empty_scene")
            last_key = ""
            time.sleep(loop_sleep)
            continue
        tlog(f"mode={last_ocr_lang} blocks={len(lines)} "
            + " || ".join(p.get("text", "")[:48] for p in lines[:6]))

        to_translate = [
            par for par in lines
            if float(par.get("conf", 0)) >= 18
            and (japanese_mode or not skip_source(par["text"]))
            and not is_garbage_ocr(par["text"])
            and not looks_like_ocr_mojibake_of_russian(par["text"])
            and not is_overlay_echo_ocr(par["text"])
            and (
                not japanese_mode
                or not is_garbage_japanese(par["text"])
                or is_speaker_name(par["text"])
                or par.get("kind") in ("chip", "latin", "menu", "name")
            )
            and (RE_LAT.search(par["text"]) or RE_CJK.search(par["text"]))
        ]
        # Неполная реплика не глушит перевод: она только заставляет повторить
        # разбор на следующем цикле. Частичный перевод сразу полезнее пустого
        # экрана.
        kept = []
        for par in to_translate:
            raw_t = str(par.get("text", "")).strip()
            kind = str(par.get("kind", ""))
            long_vn = len(raw_t) >= 36 or kind == "dialogue" or bool(par.get("wrap"))
            if par.get("incomplete") or (
                long_vn
                and "\n" not in raw_t
                and dialogue_looks_incomplete(raw_t)
            ):
                par = dict(par)
                par["force_retry"] = True
                tlog(f"translate-incomplete '{raw_t[:52]}'")
            kept.append(par)
        to_translate = kept
        if eng_ui_mode and len(to_translate) > 32:
            def _prio(p: dict) -> int:
                """What is worth spending a request on, before the cap.

                No list of particular words. Which words matter is a property of
                the page, not of this file, and a hardcoded set of menu names is
                the same mistake as a hardcoded game: it is right on the screen
                it was written against and wrong on every other one. What can be
                said without knowing the page is about size - a long sentence is
                more text to translate than a two-letter label - about shape - a
                heading is short and isolated, a paragraph is not - and about the
                confidence of the read, because a doubtful line is likely to cost
                a request to say nothing.
                """
                text = str(p.get("text", "")).strip()
                x1, y1, x2, y2 = p["box"]
                w_box = max(1, x2 - x1)
                h_box = max(1, y2 - y1)
                letters = len(re.findall(r"[A-Za-zА-Яа-яЁё]", text))
                if not letters:
                    return 0
                conf = float(p.get("conf", 0.0) or 0.0)
                # Characters, not pixels: a long sentence is worth more than a
                # short label whatever the two happen to measure on screen.
                score = letters * 100
                # A heading is a short run of letters in a box that fits them
                # closely. A paragraph is a long run in a box that fits them
                # closely too, so the ratio is what separates the two.
                if letters and h_box:
                    fit = letters / float(max(1, w_box * h_box // 60))
                    score += int(min(4000, fit * 400))
                if conf >= 90.0:
                    score += 3000
                elif conf < 60.0:
                    score -= 6000
                return max(0, score)

            ranked = sorted(to_translate, key=_prio, reverse=True)
            to_translate = ranked[:TRANSLATE_BUDGET]
            if len(ranked) > TRANSLATE_BUDGET:
                tlog(
                    f"translate-priority cap={TRANSLATE_BUDGET} "
                    f"of {len(ranked)} by text size and read confidence"
                )
        if not to_translate:
            to_translate = [
                par
                for par in lines
                if float(par.get("conf", 0)) >= 12
                and (RE_LAT.search(par["text"]) or RE_CJK.search(par["text"]))
                and (japanese_mode or not skip_source(par["text"]))
                and not is_garbage_ocr(par["text"])
            ]
        if not to_translate:
            tlog("translate-skip empty/incomplete")
            cyc.force_retry = True
            last_key = ""
            stats.note_cycle((time.monotonic() - now) * 1000.0)
            tlog(
                f"cycle_ms={int((time.monotonic()-now)*1000)} "
                f"mode={last_ocr_lang} videoish={int(bool(subtitle_mode or videoish))} "
                f"{stats.summary()}"
            )
            time.sleep(min(0.35, loop_sleep))
        else:

            # НЕ склеивать соседние реплики в одну строку — ломает переносы
            # субтитры/JP 2+: цельный MT, раскладка по ширинам реальных OCR-строк (не mid-split)
            flat_parts: list[str] = []
            part_source_langs: list[str] = []
            part_spans: list[tuple[int, int]] = []
            line_box_refs: list[list[dict] | None] = []
            for par in to_translate:
                lbs = [
                    lb
                    for lb in list(par.get("line_boxes") or [])
                    if not is_subtitle_junk_line(
                        str(lb.get("text", "")),
                        lb.get("box"),
                        rw,
                        rh,
                    )
                ]
                if len(lbs) >= 2 and (
                    dialogue_mode or str(par.get("kind", "")) == "dialogue"
                ):
                    en_parts = [unglue_english(str(lb.get("text", "")).strip()) for lb in lbs]
                    en_parts = [p if p else " " for p in en_parts]
                    full = unglue_english(" ".join(en_parts)) if not japanese_mode else " ".join(
                        str(lb.get("text", "")).strip() for lb in lbs
                    )
                    tlog(f"translate-whole-sub n={len(en_parts)} '{full[:80]}'")
                    part_spans.append((len(flat_parts), 1))
                    flat_parts.append(full)
                    part_source_langs.append(block_source_lang(par, full))
                    line_box_refs.append(lbs)
                elif (
                    eng_ui_mode
                    and str(par.get("kind", "")) == "dialogue"
                    and str(par.get("text", "")).count("\n") >= 1
                ):
                    en_parts = [
                        unglue_english(p.strip())
                        for p in str(par["text"]).split("\n")
                        if p.strip()
                    ]
                    if len(en_parts) >= 2:
                        x1, y1, x2, y2 = par["box"]
                        n = len(en_parts)
                        slice_h = max(12, (y2 - y1) // n)
                        syn_lbs = []
                        for i, ep in enumerate(en_parts):
                            ly1 = y1 + i * slice_h
                            ly2 = y2 if i == n - 1 else y1 + (i + 1) * slice_h - 2
                            syn_lbs.append(
                                {
                                    "text": ep,
                                    "box": (x1, ly1, x2, ly2),
                                    "line_height": max(10, ly2 - ly1),
                                }
                            )
                        full = unglue_english(" ".join(en_parts))
                        tlog(f"translate-whole-vn n={len(en_parts)} '{full[:80]}'")
                        part_spans.append((len(flat_parts), 1))
                        flat_parts.append(full)
                        part_source_langs.append(block_source_lang(par, full))
                        line_box_refs.append(syn_lbs)
                    else:
                        part_spans.append((len(flat_parts), 1))
                        flat_parts.append(en_parts[0] if en_parts else "")
                        part_source_langs.append(block_source_lang(par, flat_parts[-1]))
                        line_box_refs.append(None)
                else:
                    parts = [unglue_english(p) for p in str(par["text"]).split("\n")]
                    if japanese_mode:
                        parts = [p for p in str(par["text"]).split("\n")]
                    if len(parts) == 1:
                        tlog(f"translate-oneline chars={len(parts[0])} '{parts[0][:48]}'")
                    part_spans.append((len(flat_parts), len(parts)))
                    flat_parts.extend(parts)
                    part_source_langs.extend(block_source_lang(par, pt) for pt in parts)
                    line_box_refs.append(None)
            if not flat_parts:
                tlog("translate-skip empty parts")
                cyc.force_retry = True
                last_key = ""
                time.sleep(min(0.35, loop_sleep))
            else:
                # One frame can hold English and Japanese side by side, so the language
                # travels with each part instead of being decided for the whole batch.
                tr_items = [
                    {
                        "text": part,
                        "source": part_source_langs[i] if i < len(part_source_langs) else "",
                    }
                    for i, part in enumerate(flat_parts)
                ]
                tr_t0 = time.monotonic()
                flat_tr = translate_many(tr_items, cache)
                # the backend call itself. The queueing time - how long
                # a line waited before the worker got to it - is measured at the
                # point the line was queued, not here, because by the time it
                # reaches this call the wait is over and unmeasurable.
                stats.translation.translation.add((time.monotonic() - tr_t0) * 1000.0)
                stats.translation.note_cache(*stats.translation.take_batch())
                tlog(f"translate-many-result n={len(flat_tr)} all={[str(x)[:50] for x in flat_tr]}")
                translations = []
                for (start, n), lbs in zip(part_spans, line_box_refs):
                    chunk = flat_tr[start : start + n]
                    if lbs is not None and len(chunk) == 1:
                        ru = re.sub(r"\s+", " ", chunk[0]).strip()
                        en_parts = [str(lb.get("text", "")).strip() for lb in lbs]
                        split = pack_translation_to_line_boxes(lbs, ru, en_parts)
                        tlog(
                            f"sub-pack-done n={len(lbs)} "
                            + " || ".join(s[:36] for s in split if s)
                        )
                        translations.append("\n".join(split))
                    else:
                        translations.append("\n".join(chunk))
                # обновить line_boxes у to_translate после фильтра junk
                for par, lbs in zip(to_translate, line_box_refs):
                    if lbs is not None:
                        par["line_boxes"] = lbs
                        if len(lbs) == 1:
                            par["text"] = lbs[0].get("text", par.get("text", ""))
                            par["box"] = lbs[0]["box"]
                tlog(
                    f"breaks preserved pars={len(to_translate)} "
                    f"lines={len(flat_parts)} multiline={sum(1 for _, n in part_spans if n > 1)}"
                )
                blk_t0 = time.monotonic()
                blocks = build_blocks(
                    zip(to_translate, translations),
                    japanese_mode=japanese_mode,
                    dialogue_mode=dialogue_mode,
                    eng_ui_mode=eng_ui_mode,
                    region_img=region_img,
                    rx=rx,
                    ry=ry,
                    rw=rw,
                    rh=rh,
                )
                # measuring the original and laying the translation
                # against it. It reads pixels per card and it is the part that
                # grew when fonts, pitch and row handling were added.
                stats.render.render_prepare.add((time.monotonic() - blk_t0) * 1000.0)
                stats.render.cards_built += len(blocks)

                # атомарно: не clear→пусто→set (из-за этого мигало на паузе)
                expected = len(to_translate)
                # watermark / chrome не считаем «пропуском» — иначе вечный partial-miss + мигание
                real_expected = sum(
                    1
                    for par in to_translate
                    if not is_desktop_chrome(par["text"]) and not is_garbage_ocr(par["text"])
                )
                if blocks:
                    # инкремент: peek даже после hide; не wipe весь HUD
                    prev_cards = state.peek_blocks() or blocks_now
                    src_fps = ui_line_fingerprints(key)
                    before_n = len(blocks)
                    rec_t0 = time.monotonic()
                    blocks = merge_incremental_cards(prev_cards, blocks, src_fps)
                    # the step that decides which cards survive. If
                    # this is slow the overlay is re-deciding the whole screen
                    # every cycle, and nothing else in the log would show it.
                    stats.tracking.reconciliation.add((time.monotonic() - rec_t0) * 1000.0)
                    if len(blocks) != before_n or prev_cards:
                        tlog(f"incr-merge prev={len(prev_cards)} new={before_n} out={len(blocks)}")
                    # страховка: мусорная полоска на почти весь экран с коротким текстом.
                    # Длинная VN-реплика (1096px на 1920) занимает больше половины
                    # ширины, поэтому критерий смотрит на абсолютную ширину.
                    blocks = [
                        b
                        for b in blocks
                        if not (
                            float(b.get("src_w", 0)) >= rw * 0.88
                            and len(re.sub(r"\s+", "", str(b.get("text", "") or ""))) <= 20
                        )
                    ]
                    if blocks:
                        state.set(blocks, region, "")
                        stats.render.cards_drawn += len(blocks)
                    elif eng_ui_mode and prev_cards:
                        # пусто после фильтра — не затираем и не «принимаем» ключ
                        tlog("shown=0 after-wide-filter keep-prev")
                        cyc.force_retry = True
                        last_key = ""
                        stats.note_cycle((time.monotonic() - now) * 1000.0)
                        tlog(f"cycle_ms={int((time.monotonic()-now)*1000)} mode={last_ocr_lang} videoish={int(bool(subtitle_mode or videoish))} {stats.summary()}")
                        time.sleep(loop_sleep)
                        continue
                    elif prev_cards:
                        # filter emptied the batch — keep HUD, do not wipe.
                        tlog("shown=0 after-wide-filter keep-prev (no clear)")
                        cyc.force_retry = True
                        last_key = ""
                    else:
                        state.clear(region, reason="empty_scene")
                    sizes = " ".join(
                        f"{int(b.get('src_w',0))}x{int(b.get('src_h',0))}/{b.get('kind') or '-'}"
                        for b in blocks[:4]
                    )
                    tlog(f"shown={len(blocks)}/{expected} cards={sizes} "
                        + " || ".join(b.get("text", "")[:40] for b in blocks[:6]))
                    ok_target = max(1, real_expected)
                    shown_incomplete = any(bool(par.get("force_retry")) for par in to_translate)
                    if shown_incomplete and eng_ui_mode and len(blocks) >= 8:
                        # веб-колонки: не гоняем ещё один 5–9с OCR из‑за 1–2 «incomplete» хвостов
                        last_key = key
                        cyc.force_retry = False
                        cyc.partial_streak = 0
                        tlog(f"shown-incomplete accept-ui n={len(blocks)}")
                    elif shown_incomplete:
                        last_key = ""
                        cyc.force_retry = True
                        cyc.partial_streak = 0
                        tlog("shown-incomplete retry")
                    elif len(blocks) >= ok_target or (real_expected > 0 and len(blocks) / real_expected >= 0.75):
                        last_key = key
                        cyc.force_retry = False
                        cyc.partial_streak = 0
                    elif len(blocks) == 0:
                        last_key = ""
                        cyc.force_retry = True
                        tlog("shown=0 retry")
                    else:
                        streak = int(cyc.partial_streak) + 1
                        cyc.partial_streak = streak
                        if eng_ui_mode and blocks_now and len(blocks) > 0:
                            # не мигать частичным набором — уже merge_keep; принимаем
                            last_key = key
                            cyc.force_retry = False
                            tlog(f"partial-keep ({len(blocks)}/{expected})")
                        elif streak >= 3:
                            last_key = key
                            cyc.force_retry = False
                            tlog(f"partial-accept ({len(blocks)}/{expected})")
                        else:
                            last_key = ""
                            cyc.force_retry = True
                            tlog(f"partial-miss retry ({len(blocks)}/{expected})")
                else:
                    if eng_ui_mode and blocks_now:
                        tlog("shown=0 keep-prev")
                    elif blocks_now:
                        # a failed translate pass must not wipe cards.
                        tlog("shown=0 keep-prev (no clear)")
                        cyc.force_retry = True
                    else:
                        state.clear(region, reason="empty_scene")
                        last_key = ""
                        cyc.force_retry = True
                        tlog("shown=0 retry")
                cost = time.monotonic() - now
                # what this cycle cost, split into the parts. Until now
                # the only number was `cycle_ms` and the only other timing in the
                # log was `ocr_ms`, so "the cycle is slow" had nowhere to point.
                stats.note_cycle(cost * 1000.0)
                tlog(
                    f"cycle_ms={int(cost*1000)} mode={last_ocr_lang} "
                    f"videoish={int(bool(subtitle_mode or videoish))}"
                    f" {stats.summary()}"
                )
                # A cycle that already burned several seconds must not be followed
                # immediately by another one: that is how the loop ended up pinning the
                # CPU. Duty-cycle cap instead of a queue of stale work.
                extra = min(CYCLE_DUTY_CAP, cost * 0.5) if cost > SLOW_CYCLE_S else 0.0
                if extra > 0.05:
                    tlog(f"cycle-backoff +{extra:.2f}s")
                time.sleep(loop_sleep + extra)

    tlog("worker-exit")


"""Live in-place translation: OCR the screen, draw the translation over the original text."""


try:
    from ..layer_shell_lib import preload as _preload_layer_shell

    _preload_layer_shell()
except Exception:  # noqa: BLE001
    pass


try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    import cairo
    from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo
    from gi.repository import Gtk4LayerShell as LayerShell
except Exception as exc:  # noqa: BLE001 - reported by --doctor, not fatal at import
    GUI_ERROR = f"{type(exc).__name__}: {exc}"
    Gtk = Gdk = GLib = Pango = PangoCairo = LayerShell = cairo = None  # type: ignore[assignment]


try:
    import pytesseract
    from PIL import Image, ImageChops, ImageOps, ImageStat
except Exception as exc:  # noqa: BLE001
    OCR_ERROR = f"{type(exc).__name__}: {exc}"
    pytesseract = None  # type: ignore[assignment]
    Image = ImageChops = ImageOps = ImageStat = None  # type: ignore[assignment]


if __name__ == "__main__":
    from .app import main

    raise SystemExit(main())
