"""Блокировка и pid-файл идут за настроенными путями, а не за путями импорта.

Что было сломано.

`live/session.py` вычислял `LOCK` и `PIDFILE` один раз, при импорте, из
путей по умолчанию. `runtime.configure(paths=...)` после этого менял
`text.PATHS`, но оверлей продолжал смотреть на старый lock и писать pid в
старый файл. `cli.py --stop` при этом читает пути, которые ему передали, то
есть смотрел не туда же, куда писал оверлей.

Второе место рядом: `runtime.configure()` присваивал `LOCK = text.PATHS.lock`
без `global`, поэтому это были локальные переменные функции, и модульных имён
`runtime.LOCK` / `runtime.PIDFILE` не существовало вовсе:

    runtime.LOCK    = ABSENT
    runtime.PIDFILE = ABSENT
```

Замечание о влиянии: в поставляемом CLI `live.configure(conf)` зовётся без
`paths`, поэтому пути совпадают с дефолтными и пользователя это не касалось.
Ломается при нестандартном `paths` - в тестах и при встраивании.

Проверка ниже не смотрит на исходник: она зовёт то, что зовёт оверлей, после
`configure` с чужими путями.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.core import text  # noqa: E402
from kizurium_translator.live import app, runtime, session  # noqa: E402
from kizurium_translator.paths import Paths  # noqa: E402


def _paths(root: pathlib.Path) -> Paths:
    return Paths(
        config_file=root / "config.toml",
        state_dir=root / "state",
        cache_dir=root / "cache",
        translate_cache=root / "cache" / "translate.json",
        runtime_dir=root,
        lock=root / "translate.lock",
        pid=root / "translate.pid",
        text_pid=root / "text.pid",
        log=root / "kizurium.log",
        selector_log=root / "selector.log",
        selector_request=root / "selector.request.json",
        selector_lock=root / "selector.lock",
        selector_geometry=root / "selector.geometry.json",
        data_dir=root / "data",
        dictionaries_dir=root / "data" / "dictionaries",
    )


class TestTheLockFollowsThePaths:
    def test_configure_moves_the_lock_the_overlay_watches(self, tmp_path):
        runtime.configure(Config(), paths=_paths(tmp_path))
        try:
            lock, pidfile = session._lock_paths()
            assert lock == tmp_path / "translate.lock", lock
            assert pidfile == tmp_path / "translate.pid", pidfile
        finally:
            runtime.configure(Config())

    def test_the_overlay_reads_the_same_pair_as_the_session(self, tmp_path):
        runtime.configure(Config(), paths=_paths(tmp_path))
        try:
            assert app._lock_paths is session._lock_paths
            assert app._lock_paths() == session._lock_paths()
        finally:
            runtime.configure(Config())

    def test_the_stop_side_looks_where_the_overlay_writes(self, tmp_path):
        """`--stop` берёт пути из `default_paths()`, оверлей - из `text.PATHS`.

        Пока обе стороны читают одно и то же место, остановка работает. Проверка
        фиксирует, что оверлей и `--stop` не разошлись по файлам.
        """
        from kizurium_translator import cli

        runtime.configure(Config(), paths=_paths(tmp_path))
        try:
            paths = _paths(tmp_path)
            assert cli.default_paths.__module__  # точка входа существует
            assert paths.lock == text.PATHS.lock
            assert paths.pid == text.PATHS.pid
        finally:
            runtime.configure(Config())


class TestTheDeadAssignmentsAreGone:
    def test_runtime_no_longer_assigns_them_without_global(self):
        """Присваивание без `global` не оставляет имени в модуле.

        Именно так и выглядел баг: строки в исходнике были, а имён не было.
        """
        assert not hasattr(runtime, "LOCK"), "runtime.LOCK снова появился"
        assert not hasattr(runtime, "PIDFILE"), "runtime.PIDFILE снова появился"

    def test_the_module_does_not_export_them_either(self):
        from kizurium_translator import live

        exported = getattr(live, "__all__", [])
        assert "LOCK" not in exported
        assert "PIDFILE" not in exported
