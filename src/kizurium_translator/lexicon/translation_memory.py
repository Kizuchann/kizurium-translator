"""Automatic translation memory

Every successful machine translation is remembered here. This is **not** the
authoritative glossary and **not** the hot SQLite translate cache:

- cache  → speed (may be pruned, versioned with glossary hash)
- memory → curation surface (survives, can be promoted by the user)
- glossary → only what the user (or a pack) explicitly approved

Promotion writes a user pack under ``dictionaries/``, never mutates bundled
lexicons.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .pack_id import safe_pack_dir


@dataclass(frozen=True)
class MemoryHit:
    source_text: str
    translated_text: str
    source_lang: str
    target_lang: str
    backend_id: str
    created_at: float
    use_count: int


class TranslationMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory ("
                "source_text TEXT NOT NULL,"
                "translated_text TEXT NOT NULL,"
                "source_lang TEXT NOT NULL,"
                "target_lang TEXT NOT NULL,"
                "backend_id TEXT NOT NULL,"
                "created_at REAL NOT NULL,"
                "last_used_at REAL NOT NULL,"
                "use_count INTEGER NOT NULL DEFAULT 1,"
                "PRIMARY KEY (source_lang, target_lang, source_text))"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def remember(
        self,
        source_text: str,
        translated_text: str,
        *,
        source_lang: str,
        target_lang: str,
        backend_id: str = "gtx",
    ) -> None:
        src = (source_text or "").strip()
        dst = (translated_text or "").strip()
        if not src or not dst or src == dst:
            return
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO memory ("
                "source_text, translated_text, source_lang, target_lang, "
                "backend_id, created_at, last_used_at, use_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(source_lang, target_lang, source_text) DO UPDATE SET "
                "translated_text=excluded.translated_text,"
                "backend_id=excluded.backend_id,"
                "last_used_at=excluded.last_used_at,"
                "use_count=memory.use_count + 1",
                (src, dst, source_lang, target_lang, backend_id, now, now),
            )
            conn.commit()

    def lookup(
        self,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> str | None:
        src = (source_text or "").strip()
        if not src:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT translated_text FROM memory "
                "WHERE source_lang=? AND target_lang=? AND source_text=?",
                (source_lang, target_lang, src),
            ).fetchone()
        return row[0] if row else None

    def forget(
        self,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> bool:
        """Remove one remembered row (e.g. local MT junk that failed quality)."""
        src = (source_text or "").strip()
        if not src:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM memory "
                "WHERE source_lang=? AND target_lang=? AND source_text=?",
                (source_lang, target_lang, src),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_entries(
        self,
        *,
        target_lang: str | None = None,
        limit: int = 50,
    ) -> list[MemoryHit]:
        sql = (
            "SELECT source_text, translated_text, source_lang, target_lang, "
            "backend_id, created_at, use_count FROM memory"
        )
        args: list[object] = []
        if target_lang:
            sql += " WHERE target_lang=?"
            args.append(target_lang)
        sql += " ORDER BY last_used_at DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [MemoryHit(*row) for row in rows]

    def promote_to_user_glossary(
        self,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
        pack_id: str = "from-memory",
        dictionaries_dir: Path | None = None,
    ) -> Path | None:
        """Copy one TM entry into a user glossary pack after explicit approval."""
        hit = None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT translated_text FROM memory "
                "WHERE source_lang=? AND target_lang=? AND source_text=?",
                (source_lang, target_lang, source_text.strip()),
            ).fetchone()
            if row:
                hit = row[0]
        if not hit:
            return None
        from ..paths import default_paths
        from .import_external import write_pack

        root = dictionaries_dir or default_paths().dictionaries_dir
        pack_dir = safe_pack_dir(root, pack_id)
        # Merge with existing terms if the pack already exists.
        existing: list[tuple[str, str]] = []
        terms_path = pack_dir / "terms.tsv"
        if terms_path.is_file():
            for i, line in enumerate(terms_path.read_text(encoding="utf-8").splitlines()):
                if not line.strip() or (i == 0 and line.startswith("source\t")):
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    existing.append((parts[0], parts[1]))
        merged = {src: dst for src, dst in existing}
        merged[source_text.strip()] = hit
        write_pack(
            pack_dir,
            pack_id=pack_id,
            exact=list(merged.items()),
            regex=[],
            scope="user",
            source_language=source_lang,
            target_language=target_lang,
            source_note="promoted from translation memory",
        )
        return pack_dir


_MEM: TranslationMemory | None = None
_MEM_LOCK = threading.Lock()


def default_memory(path: Path | None = None) -> TranslationMemory:
    global _MEM
    with _MEM_LOCK:
        if path is not None:
            return TranslationMemory(path)
        if _MEM is None:
            from ..paths import default_paths

            _MEM = TranslationMemory(default_paths().data_dir / "translation-memory.sqlite")
        return _MEM
