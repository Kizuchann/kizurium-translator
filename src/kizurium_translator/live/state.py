"""Что сейчас на экране и очередь кадров.

Здесь то, что живёт дольше одного кадра: текущий набор блоков и
буфер кадра. Очередь на один слот - новый кадр вытесняет старый,
потому что цикл кадра быстрее распознавания, а результат по
устаревшему кадру всё равно выбрасывается по версии.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from ..core.models import (  # noqa: F401
    box_center_offset,
    box_iou,
    box_size_ratio,
)
from ..core.text import (  # noqa: F401
    SCRIPT_EN,
    SCRIPT_JA,
    SCRIPT_MIXED,
    block_script,
)


@dataclass(frozen=True)
class VersionedFrame:
    """one capture plus the revision it was stamped with."""

    frame: object
    version: int


class _FrameQueue:
    """Single-slot frame buffer: latest frame wins, stale frames are dropped.

    API:

    ```text
    put(frame) -> VersionedFrame
    take_latest() -> VersionedFrame
    ```

    The live loop produces frames faster than OCR can consume them. Instead of
    queueing every frame, we keep only the latest. The OCR worker compares the
    version it started on with ``version`` after work — if a newer frame arrived,
    the stale result is discarded.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: object | None = None
        self._version = 0
        self._ready = threading.Condition(self._lock)

    def put(self, frame: object) -> VersionedFrame:
        """Store the frame, overwriting any previous one."""
        with self._lock:
            self._frame = frame
            self._version += 1
            self._ready.notify_all()
            return VersionedFrame(frame, self._version)

    def take_latest(self) -> VersionedFrame:
        """Wait until a frame is available and return it with its version."""
        with self._ready:
            while self._frame is None:
                self._ready.wait()
            return VersionedFrame(self._frame, self._version)

    @property
    def version(self) -> int:
        with self._lock:
            return self._version


class State:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.blocks: list[dict] = []
        self.region: tuple[int, int, int, int] | None = None
        self.status = ""
        self.hidden = False
        self.rev = 0
        self.paint_empty = threading.Event()
        self.paint_empty.set()

    def _bump(self) -> None:
        self.rev += 1

    @staticmethod
    def _render_key(
        blocks: list[dict],
        region: tuple[int, int, int, int] | None,
        status: str,
        hidden: bool,
    ) -> tuple:
        """identity of what DrawingArea would paint."""
        cards = tuple(
            (
                str(b.get("text") or ""),
                str(b.get("source") or ""),
                int(b.get("x", 0) or 0),
                int(b.get("y", 0) or 0),
                int(b.get("src_w", 0) or 0),
                int(b.get("src_h", 0) or 0),
                str(b.get("kind") or ""),
            )
            for b in (blocks or [])
        )
        return (hidden, region, str(status or ""), cards)

    def set(self, blocks: list[dict], region: tuple[int, int, int, int], status: str = "") -> bool:
        """Publish cards for redraw. Returns False when nothing visible changed.

        skip revision bump (and therefore ``queue_draw``) when the
        paint identity is unchanged.
        """
        with self._lock:
            key = self._render_key(blocks, region, status, False)
            if (
                not self.hidden
                and key == getattr(self, "_render_key_cache", None)
                and list(self.blocks) is not None
                and self.region == region
                and self.status == status
            ):
                # Same paint identity — do not bump.
                if key == self._render_key(self.blocks, self.region, self.status, False):
                    return False
            self.blocks = blocks
            self.region = region
            self.status = status
            self.hidden = False
            self._render_key_cache = key
            self.paint_empty.clear()
            self._bump()
            return True

    def clear(
        self,
        region: tuple[int, int, int, int] | None = None,
        *,
        reason: str = "empty_scene",
    ) -> bool:
        """Wipe every card. Only for allowed global-clear reasons.

        Returns True when the wipe happened.
        """
        from .clear_policy import may_global_clear

        if not may_global_clear(reason):
            from ..core.text import tlog

            tlog(f"clear-denied reason={reason}")
            return False
        with self._lock:
            self.blocks = []
            if region is not None:
                self.region = region
            self.status = ""
            self.hidden = False
            self.paint_empty.clear()
            self._bump()
            from ..translation.scheduler import SCHEDULER

            SCHEDULER.bump()
        return True

    def hide(self) -> None:
        with self._lock:
            if not self.hidden:
                self.hidden = True
                self.paint_empty.clear()
                self._bump()

    def show(self) -> None:
        with self._lock:
            if self.hidden:
                self.hidden = False
                self.paint_empty.clear()
                self._bump()

    def mark_painted(self) -> None:
        with self._lock:
            if self.hidden:
                self.paint_empty.set()

    def wait_hidden_paint(self, timeout: float = 0.35) -> None:
        self.paint_empty.wait(timeout)

    def version(self) -> int:
        with self._lock:
            return self.rev

    def snapshot(self) -> tuple[list[dict], tuple[int, int, int, int] | None, str]:
        with self._lock:
            if self.hidden:
                return [], self.region, self.status
            return list(self.blocks), self.region, self.status

    def peek_blocks(self) -> list[dict]:
        """Карточки даже если hidden — для инкрементального merge без full wipe."""
        with self._lock:
            return list(self.blocks)

    def drop_cards_at(self, boxes: list[tuple[int, int, int, int]]) -> int:
        """Take down the cards whose boxes no longer hold anything.

        Matched by position because a card carries no tracker identity, and the
        card is drawn at the block's own coordinates. Anything that does not
        match a given box stays, so a mis-match removes one card rather than the
        screen.
        """
        if not boxes:
            return 0
        with self._lock:
            before = len(self.blocks)

            def same(a: dict, b: tuple[int, int, int, int]) -> bool:
                try:
                    x, y = int(a.get("x", 0)), int(a.get("y", 0))
                    w = int(a.get("src_w", a.get("w", 0)))
                    h = int(a.get("src_h", a.get("h", 0)))
                except (TypeError, ValueError):
                    return False
                return abs(x - b[0]) <= 6 and abs(y - b[1]) <= 6 and abs(w - b[2]) <= 10 and abs(h - b[3]) <= 10

            kept = [c for c in self.blocks if not any(same(c, b) for b in boxes)]
            self.blocks = kept
            self.paint_empty.clear()
            if len(kept) != before:
                self._bump()
            return before - len(kept)

    def replace_keeping_hud(self, region: tuple[int, int, int, int] | None = None) -> None:
        """Снять только dialogue/body, HUD оставить — без полного clear."""
        with self._lock:
            before = len(self.blocks)
            self.blocks = drop_overlay_kinds(self.blocks)
            if region is not None:
                self.region = region
            self.hidden = False
            self.paint_empty.clear()
            self._bump()
            if before != len(self.blocks):
                # tlog not always imported order-safe; caller logs
                pass


@dataclass
class CycleCounters:
    """Состояние, которое живёт между кадрами внутри одного цикла.

    Это не содержимое экрана - это счётчики, которые решают, как часто можно
    дёргать OCR: когда последний раз смотрели грязный кадр, сколько раз
    подряд эхо плашки совпало, не пора ли уже признать смену сцены.

    Раньше всё это лежало полями на самой функции `worker`
    (`worker._last_dirty_probe = now`). Поле на функции - это глобальное
    изменяемое состояние, просто невидимое линтеру: оно переживало перезапуск
    сессии внутри процесса, и первый кадр новой сессии видел счётчики
    прошлой. Значения по умолчанию совпадают с прежними
    `getattr(worker, "_x", default)`, поэтому поведение не меняется.
    """

    # Когда последний раз смотрели «грязный» кадр.
    last_dirty_probe: float = 0.0
    # Сколько кадров подряд плашка эхом совпала с содержимым кадра.
    dirty_echo: int = 0
    # Сколько кадров подряд полоса под оверлеем вышла пустой.
    dirty_empty: int = 0
    # Когда последний раз делали чистый OCR из-за эха.
    last_echo_clean: float = 0.0
    # Сколько кадров подряд мягко-пусто (текст ушёл, но не подтверждено).
    soft_empty: int = 0
    # Режим предыдущего кадра: eng-ui / jpn-game / …
    prev_mode: str | None = None
    # Сколько строк было в прошлом кадре - нужно, чтобы заметить пропажу UI.
    last_line_n: int = 0
    # Кадр, на котором уже решили, что сцена сменилась.
    ui_scene_change: bool = False
    # Частичное чтение не дало полного набора - нужно повторить.
    force_retry: bool = False
    # Сколько кадров подряд набор оставался неполным.
    partial_streak: int = 0


def normalize_ocr_key(key: str) -> str:
    return re.sub(r"\s+", " ", key).strip().casefold()


def drop_overlay_kinds(
    blocks: list[dict], kinds: set[str] | None = None
) -> list[dict]:
    """Убрать только реплики/бабблы, HUD оставить."""
    kinds = kinds or {"dialogue", "dialogue-line", "body"}
    return [b for b in blocks if str(b.get("kind", "") or "") not in kinds]


SHORT_TOKEN_MAX = 14


SHORT_TEXT_MAX = 8


MATCH_IOU = 0.15  # matching gate (was 0.30)


MATCH_CENTER_HEIGHT = 2.5  # center distance ≤ 2.5 × average box height


MATCH_SIZE_RATIO = 0.55


MATCH_LANG_BONUS = 0.05  # legacy alias; composite uses 0.10 weight


# composite weights.
MATCH_W_IOU = 0.45
MATCH_W_CENTER = 0.25
MATCH_W_SIZE = 0.15
MATCH_W_LANG = 0.10
MATCH_W_TEXT = 0.05


DIGITS_RE = re.compile(r"^[\d\s.,:%/+\-x×№#]+$")


LATIN_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-]*$")


def source_lang_for(script: str) -> str:
    """Language code to send to the translation backend."""
    if script in (SCRIPT_JA, SCRIPT_MIXED):
        return "ja" if script == SCRIPT_JA else "auto"
    if script == SCRIPT_EN:
        return "en"
    return "auto"


def block_source_lang(block: dict, text: str) -> str:
    """The language to send for one part of one block.

    The block's own ``lang`` when the OCR pass set it, because that is the read
    of the pixels rather than a second guess made after the text was split,
    re-joined and possibly unglued. The text is the fallback, so a block from a
    path that never annotated still travels with a language.
    """
    own = str(block.get("lang") or "").strip()
    if own:
        return own
    return source_lang_for(block_script(text))


@dataclass
class TrackedBlock:
    """One text element and everything known about it across frames."""

    id: int
    box: tuple[int, int, int, int]
    text: str
    norm: str
    script: str
    # The language this block is sent as. Separate from script because the two
    # answer different questions: script is what the characters are, language is
    # what the backend is told, and "auto" is a real answer that a script alone
    # cannot express.
    lang: str
    conf: float
    engine: str
    translation: str = ""
    last_seen: float = 0.0
    params: dict = field(default_factory=dict)

    # Consecutive passes that found nothing where this block was. Text that
    # vanishes leaves no re-read to schedule, so nothing noticed its absence and
    # the card stayed on screen indefinitely, over whatever replaced it.
    missing_passes: int = 0
    # Wall clock when the block first went TEMPORARILY_MISSING
    missing_since: float | None = None
    # Consecutive passes where this block was found and did not change. A block
    # that keeps being read back the same is not re-translated.
    stable_passes: int = 0
    # increments when the same block_id gets new text.
    content_revision: int = 0
    # dynamics signals across frames.
    first_seen: float = 0.0
    seen_passes: int = 0
    change_count: int = 0
    # Sum of center moves (px) across matched frames; low = position-stable.
    position_drift: float = 0.0

    @property
    def fingerprint(self) -> str:
        return f"{self.norm}|{self.script}|{self.lang}"

    @property
    def missing(self) -> bool:
        return self.missing_passes > 0 or self.missing_since is not None

    def lifespan_s(self, now: float) -> float:
        origin = float(self.first_seen or self.last_seen or now)
        return max(0.0, float(now) - origin)

    def change_frequency(self) -> float:
        seen = max(1, int(self.seen_passes))
        return float(self.change_count) / float(seen)

    def position_stability(self) -> float:
        """1.0 = glued in place; approaches 0 as cumulative drift grows."""
        # ~2 line-heights of drift over life → near zero.
        return max(0.0, 1.0 - float(self.position_drift) / 80.0)


# The three geometry helpers that used to be defined here were one-line
# pass-throughs to the `core.models` functions, kept only so `live.tracking` could
# import them from this module. A name that looks like the original while being a
# copy of it is the kind of thing that later gets edited in one place and read in
# the other, so callers use the `core.models` names directly.


def _center_distance_px(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax = (a[0] + a[2]) / 2.0
    ay = (a[1] + a[3]) / 2.0
    bx = (b[0] + b[2]) / 2.0
    by = (b[1] + b[3]) / 2.0
    return float(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5)


def _avg_box_height(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    return max(1.0, ((a[3] - a[1]) + (b[3] - b[1])) / 2.0)


def _center_score(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """1 when centres coincide, 0 at/beyond the center distance gate."""
    lim = MATCH_CENTER_HEIGHT * _avg_box_height(a, b)
    return max(0.0, 1.0 - _center_distance_px(a, b) / lim)


def same_visual_block(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """candidate gate: IoU ≥ 0.15 OR center ≤ 2.5× avg height."""
    if box_iou(a, b) >= MATCH_IOU:
        return True
    return _center_distance_px(a, b) <= MATCH_CENTER_HEIGHT * _avg_box_height(a, b)


def block_match_score(
    prev_box: tuple[int, int, int, int],
    box: tuple[int, int, int, int],
    *,
    prev_lang: str = "",
    lang: str = "",
    prev_text: str = "",
    text: str = "",
) -> float:
    """composite score (deterministic, no scipy)."""
    iou = box_iou(prev_box, box)
    size = box_size_ratio(prev_box, box)
    center = _center_score(prev_box, box)
    lang_sim = 1.0 if (lang and prev_lang and lang == prev_lang) else 0.0
    text_sim = 0.0
    if prev_text or text:
        try:
            from difflib import SequenceMatcher

            text_sim = SequenceMatcher(
                None,
                normalize_ocr_key(prev_text),
                normalize_ocr_key(text),
            ).ratio()
        except Exception:  # noqa: BLE001
            text_sim = 1.0 if normalize_ocr_key(prev_text) == normalize_ocr_key(text) else 0.0
    return (
        MATCH_W_IOU * iou
        + MATCH_W_CENTER * center
        + MATCH_W_SIZE * size
        + MATCH_W_LANG * lang_sim
        + MATCH_W_TEXT * text_sim
    )