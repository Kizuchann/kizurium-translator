"""Filesystem locations.

Follows the XDG base directory specification. Every location can be overridden
with an environment variable, which is what the tests use to run several
instances side by side.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = "kizurium-translator"
ENV_PREFIX = "KIZURIUM_TRANSLATOR_"


@dataclass(frozen=True)
class Paths:
    config_file: Path
    state_dir: Path
    cache_dir: Path
    translate_cache: Path
    runtime_dir: Path
    lock: Path
    pid: Path
    text_pid: Path
    log: Path
    selector_log: Path
    selector_request: Path
    selector_lock: Path
    selector_geometry: Path
    # Каталоги словарей. Пользовательские данные отдельно от кэша.
    data_dir: Path = field(default_factory=Path)
    dictionaries_dir: Path = field(default_factory=Path)
    models_dir: Path = field(default_factory=Path)

    def ensure(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dictionaries_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        retire_legacy_translation_caches(self.cache_dir)
        adopt_legacy_home_translate_cache(self.translate_cache)
        # 0700: everything in here is another user's business otherwise. The mode
        # is set on the directory itself, not just on whatever mkdir defaulted to.
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.runtime_dir.chmod(0o700)
        except OSError:
            # A filesystem that does not support it is not a reason to refuse to run.
            pass


def _env(name: str) -> str | None:
    return os.environ.get(ENV_PREFIX + name)


def _xdg(var: str, fallback: str) -> Path:
    """One XDG base directory, with the spec's fallback.

    XDG_STATE_HOME has no fallback in the specification, so it defaults to
    ~/.local/state rather than to ~/.local/share.
    """
    value = os.environ.get(var, "").strip()
    return Path(value) if value else (Path.home() / fallback)


def retire_legacy_translation_caches(cache_dir: Path) -> list[Path]:
    """Удаляет JSON-кэш переводов.

    Хранение теперь SQLite. Старый файл и его запасная копия не читаются и
    не должны лежать рядом: иначе кажется, что перевод всё ещё берётся из
    них. Логи и конфиг это не трогает.
    """
    removed: list[Path] = []
    if not cache_dir.is_dir():
        return removed
    names = (
        "translate-cache.json",
        "translate-cache.json.bak",
        "translate-cache.json.tmp",
    )
    for name in names:
        path = cache_dir / name
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path)
    return removed


def adopt_legacy_home_translate_cache(translate_cache: Path) -> bool:
    """Pull translations left under ``~/kizurium-translator/`` into XDG cache.

    An older fontconfig helper rewrote ``XDG_CACHE_HOME`` to ``$HOME``, so the
    live SQLite grew at ``~/kizurium-translator/translate-cache.sqlite`` instead
    of under ``~/.cache``. After that rewrite was fixed the process looked at
    an almost-empty proper cache and dropped dialogue while GTX was rate-limited.
    Merge the legacy rows in once, then remove the misplaced file.
    """
    legacy = Path.home() / APP_DIR / "translate-cache.sqlite"
    if not legacy.is_file():
        return False
    try:
        if legacy.resolve() == translate_cache.resolve():
            return False
    except OSError:
        return False
    translate_cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        import sqlite3

        if not translate_cache.is_file():
            # Nothing at the proper path yet: move the whole file over.
            legacy.replace(translate_cache)
            _remove_empty_legacy_home_dir(legacy.parent)
            return True
        src = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
        dst = sqlite3.connect(translate_cache)
        try:
            src_tables = {
                name
                for (name,) in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "translations" not in src_tables:
                return False
            dst.execute("BEGIN")
            # Create the destination schema if the new cache is still empty.
            dst.execute(
                "CREATE TABLE IF NOT EXISTS translations ("
                "cache_key TEXT PRIMARY KEY,"
                "source_lang TEXT NOT NULL,"
                "target_lang TEXT NOT NULL,"
                "backend_id TEXT NOT NULL,"
                "glossary_version TEXT NOT NULL,"
                "dictionary_version TEXT NOT NULL,"
                "model_version TEXT NOT NULL,"
                "source_text TEXT NOT NULL,"
                "translated_text TEXT NOT NULL,"
                "created_at REAL NOT NULL,"
                "last_used_at REAL NOT NULL,"
                "use_count INTEGER NOT NULL DEFAULT 0)"
            )
            cols = [
                "cache_key",
                "source_lang",
                "target_lang",
                "backend_id",
                "glossary_version",
                "dictionary_version",
                "model_version",
                "source_text",
                "translated_text",
                "created_at",
                "last_used_at",
                "use_count",
            ]
            placeholders = ",".join("?" for _ in cols)
            col_sql = ",".join(cols)
            for row in src.execute(f"SELECT {col_sql} FROM translations"):
                dst.execute(
                    f"INSERT OR IGNORE INTO translations ({col_sql}) "
                    f"VALUES ({placeholders})",
                    row,
                )
            dst.commit()
        finally:
            src.close()
            dst.close()
        legacy.unlink(missing_ok=True)
        _remove_empty_legacy_home_dir(legacy.parent)
        return True
    except OSError:
        return False
    except Exception:
        # A corrupt legacy file must not stop the overlay from starting.
        return False


def _remove_empty_legacy_home_dir(directory: Path) -> None:
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass

def default_paths() -> Paths:
    config_home = _xdg("XDG_CONFIG_HOME", ".config")
    cache_home = _xdg("XDG_CACHE_HOME", ".cache")
    state_home = _xdg("XDG_STATE_HOME", ".local/state")

    # XDG_RUNTIME_DIR is a tmpfs that the session manager owns and wipes on logout.
    # When it is missing - a bare terminal, a service unit, a container - a
    # per-user directory under the state home is used instead. It is not volatile,
    # so anything there is treated as disposable and rewritten on every run.
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if xdg_runtime and Path(xdg_runtime).is_dir():
        runtime = Path(xdg_runtime) / APP_DIR
    else:
        runtime = state_home / APP_DIR / "run"

    cache_dir = Path(_env("CACHE") or (cache_home / APP_DIR))
    state_dir = Path(_env("STATE") or (state_home / APP_DIR))
    data_home = _xdg("XDG_DATA_HOME", ".local/share")
    data_dir = Path(_env("DATA") or (data_home / APP_DIR))
    return Paths(
        config_file=Path(_env("CONFIG") or (config_home / APP_DIR / "config.toml")),
        state_dir=state_dir,
        cache_dir=cache_dir,
        translate_cache=Path(_env("TRANSLATE_CACHE") or (cache_dir / "translate-cache.sqlite")),
        runtime_dir=runtime,
        lock=Path(_env("LOCK") or (runtime / "translate.lock")),
        pid=Path(_env("PID") or (runtime / "translate.pid")),
        text_pid=Path(_env("TEXT_PID") or (runtime / "textui.pid")),
        # Logs live in state, not runtime: XDG_RUNTIME_DIR is tmpfs and the
        # session manager wipes it, so a log written there is gone by the time
        # anyone reads it after a failure. The lock and pid below stay on tmpfs
        # because a stale one there is correct behaviour, not a diagnostic.
        log=Path(_env("LOG") or (state_dir / "overlay.log")),
        # The selector is a separate process with its own failure modes, so its
        # output is kept apart from the live overlay's.
        selector_log=Path(_env("SELECTOR_LOG") or (state_dir / "selector.log")),
        # The shell is pointed at runtime_dir and reads this as it loads, which
        # is why it is written before the process is started.
        selector_request=Path(
            _env("SELECTOR_REQUEST") or (runtime / "selector-request.json")
        ),
        # The single-selector lock used to sit in /tmp, where any other process on
        # the machine could take it and lock the user out of their own selector.
        selector_lock=Path(
            _env("SELECTOR_LOCK") or (runtime / "selector.lock")
        ),
        # Remembered regions live in state, not tmpfs runtime: otherwise every
        # logout forgets the box and the next Win+Shift+T feels like starting over.
        selector_geometry=Path(
            _env("SELECTOR_GEOMETRY") or (state_dir / "selector-geometry.json")
        ),
        data_dir=data_dir,
        dictionaries_dir=data_dir / "dictionaries",
        models_dir=data_dir / "models",
    )


_STORAGE_BANNER_STDERR = False
_STORAGE_BANNER_LOG = False


def storage_report(paths: Paths | None = None) -> list[str]:
    """Human-readable map of where this process keeps and sends data.

    Printed once at startup so a terminal session is not a black box: cache is
    local SQLite, dictionaries are under XDG data, logs under XDG state, and
    online translation only happens when a string misses cache/glossary.
    """
    p = paths or default_paths()
    try:
        from .lexicon.store import DATA_ROOT as lexicon_root
    except Exception:  # noqa: BLE001
        lexicon_root = Path("(lexicons unavailable)")
    lines = [
        "хранилище (всё локально, пока нет промаха кэша/глоссария):",
        f"  config          {p.config_file}",
        f"  translate-cache {p.translate_cache}  (SQLite, на диске)",
        f"  cache-dir       {p.cache_dir}",
        f"  dictionaries    {p.dictionaries_dir}  (пользовательские словари)",
        f"  models          {p.models_dir}  (offline language packs / CT2)",
        f"  translation-mem {p.data_dir / 'translation-memory.sqlite'}  (память, не глоссарий)",
        f"  lexicons        {lexicon_root}  (вшитые пачки)",
        "  language-packs  catalog: data/language_packs  (--packs / --pack-install)",
        f"  state           {p.state_dir}",
        f"  overlay-log     {p.log}",
        f"  selector-log    {p.selector_log}",
        f"  runtime         {p.runtime_dir}  (pid/lock, не конфиг)",
        "сеть:",
        "  • снимок области локален (OCR на машине);",
        "  • текст при промахе кэша → Google gtx (по умолчанию);",
        "  • allow_slow → MyMemory; local CT2 — если установлен pack;",
        "  • offline_only=true отключает онлайн-бэкенды;",
        "  • аналитики нет; конфиги композитора не меняются.",
        "см. docs/trust.md",
        "глоссарий ≠ память ≠ кэш: в глоссарий только --tm-promote.",
    ]
    return lines


def emit_storage_banner(
    paths: Paths | None = None,
    *,
    sink=None,
    also_log: bool = True,
) -> None:
    """Write the storage map to stderr and/or the overlay log (once each)."""
    global _STORAGE_BANNER_STDERR, _STORAGE_BANNER_LOG
    import sys

    lines = storage_report(paths)
    if sink is not None:
        for line in lines:
            sink(line)
    elif not _STORAGE_BANNER_STDERR:
        for line in lines:
            print(line, file=sys.stderr)
        _STORAGE_BANNER_STDERR = True
    if also_log and not _STORAGE_BANNER_LOG:
        try:
            from .core.text import tlog

            for line in lines:
                tlog(line)
            _STORAGE_BANNER_LOG = True
        except Exception:  # noqa: BLE001
            pass
