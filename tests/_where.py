"""Где на самом деле лежит имя, которое тест хочет подменить.

Раздел 8 плана разделил `live.py` на слои, и `live` теперь реэкспортирует их
имена. Реэкспорт - это копия в `__init__`, а не псевдоним: `monkeypatch.setattr(live,
"region_text",...)` после разделения меняет атрибут в `live`, а код зовёт
`region_text` в `translation.service`, и подмена не срабатывает. Тест проходит
сравнительно, проверяет старую форму и падает на новой - или, что хуже, молча
перестаёт ничего проверять.

Поэтому подменять надо у того модуля, который имя определил. Имена не хардкодятся
в списке: модуль ищется разбором исходников пакета, и следующий разрез не
потребует править тесты.
"""

from __future__ import annotations

import ast
import functools
import importlib
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / "src" / "kizurium_translator"


@functools.lru_cache(maxsize=1)
def _defined() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for nm in names:
                # Первый по алфавиту - тот, кто определил: `__init__.py` только
                # переэкспортирует и определением не считается.
                prev = out.get(nm)
                if prev is None or path.name != "__init__.py":
                    out.setdefault(nm, str(path.relative_to(PKG))[:-3].replace("/", "."))
    return out


def source_of(name: str) -> str:
    """Исходник модуля, определившего `name`.

    Структурные тесты читают код текстом и проверяют, что в нужной функции есть
    нужная строка. Такой тест обязан смотреть в тот файл, где функция теперь
    лежит, а не в пакет `live` целиком: тот указывает на свой `__init__.py` с
    одним списком имён, и проверка проходит, ни разу не увидев функции.
    """
def source_text(name: str) -> str:
    import pathlib as _p
    mod = owner(name)
    return _p.Path(mod.__file__).read_text(encoding="utf-8")


def all_sources() -> list[pathlib.Path]:
    """Все модули слоёв пакета.

    Дымовая проверка «код разбирается» должна видеть весь код, а не один
    `__init__.py`: после разделения `live` стал каталогом, и проверка
    `ast.parse(Path(live.__file__))` разбирала список имён, ни разу не
    дойдя до функций, которые её и защищали.
    """
    return [
        p for p in sorted(PKG.rglob("*.py"))
        if "__pycache__" not in str(p) and p.name != "__init__.py"
    ]


def holders(name: str) -> list:
    """Все модули пакета, у которых есть атрибут `name`.

    `from m import f` - это ссылка на объект в момент импорта, а не обращение
    к `m.f`. Модуль, который привёз имя себе, держит свою копию ссылки, и
    подмена у владельца её не касается. Поэтому подменять надо везде, где
    атрибут есть, а не только у того, кто определил.

    На производственный код это не влияет: там имя никто не подменяет на
    лету. Ловушка была только в тестах, и молчаливая - подмена не срабатывала,
    тест проходил, проверяя не то.
    """
    import pkgutil

    import kizurium_translator

    out = []
    for info in pkgutil.walk_packages(kizurium_translator.__path__,
                                      kizurium_translator.__name__ + "."):
        try:
            mod = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 - модуль может тянуть GTK
            continue
        if hasattr(mod, name):
            out.append(mod)
    return out


def patch_all(monkeypatch, name: str, value) -> None:
    """Подменяет имя во всех модулях пакета, где оно есть."""
    mods = holders(name)
    if not mods:
        raise KeyError(f"имя {name!r} не найдено ни в одном модуле пакета")
    for mod in mods:
        monkeypatch.setattr(mod, name, value)


def live_value(name: str):
    """Текущее значение имени там, где оно определено.

    `live.<имя>` - копия на момент импорта. `configure` пишет в модуль-владелец,
    поэтому чтение через `live` возвращает то, что было до настройки, и тест
    падает на верном коде.
    """
    return getattr(owner(name), name)


#: Поля объектов состояния. Их не ищет разбор модулей: объявлены они как
#: поля `dataclass`, и держатся не в модуле, а в экземпляре. Подмена идёт по
#: экземпляру, поэтому искать надо здесь, а не в определениях.
FIELDS = {
    "disabled": "kizurium_translator.live.session",
    "translator": "kizurium_translator.live.session",
}


def owner(name: str):
    """Модуль, определивший `name`."""
    if name in FIELDS:
        return importlib.import_module(FIELDS[name])
    rel = _defined().get(name)
    if rel is None:
        raise KeyError(f"имя {name!r} нигде не определено в пакете")
    return importlib.import_module(f"kizurium_translator.{rel}")
