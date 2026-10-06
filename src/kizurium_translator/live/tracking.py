"""Сопоставление блоков между кадрами.

Блок на кадре - это прямоугольник в пикселях, и он меняет
размер от кадра к кадру. Сопоставление идёт по тому, что видно
глазом: положение, размер, начертание первых знаков. Без этого
смена сцены выглядит как полная перерисовка, и весь экран
переводится заново на каждом кадре.
"""
from __future__ import annotations

import re

from PIL import Image

from ..core.models import box_center_offset
from ..core.text import (  # noqa: F401
    block_lang,
    block_script,
    tdetail,
    tlog,
)
from ..translate import (  # noqa: F401
    normalize_for_compare,
)
from .change import (  # noqa: F401
    DIRTY_GROUPS_MAX,
    DIRTY_JOIN_PAD,
    _boxes_overlap,
    dirty_block_ids,
    needs_full_reanchor,
    probe,
)
from .reconcile import (  # noqa: F401
    _reread_element,
)
from .runtime import (  # noqa: F401
    SETTINGS,
)
from .state import (  # noqa: F401
    MATCH_LANG_BONUS,
    SHORT_TEXT_MAX,
    TrackedBlock,
    block_match_score,
    normalize_ocr_key,
    same_visual_block,
)


def keys_similar(a: str, b: str, threshold: float = SETTINGS.ocr_key_sim) -> bool:
    if not a and not b:
        return True
    if not a or not b:
        return False
    na, nb = normalize_ocr_key(a), normalize_ocr_key(b)
    if na == nb:
        return True
    # общий префикс / вхождение — OCR иногда откусывает хвост
    # НЕ для multi-line UI (Head|Blinded vs Head|Obtained…) — иначе тосты «невидимы»
    # НЕ для длинных реплик: «Believe me» ⊂ старой фразы → залипание старого RU
    if "|" not in na and "|" not in nb:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(longer) <= 48 and shorter in longer:
            if len(shorter) >= 8 and len(shorter) / max(1, len(longer)) >= 0.72:
                return True
    try:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, na, nb).ratio() >= threshold
    except Exception:
        return False


def _significant_tokens(text: str) -> set[str]:
    """Words and numbers, which is what a changed line is made of.

    Numbers count. "1" -> "2" and "ON" -> "OFF" are the changes a game UI makes
    most often, and a letters-only pattern saw neither of them at all: the token
    sets came out empty and the function reported no change, leaving the old
    translation of the old number on screen.
    """
    # `\w` с UNICODE, а не перечисление букв: перечисление перечисляет то,
    # что перечисляли - латиницу и кириллицу, - и для любого другого алфавита
    # множество выходит пустым. Пустое множество значит «ничего не изменилось»
    # (слова одинаковы), и блок молча не переводится заново: ошибка без
    # сообщения и без следа.
    return set(re.findall(r"\w+", (text or "").casefold(), flags=re.UNICODE))


def significant_word_diff(a: str, b: str) -> bool:
    """True если пропало/появилось хотя бы одно значимое слово (смена реплики).

    Words of one and two letters count. Ignoring them meant "is" -> "15" and
    "to" -> "go" compared as almost the same text, so the old translation stayed
    on screen and the new line was never translated. A dynamic number, a toggle
    and a preposition are exactly the things that change in a game UI, so the
    length of a word is not evidence that it does not matter.
    """
    wa = _significant_tokens(a)
    wb = _significant_tokens(b)
    if not wa and not wb:
        return False
    if not wa or not wb:
        return True
    only_a, only_b = wa - wb, wb - wa
    if not only_a and not only_b:
        return False
    # A one-letter word that is only there because OCR read a character wrongly is
    # noise; "I" -> "1" is the same length and is a real change, so the letter
    # itself is not the signal, the shape of the change is.
    only_a = {w for w in only_a if len(w) > 1 or w.isdigit() or _is_word(w)}
    only_b = {w for w in only_b if len(w) > 1 or w.isdigit() or _is_word(w)}
    if not only_a and not only_b:
        return False
    inter = wa & wb
    # один короткий OCR-хвост при сильном пересечении — шум, не смена
    if len(inter) >= 5 and len(only_a) + len(only_b) == 1:
        lonely = next(iter(only_a or only_b))
        if len(lonely) <= 5:
            return False
    return True


def _is_word(word: str) -> bool:
    """A single letter that is a word rather than an OCR artefact.

    A and I are words. A single stray letter in the middle of a long line is far
    more likely a misread character.
    """
    return word in {"a", "i"}


def dialogue_body_key(key: str) -> str:
    k = normalize_ocr_key(key)
    k = re.sub(r"^[a-z0-9 .'\-]{1,28}:\s*", "", k)
    # JP speaker「名前」/ Name:
    k = re.sub(r"^[^:]{1,20}:\s*", "", k)
    return k


def dialogue_keys_similar(a: str, b: str) -> bool:
    """Субтитры EN/JP: разные реплики ≠ похожи из‑за общего Name:."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    # эхо сравниваем снаружи — здесь не решаем «смена»
    ba, bb = dialogue_body_key(a), dialogue_body_key(b)
    if not ba or not bb:
        return keys_similar(a, b, 0.93)
    if ba[:18] != bb[:18]:
        try:
            from difflib import SequenceMatcher

            if SequenceMatcher(None, ba, bb).ratio() < 0.78:
                return False
        except Exception:
            return False
    return keys_similar(ba, bb, 0.90)


def merge_dirty_regions(blocks: list[TrackedBlock]) -> list[list[TrackedBlock]]:
    """Group neighbouring changed elements into one region each.

    Five elements that changed together are one changed area, not five. Cropping
    around each of them separately costs five OCR calls where one wide crop of
    the same pixels costs one, and the old limit gave up and ran a full pass the
    moment more than four elements moved - which is exactly what a reflowing
    menu or a row of counters does.
    """
    if not blocks:
        return []
    # A generous join, not the reading pad. Two labels ten pixels apart are
    # neighbours in a row of counters, and re-reading them as one region is the
    # whole point; a row of them is one changed area, not five. The pad used for
    # deciding whether two boxes are the same element is the wrong instrument
    # here, because a gap that small means the same thing to a human.
    groups: list[list[TrackedBlock]] = []
    for blk in sorted(blocks, key=lambda b: (b.box[1], b.box[0])):
        for group in groups:
            if any(_boxes_overlap(blk.box, other.box, pad=DIRTY_JOIN_PAD) for other in group):
                group.append(blk)
                break
        else:
            groups.append([blk])
    return groups


def update_dirty_blocks(
    region_img,
    previous: list[TrackedBlock],
    dirty_ids: list[int],
) -> list[tuple[TrackedBlock, dict]] | None:
    """Re-read only the elements inside the changed area.

    Returns ``[(block, fresh_line),...]`` or ``None`` when the situation needs
    a full pass: a large dirty area, no previous blocks, or nothing the tracker
    can attribute to the change.
    """
    if not previous or not dirty_ids:
        return None
    hits = [b for b in previous if b.id in dirty_ids]
    if not hits:
        return None

    groups = merge_dirty_regions(hits)
    # Crop budget (DIRTY_GROUPS_MAX) is stricter than the re-anchor hard
    # re-anchor threshold; either one abandons the incremental path.
    reanchor, reason = needs_full_reanchor(
        tracked_count=len(previous),
        dirty_region_count=len(groups),
    )
    if len(groups) > DIRTY_GROUPS_MAX or (reanchor and reason == "many_dirty"):
        # Neighbouring elements in many separate places: the changed area is no
        # longer a few boxes, and a full pass is cheaper than cropping around
        # all of them.
        tlog(f"dirty-full groups={len(groups)} blocks={len(hits)} reason={reason or 'crop'}")
        return None

    out: list[tuple[TrackedBlock, dict]] = []
    for group in groups:
        for blk in group:
            text = _reread_element(region_img, blk)
            if not text:
                # One unreadable element in a merged region is not a reason to
                # abandon the rest: the neighbours were read fine and the frame
                # is still mostly the one we have. Falling back to a full pass
                # here is what made a single blink cost the whole screen.
                tdetail(f"dirty-unreadable '{blk.text[:20]}'")
                continue
            out.append(
                (
                    blk,
                    {
                        "text": text,
                        "box": blk.box,
                        "line_height": max(8, blk.box[3] - blk.box[1]),
                        "conf": 90.0,
                        "engine": "rapidocr",
                    },
                )
            )
    if not out:
        return None
    tlog(
        f"dirty-reocr {len(out)}/{len(previous)} blocks "
        f"groups={len(groups)} {[b.text[:14] for b, _ in out]}"
    )
    return out


OCR_FIELDS = ("text", "box", "conf", "engine", "lang")


def char_fingerprint(text: str) -> tuple[str, ...]:
    """Every meaningful symbol of a text, as a multiset.

    The character-level identity of a block, for text too short for a similarity
    threshold to be meaningful. Whitespace is dropped and case is kept: case is
    a real difference ("ON" -> "on" is a state change) while spacing is not, and
    a sorted tuple makes the comparison independent of order so a reflow is not
    mistaken for an edit.
    """
    return tuple(sorted(re.sub(r"\s+", "", str(text or ""))))


def punctuation_skeleton(text: str) -> str:
    """Non-letter/digit marks only — compared apart from words."""
    return re.sub(r"[\w\s]+", "", str(text or ""), flags=re.UNICODE)


def block_content_changed(old: TrackedBlock, text: str, script: str, conf: float) -> bool:
    """Did this element's content change?

    This is the only question the worker asks about individual text. Short text
    is compared character by character: with a global similarity threshold, "is"
    -> "15" or "to" -> "go" can be judged "the same" and the stale translation
    stays on screen forever.

    The scene-wide key is not consulted here on purpose. It and this function
    could disagree - one says a block changed, the other says the frame key is
    the same - and when they did, the frame key won and the new block was never
    re-read at all. The block knows its own previous text; the scene key does
    not know which element is being asked about.
    """
    new_norm = normalize_for_compare(text)
    if new_norm == old.norm:
        # Same words can still change punctuation (! → ?); keep the id, bump rev.
        if punctuation_skeleton(old.text) != punctuation_skeleton(text):
            return True
        return False
    if old.script != script:
        return True
    # One meaningful symbol is enough - but only for text short enough that a
    # threshold cannot be trusted with it. "is" and "15" share nothing but a
    # length, and any threshold that forgives a misread character also forgives
    # a toggle. Applied to a whole paragraph it would instead re-translate it on
    # every frame a pixel moved, which is why the rule is scoped rather than
    # general: above the short threshold, similarity is still the right
    # instrument, because there a wobble really is a wobble.
    if len(old.norm) <= SHORT_TEXT_MAX or len(new_norm) <= SHORT_TEXT_MAX:
        if char_fingerprint(old.norm) != char_fingerprint(new_norm):
            return True
        return True
    # Longer text: tolerate OCR jitter, but low confidence means trust the diff.
    if conf < 55:
        return True
    return not keys_similar(old.norm, new_norm)


def any_block_text_changed(
    frame: Image.Image,
    previous_probe: Image.Image | None,
    blocks: list[TrackedBlock],
    rw: int,
    rh: int,
) -> bool:
    """Whether any single tracked element's own text differs from what it had.

    Cheap by design: this runs on the dirty frame, under the overlay, precisely
    so that deciding "is anything actually different here" does not require
    hiding the overlay and running a full OCR. The thumbnail diff says which
    boxes moved, and only those are re-read and compared against the text that
    box had.

    This is what makes the per-block tracker the authority. A scene key cannot
    answer it - two elements swapping their text leave the scene identical - and
    when the two disagreed the scene won, so the element whose text had changed
    was never re-read and kept showing its old translation.
    """
    if previous_probe is None or not blocks:
        return False
    try:
        dirty_ids = dirty_block_ids(
            previous_probe, probe(frame), blocks, rw, rh
        )
    except Exception:
        return False
    if not dirty_ids:
        return False
    try:
        re_read = update_dirty_blocks(frame, blocks, dirty_ids)
    except Exception:
        # A failed re-read is not evidence of a change, and guessing here would
        # force a full capture on every single cycle.
        return False
    if not re_read:
        # None or empty means the tracker could not attribute the change, which
        # is the caller's cue to do a full pass rather than to skip.
        return True
    for old, fresh in re_read:
        text = str(fresh.get("text", ""))
        if block_content_changed(
            old, text, block_script(text), float(fresh.get("conf", 0.0) or 0.0)
        ):
            return True
    return False


def changed_matched_blocks(pairs: list[tuple]) -> list[TrackedBlock]:
    """The matched elements whose own text changed since the last frame.

    ``track_blocks`` returns ``(old_block, new_block)`` for every element it
    could match one-to-one, and this asks the per-block question over that
    list. The
    scene key cannot answer it: the key is every line joined with a separator,
    so a counter going from "14" to "15" in a screen of forty menu labels
    changes it by a rounding error, reads as the same screen, and leaves the
    old translation under the new number. Elements with no match are left out,
    because a new element is a question about the scene rather than about one
    element, and the scene key is the right instrument for that.

    The comparison is the tracker's, not the key's: short text by character
    fingerprint, longer text by similarity with confidence taken into account.
    """
    out: list[TrackedBlock] = []
    for pair in pairs or []:
        old = pair[0]
        # The second element of the pair is the block built from this frame's
        # line, and it carries the confidence the recogniser reported. Asking
        # about "old" against its own normalised text would be asking whether it
        # differs from itself.
        fresh = pair[1] if len(pair) > 1 else old
        text = str(getattr(fresh, "text", "") or "")
        if not text:
            continue
        if block_content_changed(old, text, getattr(fresh, "script", ""), getattr(fresh, "conf", 0.0)):
            out.append(old)
    return out


def scene_changed(scene_key: str, last_scene_key: str) -> bool:
    """Whether the scene as a whole is different.

    For the things a single block cannot answer: a new screen, a finished line,
    whether anything is on screen at all. It is not the answer to "did this
    element's text change", because it cannot see individual elements - two
    elements can swap their text and leave the scene key identical.
    """
    return not dialogue_keys_similar(scene_key, last_scene_key)


def make_block_from_line(line: dict, index: int, now: float) -> TrackedBlock:
    text = str(line.get("text", ""))
    return TrackedBlock(
        id=index,
        box=tuple(int(v) for v in line.get("box", (0, 0, 0, 0))),  # type: ignore[arg-type]
        text=text,
        norm=normalize_for_compare(text),
        script=block_script(text),
        lang=str(line.get("lang") or "") or block_lang(text),
        conf=float(line.get("conf", 0.0) or 0.0),
        engine=str(line.get("engine", "rapidocr")),
        last_seen=now,
        first_seen=now,
        seen_passes=1,
    )


def tracking_match_rate(
    previous_count: int,
    pair_count: int,
    line_count: int,
) -> float:
    """Fraction of elements that matched across frames

    Denominator is the larger of the two sides so a scene that gained or lost
    many lines cannot look healthy just because the smaller side matched.
    """
    denom = max(int(previous_count), int(line_count), 1)
    return float(pair_count) / float(denom)


def track_blocks(
    previous: list[TrackedBlock],
    lines: list[dict],
    now: float,
    next_id: int,
) -> tuple[list[TrackedBlock], list[tuple[TrackedBlock, TrackedBlock]], int]:
    """Match new OCR lines against the previous frame's blocks.

    Returns the current block set, the pairs that describe the same element, and
    the next free id.
    """
    # One previous block can describe at most one new line, and one new line at
    # most one previous block. Matching greedily in reading order let a single
    # wide previous box claim several new lines, which is what happens on a
    # paragraph that reflows or a menu whose items move: the first line kept the
    # block id and its translation, and the rest were told they were new.
    candidates: list[tuple[float, int, int, TrackedBlock, tuple[int, ...]]] = []
    for line_index, line in enumerate(lines):
        box = tuple(int(v) for v in line.get("box", (0, 0, 0, 0)))  # type: ignore[assignment]
        for prev_index, prev in enumerate(previous):
            if not same_visual_block(prev.box, box):  # type: ignore[arg-type]
                continue
            lang = str(line.get("lang") or "") or block_lang(str(line.get("text", "")))
            score = block_match_score(
                prev.box,
                box,  # type: ignore[arg-type]
                prev_lang=prev.lang,
                lang=lang,
                prev_text=prev.text,
                text=str(line.get("text", "")),
            )
            candidates.append((score, prev_index, line_index, prev, box))

    # Best pairs first, and each block and each line is used at most once.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    matched: dict[int, TrackedBlock] = {}
    taken_prev: set[int] = set()
    taken_line: set[int] = set()
    for _score, prev_index, line_index, prev, _box in candidates:
        if prev_index in taken_prev or line_index in taken_line:
            continue
        taken_prev.add(prev_index)
        taken_line.add(line_index)
        matched[line_index] = prev

    current: list[TrackedBlock] = []
    pairs: list[tuple[TrackedBlock, TrackedBlock]] = []
    for line_index, line in enumerate(lines):
        block = make_block_from_line(line, next_id, now)
        prev = matched.get(line_index)
        if prev is not None:
            block.id = prev.id
            block.content_revision = int(getattr(prev, "content_revision", 0) or 0)
            block.first_seen = float(getattr(prev, "first_seen", 0.0) or prev.last_seen or now)
            block.seen_passes = int(getattr(prev, "seen_passes", 0) or 0) + 1
            block.change_count = int(getattr(prev, "change_count", 0) or 0)
            block.position_drift = float(getattr(prev, "position_drift", 0.0) or 0.0)
            block.position_drift += box_center_offset(prev.box, block.box)
            if block_content_changed(prev, block.text, block.script, block.conf):
                block.content_revision += 1
                block.change_count += 1
                block.stable_passes = 0
            else:
                block.stable_passes = int(getattr(prev, "stable_passes", 0) or 0) + 1
            pairs.append((prev, block))
        else:
            next_id += 1
        current.append(block)
    return current, pairs, next_id
