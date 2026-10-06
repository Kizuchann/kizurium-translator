"""Кэш переводов на SQLite.

JSON-файл не был LRU: выкидывалась первая четверть словаря в порядке
вставки, и ключ не знал ни версии словаря, ни бэкенда. Здесь строка
живёт, пока её спрашивают, и тот же текст для другой пары языков —
другая строка.

Соединение одно на поток, журнал WAL, запись короткой транзакцией.
Память остаётся рабочей копией: `forget` помечает её грязной и не
попадает на диск, пока не будет `flush`. Так удаление не возвращается
при следующем запуске и не пишется, если удалять было нечего.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    cache_key TEXT PRIMARY KEY,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    glossary_version TEXT NOT NULL,
    dictionary_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    source_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0
)
"""


def _split_key(key: str) -> tuple[str, str, str]:
    parts = str(key).split("\x1f", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return "", "", str(key)


class SqliteCache:
    """Тот же контракт, что был у JSON-кэша: get/put/forget/flush."""

    def __init__(
        self,
        path: Path | None,
        max_entries: int,
        *,
        backend_id: str = "gtx",
        glossary_version: str = "",
        dictionary_version: str = "",
        model_version: str = "none",
    ) -> None:
        self.path = Path(path) if path else None
        self.max_entries = max(1, int(max_entries))
        self.backend_id = backend_id
        self.glossary_version = glossary_version
        self.dictionary_version = dictionary_version
        self.model_version = model_version
        self._data: OrderedDict[str, str] = OrderedDict()
        self._meta: dict[str, dict[str, str]] = {}
        self._dirty = False
        self._last_flush = 0.0
        self._loaded = False
        self._lock = threading.RLock()
        self._local = threading.local()

    def _connect(self) -> sqlite3.Connection | None:
        if self.path is None:
            return None
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        try:
            conn = sqlite3.connect(
                self.path,
                timeout=1.0,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=1000")
            conn.execute(_SCHEMA)
        except sqlite3.Error:
            return None
        self._local.conn = conn
        return conn

    def load(self) -> OrderedDict[str, str]:
        with self._lock:
            if self._loaded:
                return self._data
            self._loaded = True
            self._data = OrderedDict()
            self._meta = {}
            conn = self._connect()
            if conn is None:
                return self._data
            try:
                rows = conn.execute(
                    "SELECT cache_key, translated_text, source_lang, target_lang, "
                    "backend_id, glossary_version, dictionary_version, model_version, "
                    "source_text, last_used_at "
                    "FROM translations ORDER BY last_used_at ASC"
                ).fetchall()
            except sqlite3.Error:
                return self._data
            for row in rows:
                key = str(row[0])
                self._data[key] = str(row[1])
                self._meta[key] = {
                    "source_lang": str(row[2]),
                    "target_lang": str(row[3]),
                    "backend_id": str(row[4]),
                    "glossary_version": str(row[5]),
                    "dictionary_version": str(row[6]),
                    "model_version": str(row[7]),
                    "source_text": str(row[8]),
                }
            return self._data

    def get(self, key: str) -> str:
        with self._lock:
            data = self.load()
            if key not in data:
                return ""
            data.move_to_end(key)
            return data[key]

    def get_matching(self, key: str, glossary_version: str) -> str:
        """Пустая версия в строке — запись старого кэша, она ещё годится.

        Другая версия словаря — другая запись: термин поменялся, старый
        перевод больше не ответ.
        """
        with self._lock:
            data = self.load()
            if key not in data:
                return ""
            stored = self._meta.get(key, {}).get("glossary_version", "")
            if stored not in ("", glossary_version):
                return ""
            data.move_to_end(key)
            return data[key]

    def put(
        self,
        key: str,
        value: str,
        *,
        glossary_version: str | None = None,
        backend_id: str | None = None,
    ) -> None:
        with self._lock:
            data = self.load()
            if key not in data and len(data) >= self.max_entries:
                drop = max(1, len(data) - self.max_entries + 1)
                for _ in range(drop):
                    old, _value = data.popitem(last=False)
                    self._meta.pop(old, None)
            data[key] = value
            data.move_to_end(key)
            src, tgt, text = _split_key(key)
            self._meta[key] = {
                "source_lang": src,
                "target_lang": tgt,
                "backend_id": backend_id or self.backend_id,
                "glossary_version": self.glossary_version if glossary_version is None else glossary_version,
                "dictionary_version": self.dictionary_version,
                "model_version": self.model_version,
                "source_text": text,
            }
            self._dirty = True

    def forget(self, key: str) -> bool:
        with self._lock:
            data = self.load()
            if key not in data:
                return False
            del data[key]
            self._meta.pop(key, None)
            self._dirty = True
            return True

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def flush(self, force: bool = False, every_s: float = 20.0) -> None:
        with self._lock:
            if not self._dirty:
                return
            now = time.monotonic()
            if not force and (now - self._last_flush) < every_s:
                return
            self.write()
            self._last_flush = now

    def write(self) -> None:
        with self._lock:
            if self.path is None:
                self._dirty = False
                return
            conn = self._connect()
            if conn is None:
                return
            now = time.time()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM translations")
                for key, value in self._data.items():
                    meta = self._meta.get(key, {})
                    conn.execute(
                        "INSERT INTO translations ("
                        "cache_key, source_lang, target_lang, backend_id, "
                        "glossary_version, dictionary_version, model_version, "
                        "source_text, translated_text, created_at, last_used_at, use_count"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            key,
                            meta.get("source_lang", ""),
                            meta.get("target_lang", ""),
                            meta.get("backend_id", self.backend_id),
                            meta.get("glossary_version", ""),
                            meta.get("dictionary_version", ""),
                            meta.get("model_version", self.model_version),
                            meta.get("source_text", ""),
                            value,
                            now,
                            now,
                            1,
                        ),
                    )
                conn.execute("COMMIT")
            except sqlite3.Error:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                return
            self._dirty = False
