"""Reference map for the source tree.

Answers, for every top-level definition: who refers to it, and how. The point is
to tell apart a symbol nobody reaches from one that is only reached from a
callback, an entry point or a test, because those need different evidence before
a removal.

The callers a name search cannot see are the reason this is not `rg`: a function
passed to a toolkit, a string in a GTK or QML signal handler, a name in a
config file, an import by attribute.

    python scripts/refmap.py                 # every definition
    python scripts/refmap.py module_available
    python scripts/refmap.py --unused        # nothing outside its own file
    python scripts/refmap.py --tests         # only reached from tests
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"

# Names reached without a textual reference. Each is a contract, not a guess:
# either a CLI entry point, a signal handler, a data file, or a toolkit that
# calls back by name.
DYNAMIC_ROOTS = {
    "main", "main_for_cli", "configure", "worker", "shutdown",
    "translate_text", "text_window", "action_menu",
    "prepare", "begin", "close", "finish", "start", "stop", "cancel",
}
DYNAMIC_ATTRS = {
    "on_clicked", "on_activated", "on_triggered", "on_exited", "onExited",
    "set_draw_func", "connect", "addAction", "set_text", "setText",
    "get", "read_text", "parse_args", "add_argument", "cmdclass",
}


def definitions(path: Path) -> dict[str, int]:
    """Top-level functions and classes, with their line."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node.lineno
    return out


def all_names(path: Path) -> set[str]:
    """Identifier and string literals used in a file.

    Only use sites: the name of a def is not a Name node, so a definition never
    counts as a reference to itself. That distinction matters because a symbol
    called from the file that defines it is alive, and treating "no callers
    outside this file" as unused reported most of the module as dead.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def data_files() -> list[Path]:
    """Config and data files that may name a symbol in a string."""
    out: list[Path] = []
    for pat in ("*.toml", "*.sh", "*.json"):
        out.extend(p for p in ROOT.rglob(pat)
                   if ".venv" not in p.parts and ".git" not in p.parts)
    out.extend((ROOT / "src").rglob("*.qml"))
    return out


def build() -> dict[str, dict]:
    files = sorted(SRC.rglob("*.py")) + sorted(TESTS.rglob("*.py"))
    src_names = {p: all_names(p) for p in files}
    data_names: dict[Path, set[str]] = {}
    for p in data_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        data_names[p] = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text))

    report: dict[str, dict] = {}
    for path in sorted(SRC.rglob("*.py")):
        own = definitions(path)
        own_uses = all_names(path)
        for name, line in own.items():
            callers: list[str] = []
 # A call inside the defining file counts, and is labelled as such so
 # the report distinguishes "module-private, used here" from
 # "reachable from the rest of the package".
            if name in own_uses:
                callers.append(f"self:{path.name}")
            for other, names in src_names.items():
                if other == path:
                    continue
                if name in names:
                    kind = "test" if TESTS in other.parents else "src"
                    callers.append(f"{kind}:{other.name}")
            data_callers = [str(p.relative_to(ROOT)) for p, names in data_names.items()
                            if name in names and "translate-cache" not in p.name]
            report[name] = {
                "file": str(path.relative_to(ROOT)),
                "line": line,
                "callers": sorted(set(callers)),
                "data_callers": sorted(set(data_callers)),
                "dynamic": name in DYNAMIC_ROOTS,
            }
    return report


def verdict(name: str, info: dict) -> str:
    if info["callers"] or info["data_callers"]:
        return "живой"
    if info["dynamic"]:
        return "корень/dynamic — проверять вручную"
    return "НЕТ ССЫЛОК — кандидат на удаление"


def main() -> int:
    args = sys.argv[1:]
    show_unused = "--unused" in args
    show_tests = "--tests" in args
    names = [a for a in args if not a.startswith("--")]

    report = build()
    if names:
        for name in names:
            info = report.get(name)
            if not info:
                print(f"{name}: не найдено")
                continue
            print(f"\n{name}  [{info['file']}:{info['line']}]  {verdict(name, info)}")
            for c in info["callers"]:
                print(f"    вызов: {c}")
            for c in info["data_callers"]:
                print(f"    данные: {c}")
            if info["dynamic"]:
                print("    объявлен как корень/dynamic")
        return 0

    dead = [(n, i) for n, i in report.items()
            if not i["callers"] and not i["data_callers"] and not i["dynamic"]]
    self_only = [(n, i) for n, i in report.items()
                 if i["callers"] and all(c.startswith("self:") for c in i["callers"])
                 and not i["data_callers"]]
    test_only = [(n, i) for n, i in report.items()
                 if i["callers"] and all(c.startswith("test:") for c in i["callers"])]

    print(f"всего определений верхнего уровня: {len(report)}")
    print(f"вообще без ссылок:                 {len(dead)}")
    print(f"только внутри своего файла:        {len(self_only)}")
    print(f"только из тестов:                  {len(test_only)}")

    if show_unused:
        print("\nкандидаты на удаление:")
        for n, i in sorted(dead, key=lambda kv: kv[1]["file"]):
            print(f"  {i['file']}:{i['line']:<5} {n}")
    if show_tests:
        print("\nтолько из тестов:")
        for n, i in sorted(test_only, key=lambda kv: kv[1]["file"]):
            print(f"  {i['file']}:{i['line']:<5} {n}  <- {', '.join(i['callers'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())