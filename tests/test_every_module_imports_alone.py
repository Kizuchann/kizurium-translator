"""Каждый модуль пакета должен импортироваться сам по себе.

Что было сломано.

Четыре модуля нельзя было импортировать как `import kizurium_translator.<...>` -
они падали с `ImportError: cannot import name ... from partially initialized
module`:

    layout.dialogue  ocr.frame  ocr.passes  translation.batch

Причина одна: `live/__init__.py` импортирует `layout.dialogue`, а тот на
верхнем уровне тянет `live.reconcile`, `ocr.engine` и `ocr.passes` - а те, в
свою очередь, тоже тянут `live.*`. Стрелка зависимости была направлена туда,
где находится низ.

Работало только потому, что CLI импортирует `live` первым и задаёт порядок
случайно. Проверка через `pkgutil.walk_packages` это тоже не ловит: она
импортирует модули по алфавиту, к моменту проверки нужные из них уже в
`sys.modules`. Отсюда и расхождение в оценках: один прогон давал «81 из 81», а
честный - каждый модуль в своём процессе.

Ниже - именно честная проверка: отдельный интерпретатор на каждый модуль.
"""

from __future__ import annotations

import os
import pathlib
import pkgutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _module_names() -> list[str]:
    names = [str(p.relative_to(SRC).with_suffix("")).replace("/", ".")
             for p in SRC.rglob("*.py")
             if p.name != "__init__.py"]
    return sorted(names)


def _child_env() -> dict[str, str]:
    """Окружение ребёнка — своё, но не выброшенное.

    Собственный PYTHONPATH нужен, чтобы не подхватить исходники извне. А вот
    остальное окружение выбрасывать нельзя: в сборке nix зависимости видны
    только через переменные, которые ставит fixup, и пустой env даёт
    `No module named 'PIL'` на каждом модуле - то есть проверка сообщала бы о
    песочнице, а не о цикле импортов.
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    # К началу, а не вместо: в сборке nix зависимости лежат именно в PYTHONPATH,
    # и подмена дала бы `No module named 'PIL'` на каждом модуле - проверка
    # сообщала бы о песочнице, а не о цикле.
    env["PYTHONPATH"] = f"{SRC}{os.pathsep}{existing}" if existing else str(SRC)
    return env


def _import_alone(name: str) -> subprocess.CompletedProcess:
    """Один модуль, один свежий интерпретатор, никакого порядка импортов."""
    return subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=120,
    )


def test_the_module_list_is_not_empty():
    assert len(_module_names()) > 50, "список модулей подозрительно короткий"


def test_every_module_imports_on_its_own():
    """Ни один модуль не должен зависеть от того, что импортировали до него."""
    failed: list[tuple[str, str]] = []
    for name in _module_names():
        proc = _import_alone(name)
        if proc.returncode != 0:
            tail = proc.stderr.strip().splitlines()
            failed.append((name, tail[-1] if tail else "без вывода"))
    assert not failed, "модули не импортируются по одному:\n" + "\n".join(
        f"  {n}: {e}" for n, e in failed
    )


def test_the_package_imports_as_a_whole():
    proc = subprocess.run(
        [sys.executable, "-c", "import kizurium_translator"],
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_walk_packages_covers_what_is_on_disk():
    """`walk_packages` и файловая проверка должны видеть один и тот же набор.

    Расхождение означало бы, что один из них что-то пропускает - а именно на этом
    и строилась неверная оценка «все модули импортируются».
    """
    sys.path.insert(0, str(SRC))
    import kizurium_translator as pkg

    walked = {m.name for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".")}
    on_disk = set(_module_names())
    missing = on_disk - walked
    assert not missing, f"walk_packages не видит: {sorted(missing)}"
