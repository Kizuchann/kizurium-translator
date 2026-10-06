"""Планировщик перевода.

Запрос несёт ревизию кадра. Ответ, который вернулся после смены ревизии,
не применяется: блок уже другой, и старый перевод лёг бы поверх новой сцены.

every asynchronous result carries:

```text
session_id
frame_revision
state_revision
block_id
content_revision
```

and is discarded when any of those no longer match current state.

Онлайн-бэкенд (`gtx`) идёт строго по одному запросу за раз. Локальная пачка
ограничена восемью блоками или 512 исходными токенами — это режет
`translate_many`, а не этот модуль.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

# Приоритеты очереди. Меньше — раньше.
P0_NEW_DIALOGUE = 0
P1_CHANGED_DIALOGUE = 1
P2_NEW_UI = 2
P3_CHANGED_UI = 3
P4_TRANSIENT = 4
P5_BACKGROUND = 5

LOCAL_BATCH_BLOCKS = 8
LOCAL_BATCH_TOKENS = 512


@dataclass(frozen=True)
class ResultIdentity:
    """identity stamped on every async OCR/translation result."""

    session_id: str
    frame_revision: int
    state_revision: int
    block_id: int = 0
    content_revision: int = 0


@dataclass(frozen=True)
class TranslationRequest:
    """Один запрос. Ревизия — то, с чем сверяется ответ."""

    session_id: str
    state_revision: int
    block_id: int
    content_revision: int
    source: str
    source_language: str
    target_language: str
    text: str
    priority: int = P2_NEW_UI
    frame_revision: int = 0

    def identity(self) -> ResultIdentity:
        return ResultIdentity(
            session_id=self.session_id,
            frame_revision=int(self.frame_revision),
            state_revision=int(self.state_revision),
            block_id=int(self.block_id),
            content_revision=int(self.content_revision),
        )


class Scheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._frame_revision = 0
        self._session = "live"
        self._block_content: dict[int, int] = {}
        self._gtx = threading.BoundedSemaphore(1)

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def frame_revision(self) -> int:
        with self._lock:
            return self._frame_revision

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._session

    def bump(self) -> int:
        """Сцена сменилась: ответы, снятые со старой ревизии, больше не годятся."""
        with self._lock:
            self._revision += 1
            self._block_content.clear()
            return self._revision

    def bump_frame(self) -> int:
        """A newer capture arrived; OCR/translate tied to the old frame is stale."""
        with self._lock:
            self._frame_revision += 1
            return self._frame_revision

    def set_session(self, session_id: str) -> None:
        with self._lock:
            self._session = str(session_id or "live")
            self._revision += 1
            self._frame_revision += 1
            self._block_content.clear()

    def note_block_content(self, block_id: int, content_revision: int) -> None:
        with self._lock:
            self._block_content[int(block_id)] = int(content_revision)

    def capture(self) -> int:
        return self.revision

    def capture_identity(
        self,
        *,
        block_id: int = 0,
        content_revision: int | None = None,
    ) -> ResultIdentity:
        with self._lock:
            rev = (
                int(content_revision)
                if content_revision is not None
                else int(self._block_content.get(int(block_id), 0))
            )
            return ResultIdentity(
                session_id=self._session,
                frame_revision=self._frame_revision,
                state_revision=self._revision,
                block_id=int(block_id),
                content_revision=rev,
            )

    def stale(self, captured: int) -> bool:
        return captured != self.revision

    def accept_identity(self, token: ResultIdentity) -> bool:
        """discard when session/frame/state/content no longer match."""
        with self._lock:
            if token.session_id != self._session:
                return False
            # frame_revision 0 means "not stamped" (legacy TranslationRequest).
            if int(token.frame_revision) and int(token.frame_revision) != int(
                self._frame_revision
            ):
                return False
            if int(token.state_revision) != int(self._revision):
                return False
            if int(token.block_id) and int(token.block_id) in self._block_content:
                if int(token.content_revision) != int(
                    self._block_content[int(token.block_id)]
                ):
                    return False
            return True

    def accept(self, request: TranslationRequest) -> bool:
        """Ответ ещё про тот же кадр и ту же ревизию содержимого."""
        return self.accept_identity(request.identity())

    def online(self):
        """Не больше одного одновременного запроса к gtx."""
        return self._gtx


SCHEDULER = Scheduler()


def source_tokens(text: str) -> int:
    """Грубая оценка исходных токенов: слова, минимум одно."""
    parts = [p for p in str(text or "").split() if p]
    return max(1, len(parts))


def pack_local_batch(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Пачки не больше 8 блоков и 512 исходных токенов."""
    batches: list[list[tuple[str, str]]] = []
    buf: list[tuple[str, str]] = []
    tokens = 0
    for key, src in items:
        n = source_tokens(key)
        if buf and (len(buf) >= LOCAL_BATCH_BLOCKS or tokens + n > LOCAL_BATCH_TOKENS):
            batches.append(buf)
            buf, tokens = [], 0
        buf.append((key, src))
        tokens += n
    if buf:
        batches.append(buf)
    return batches
