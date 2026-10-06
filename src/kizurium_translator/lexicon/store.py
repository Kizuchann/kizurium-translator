"""Словари из данных, не из исходника.

Формат пачки: `manifest.toml`, точные термины в `terms.tsv`
(`source`, `target`, `scope`, `priority`, `flags`), богатые записи в
`*.jsonl`. При чтении пачки собираются в память и в SQLite-индекс.

Области: `user` > `profile` > `game` > `global`. При равной области
побеждает больший `priority`. Машинный перевод сюда не пишется.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "lexicons"

SCOPE_RANK = {"user": 4, "profile": 3, "game": 2, "global": 1}


@dataclass(frozen=True)
class Term:
    source: str
    target: str
    scope: str
    priority: int
    flags: frozenset[str]
    pack: str


@dataclass(frozen=True)
class Groups:
    ui: frozenset[str]
    game_vocab: frozenset[str]
    title_vocab: frozenset[str]
    proper_names: frozenset[str]
    title_names: frozenset[str]
    stat_abbr: frozenset[str]
    speakers: frozenset[str]


def _pack_id(directory: Path) -> str:
    manifest = directory / "manifest.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return directory.name
    return str(data.get("id") or directory.name)


def _pack_target(directory: Path) -> str | None:
    manifest = directory / "manifest.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return str(data.get("target_language") or "").strip().lower() or None


_PACK_TARGETS: dict[str, str] = {}
_PACK_TARGETS_READ = False


def _read_pack_targets() -> None:
    global _PACK_TARGETS_READ
    if _PACK_TARGETS_READ:
        return
    _PACK_TARGETS_READ = True
    for root in (DATA_ROOT, *_user_dictionary_roots()):
        if not root.is_dir():
            continue
        for manifest in sorted(root.rglob("manifest.toml")):
            target = _pack_target(manifest.parent)
            if target:
                _PACK_TARGETS.setdefault(_pack_id(manifest.parent), target)


def pack_target(pack: str) -> str | None:
    """Язык, на который пакет переводит, или None, если пакет не заявил."""
    _read_pack_targets()
    return _PACK_TARGETS.get(pack)


def speaks_to(pack: str, target_lang: str | None) -> bool:
    """Пакет переводит на запрошенный язык.

    Пакет объявляет в манифесте, на какой язык он переводит. `core/en-ru`
    переводит на русский, и при `target=en` его строки показывать нельзя:
    словарь отвечает верно, но не на том языке, и на экране это читается как
    «перевод сломан». Язык не заявлен - пакет считается языконезависимым,
    иначе молча выпадали бы пользовательские словари без манифеста.
    """
    if not target_lang:
        return True
    want = target_lang.strip().lower().split("-")[0]
    have = pack_target(pack)
    if have is None:
        return True
    return have.split("-")[0] == want


def _read_tsv(path: Path, pack: str) -> list[Term]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[Term] = []
    for i, line in enumerate(lines):
        if not line.strip() or (i == 0 and line.startswith("source\t")):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        source, target, scope, priority, flags = parts[:5]
        try:
            prio = int(priority)
        except ValueError:
            prio = 0
        out.append(
            Term(
                source=source,
                target=target,
                scope=scope or "global",
                priority=prio,
                flags=frozenset(f for f in flags.split(",") if f),
                pack=pack,
            )
        )
    return out


def _read_jsonl(path: Path, pack: str) -> list[Term]:
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[Term] = []
    for line in raw:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        surface = str(rec.get("surface") or "")
        if not surface:
            continue
        out.append(
            Term(
                source=surface,
                target=surface,
                scope="global",
                priority=60,
                flags=frozenset({"speaker"}),
                pack=pack,
            )
        )
    return out


def load_terms(*extra_roots: Path) -> tuple[Term, ...]:
    """Все пачки: из репозитория и из каталогов пользователя."""
    found: list[Term] = []
    roots = [DATA_ROOT, *extra_roots]
    seen: set[tuple[str, str, str, str]] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for manifest in sorted(root.rglob("manifest.toml")):
            directory = manifest.parent
            pack = _pack_id(directory)
            batch = _read_tsv(directory / "terms.tsv", pack)
            for path in sorted(directory.glob("*.jsonl")):
                batch.extend(_read_jsonl(path, pack))
            for term in batch:
                key = (term.source, term.scope, term.pack, ",".join(sorted(term.flags)))
                if key in seen:
                    continue
                seen.add(key)
                found.append(term)
    return tuple(found)


def compile_index(path: Path, terms: tuple[Term, ...] | None = None) -> Path:
    """Собирает `lexicon.sqlite3` для быстрого точного поиска."""
    rows = terms if terms is not None else load_terms()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS terms ("
            "source TEXT NOT NULL, target TEXT NOT NULL, scope TEXT NOT NULL, "
            "priority INTEGER NOT NULL, flags TEXT NOT NULL, pack TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM terms")
        conn.executemany(
            "INSERT INTO terms (source, target, scope, priority, flags, pack) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (t.source, t.target, t.scope, t.priority, ",".join(sorted(t.flags)), t.pack)
                for t in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def groups(terms: tuple[Term, ...] | None = None) -> Groups:
    """The seven vocabularies the translator binds at import time.

    A flat set per flag, not the `Term` rows: every consumer asks "is this
    surface in this group", and keeping the rows here kept every `Term` alive in
    memory for the life of the process for an answer nobody asked.
    """
    rows = terms if terms is not None else load_terms()

    def take(pred) -> frozenset[str]:
        return frozenset(t.source for t in rows if pred(t))

    return Groups(
        ui=take(lambda t: t.flags == frozenset({"not-name"}) and t.scope == "global"),
        game_vocab=take(lambda t: "game-vocab" in t.flags and "title" not in t.flags),
        title_vocab=take(lambda t: "title" in t.flags and "not-name" in t.flags),
        proper_names=take(
            lambda t: "proper-name" in t.flags and "title" not in t.flags and "speaker" not in t.flags
        ),
        title_names=take(lambda t: "proper-name" in t.flags and "title" in t.flags),
        stat_abbr=take(lambda t: "stat" in t.flags),
        speakers=take(lambda t: "speaker" in t.flags),
    )


def _user_dictionary_roots() -> tuple[Path, ...]:
    """Каталог словарей пользователя, если он уже есть. Пустой — не ошибка."""
    try:
        from ..paths import default_paths

        directory = default_paths().dictionaries_dir
    except Exception:  # noqa: BLE001
        return ()
    return (directory,) if directory.is_dir() else ()


def exact(
    term: str,
    *,
    user: dict[str, str] | None = None,
    game_on: bool = False,
    game: str | None = None,
    terms: tuple[Term, ...] | None = None,
    target_lang: str | None = None,
) -> str | None:
    """Точное попадание. Машинный перевод сюда не подмешивается.

    Пустой `target` значит «это не перевод, а пометка» - такое слово
    резолвер не возвращает. Имя, которое надо оставить как есть, хранится
    отдельным множеством, а не как перевод.

    `target_lang` отсекает пакеты, которые переводят на другой язык: при
    `target=en` русский пакет отсюда не выдаётся.
    """
    if user:
        for key, value in user.items():
            if key == term and value:
                return value
    hits: list[Term] = []
    rows = terms if terms is not None else load_terms(*_user_dictionary_roots())
    for item in rows:
        if item.source != term or not item.target or item.target == item.source:
            continue
        if not speaks_to(item.pack, target_lang):
            continue
        if item.scope == "game":
            if game and item.pack != game:
                continue
            if not game and not game_on:
                continue
        elif item.scope == "global":
            pass
        elif item.scope in ("user", "profile"):
            if not game_on and item.scope == "profile":
                continue
        else:
            continue
        hits.append(item)
    if not hits:
        return None
    hits.sort(key=lambda t: (SCOPE_RANK.get(t.scope, 0), t.priority), reverse=True)
    return hits[0].target
