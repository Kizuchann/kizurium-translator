"""Адресный перечит: что на экране изменилось с прошлого кадра.

Один шаг конвейера  - «read dirty regions». Он стоит между «сравнить кадры»
и «сопоставить блоки»: дешёвая миниатюра говорит, какие элементы трогать,
и только они перечитываются распознавателем.

Вынесено из `session.worker` дословно. Причина разделения - не размер, а
направление зависимостей: пока этот кусок лежал в теле цикла, решение
«перечитывать ли адресно» и действие «перечитать и применить» были одним
куском на полторы тысячи строк, и разобрать порядок шагов было невозможно.

Что функция возвращает наружу, перечислено в `IncrementalResult`: всё, что
раньше менялось по месту, теперь возвращается явно.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from PIL import Image

from ..core.text import (
    block_lang,
    block_script,
    normalize_for_compare,
    tdetail,
    tlog,
)
from ..ocr.engine import is_garbage_ocr, is_overlay_echo_ocr
from ..render.cards import make_block
from ..translation.service import is_translation_error, same_line
from .change import dirty_block_ids, probe
from .reconcile import _box_has_ink, _merge_cards_by_position
from .runtime import SETTINGS
from .scheduler import confirm_gone, gone_grace
from .state import TrackedBlock, source_lang_for
from .tracking import block_content_changed, update_dirty_blocks


@dataclass
class IncrementalResult:
    """Что изменилось на экране с прошлого успешного кадра.

    `handled` - ключевое поле: True означает, что шаг закрыл кадр сам и
    основному конвейеру продолжать не нужно. Раньше это был `continue` в
    середине тела цикла, и из-за него порядок шагов приходилось
    восстанавливать по чтению.
    """

    handled: bool = False
    tracked: list[TrackedBlock] = field(default_factory=list)
    last_key: str = ""
    last_clean: Image.Image | None = None
    last_clean_check: float = 0.0
    last_dirty: Image.Image | None = None
    changed_cards: int = 0
    removed_cards: int = 0


_BATCH_CACHE: dict[str, str] = {}


def _translate_batch(items: list[dict]) -> list[str]:
    """Порция на перевод — тот же `translate_many`, что и у основного пути.

    Обёртка существует ради одного: у этой функции своя кэш-словарь на
    инкрементальный шаг. Общий словарь живёт в `worker` и сбрасывается там
    же, а инкрементальный шаг выполняется в своём модуле, и общий ему недоступен.
    """
    from ..translation.batch import translate_many

    return translate_many(items, _BATCH_CACHE)


def read_dirty_regions(
    region_img: Image.Image,
    state,
    tracked: list[TrackedBlock],
    last_clean: Image.Image | None,
    last_key: str,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
    now: float,
) -> IncrementalResult:
    """Перечитать только то, что изменилось. Ничего не делать - не ошибка."""
    out = IncrementalResult(
        tracked=list(tracked),
        last_key=last_key,
        last_clean=last_clean,
    )
    if not (SETTINGS.incremental and tracked and last_clean is not None):
        return out

    probe_now = probe(region_img)
    # Set before the branches, not inside one of them. "Nothing was
    # scheduled this pass" is the normal case on a screen that is not
    # changing - and the name was bound only in the branch that runs
    # when something was, so the pass below raised UnboundLocalError on
    # its way to reading it. The worker died there, and a dead worker
    # leaves its last set of cards on the desktop with nothing to
    # replace them. That is what a switch between windows looks like
    # from the other side: the old translation stays and the new one
    # never arrives.
    dirty_lines: list[tuple[TrackedBlock, dict]] | None = None
    # KZT_PROBE_LOG=1 explains a slow cycle: which elements the
    # thumbnail diff considered dirty and by how much.
    means: list[str] | None = [] if os.environ.get("KZT_PROBE_LOG") else None
    dirty_ids = dirty_block_ids(probe(last_clean), probe_now, tracked, rw, rh, means=means)
    if means:
        tlog("probe-means " + " ".join(means))

    if dirty_ids:
        dirty_lines = update_dirty_blocks(region_img, tracked, dirty_ids) or []
        # A block inside a re-read area that came back empty has been
        # looked at and found gone, which is the only thing that counts
        # as news here. Anything outside it was not scheduled and has no
        # verdict either way.
        reread_ok = {blk.id for blk, _ in (dirty_lines or [])}
        for blk in tracked:
            if blk.id in dirty_ids and blk.id not in reread_ok:
                blk.missing_passes = 0
        gone_ids, still_tracked = confirm_gone(
            region_img, tracked, reread_ok, time.monotonic()
        )
        if gone_ids:
            gone_set = set(gone_ids)
            for blk in tracked:
                if blk.id in gone_set:
                    tdetail(f"block-gone '{blk.text[:28]}'")
            gone_boxes = [b.box for b in still_tracked if b.id in gone_set]
            # also boxes from blocks that were removed from still_tracked
            gone_boxes = [b.box for b in tracked if b.id in gone_set]
            out.tracked = [b for b in still_tracked if b.id not in gone_set]
            out.removed_cards = state.drop_cards_at(gone_boxes)
            tlog(
                f"blocks-gone {len(gone_ids)} cards={out.removed_cards} "
                f"remaining={len(out.tracked)}"
            )
    else:
        # Nothing was scheduled this pass, which is also what a still
        # screen looks like, so there is no verdict here. But a block
        # whose box is now flat fill has nothing left under its card, and
        # that is checkable without any OCR: the text went, and nothing
        # else would have noticed.
        flat = [b for b in tracked if not _box_has_ink(region_img, b.box)]
        if flat:
            now_flat = time.monotonic()
            for blk in flat:
                if blk.missing_since is None:
                    blk.missing_since = now_flat
                blk.missing_passes += 1
            overdue = [
                b for b in flat if b.missing_passes >= gone_grace(b)
            ]
            if overdue:
                boxes = [b.box for b in overdue]
                gone = {b.id for b in overdue}
                out.tracked = [b for b in tracked if b.id not in gone]
                out.removed_cards = state.drop_cards_at(boxes)
                tlog(
                    f"blocks-flat {len(overdue)} cards={out.removed_cards} "
                    f"remaining={len(out.tracked)}"
                )
        else:
            for blk in tracked:
                blk.missing_passes = 0
                blk.missing_since = None

    if dirty_lines:
        changed_items: list[dict] = []
        changed_blocks: list[TrackedBlock] = []
        for blk, fresh in dirty_lines:
            script = block_script(str(fresh.get("text", "")))
            if not block_content_changed(
                blk, str(fresh.get("text", "")), script, float(fresh.get("conf", 0.0))
            ):
                continue
            fresh["script"] = script
            changed_blocks.append(
                TrackedBlock(
                    id=blk.id,
                    box=tuple(fresh["box"]),  # type: ignore[arg-type]
                    text=str(fresh["text"]),
                    norm=normalize_for_compare(str(fresh["text"])),
                    script=script,
                    lang=str(fresh.get("lang") or "") or block_lang(str(fresh["text"])),
                    conf=float(fresh.get("conf", 0.0)),
                    engine=str(fresh.get("engine", "rapidocr")),
                    last_seen=time.monotonic(),
                )
            )
            changed_items.append(
                {"text": str(fresh["text"]), "source": source_lang_for(script)}
            )
        if changed_items:
            new_tr = _translate_batch(changed_items)
            kept = [b for b in state.snapshot()[0] if b.get("kind") != "dirty"]
            fresh_cards: list[dict] = []
            for blk, got in zip(changed_blocks, new_tr):
                src_text = str(blk.text)
                # Same gates the full path applies, otherwise a
                # noisy re-read of one box can inject junk.
                if not got or same_line(src_text, got):
                    continue
                if is_garbage_ocr(src_text) or is_overlay_echo_ocr(got):
                    tlog(f"incremental-drop '{src_text[:32]}'")
                    continue
                if is_translation_error(src_text, got):
                    continue
                card = make_block(
                    {
                        "text": src_text,
                        "box": blk.box,
                        "line_height": max(8, blk.box[3] - blk.box[1]),
                        "kind": "dirty",
                    },
                    got,
                    region_img,
                    rx,
                    ry,
                    rw,
                    rh,
                )
                # Two re-read boxes can land on the same element.
                if any(
                    abs(int(card["x"]) - int(old["x"])) <= 4
                    and abs(int(card["y"]) - int(old["y"])) <= 4
                    for old in fresh_cards
                ):
                    continue
                fresh_cards.append(card)
            if fresh_cards:
                merged = _merge_cards_by_position(kept, fresh_cards)
                state.set(merged, (rx, ry, rw, rh), "")
                out.tracked = out.tracked
                out.last_key = "|".join(
                    str(c.get("source") or c.get("text") or "") for c in merged
                )
                out.changed_cards = len(fresh_cards)
                tlog(
                    f"incremental {len(fresh_cards)}/{len(out.tracked)} cards "
                    f"({int((time.monotonic() - now) * 1000)}ms)"
                )
                out.last_clean = region_img
                out.last_clean_check = time.monotonic()
                out.last_dirty = None
                out.handled = True
    return out
