"""Имена, которые вызывает тело цикла, должны существовать.

Мёртвый блок сравнения с собственным оверлеем звал `_overlay_texts` и
`_same_text`. Ни одна из них не была определена ни в одном коммите истории.
Блок сработал ноль раз за всю историю логов, а на одном наборе окон дошёл
и уронил цикл с `NameError`.

Проверка ловит это без экрана: имя из вызова в теле цикла обязано быть
определено в том же пакете. Импорт, строка и класс исключения - не в счёт:
смысл проверки в том, что функция существует и её можно вызвать.
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

LIVE = pathlib.Path(__file__).resolve().parent.parent / "src" / "kizurium_translator" / "live"

# Имена, которые не обязаны быть определены: они либо параметры, либо
# локальные переменые тела функции.
def _defined_in_function(fn: ast.FunctionDef, source: str) -> set[str]:
    """Всё, чему имя доступно внутри функции, включая вложенные def.

    Вложенная функция видна во всём теле внешней, и без неё проверка
    сообщает о `_gdk_monitor_at` как о несуществующем имени - то есть о
    настоящей функции, определённой рядом.

    Вложенные функции обходятся рекурсивно и по отдельности: их собственные
    имена видны только внутри них, и если считать их частью внешней, то
    пропущенное имя спрячется за именем соседа.
    """
    out: set[str] = set()
    args = fn.args
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        out.add(a.arg)
    if args.vararg:
        out.add(args.vararg.arg)
    if args.kwarg:
        out.add(args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.For, ast.With, ast.comprehension)):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.For):
                targets = [node.target]
            elif isinstance(node, ast.With):
                targets = [i.optional_vars for i in node.items if i.optional_vars]
            else:
                targets = [node.target]
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        out.add(sub.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        if isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
    return out


def _module_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Try):
            for sub in node.body:
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            out.add(t.id)
            for h in node.handlers:
                for sub in ast.walk(h):
                    if isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                out.add(t.id)
    return out


def _package_names() -> set[str]:
    out: set[str] = set()
    for p in sorted(LIVE.glob("*.py")):
        out |= _module_names(p)
    return out


class TestEveryNameTheCycleUsesExists:
    """Один проход по всем функциям пакета: каждое вызванное имя найдено."""

    def _called_undefined(self) -> list[str]:
        known = _package_names() | set(dir(builtins))
        missing: list[str] = []
        # Функции, объявленные внутри других: их имена видны в теле объявления.
        nested: dict[str, str] = {}
        for p in sorted(LIVE.glob("*.py")):
            tree = ast.parse(p.read_text())
            for outer in ast.walk(tree):
                if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(outer):
                    if (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node is not outer
                    ):
                        nested[node.name] = outer.name
        for p in sorted(LIVE.glob("*.py")):
            source = p.read_text()
            tree = ast.parse(source)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                local = _defined_in_function(fn, source)
                # Если функция вложена, она видит и всё, что объявлено выше.
                if fn.name in nested:
                    outer_name = nested[fn.name]
                    for outer in ast.walk(tree):
                        if isinstance(outer, ast.FunctionDef) and outer.name == outer_name:
                            local |= _defined_in_function(outer, source)
                            break
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    name = getattr(f, "id", None)
                    if name is None:
                        continue
                    if name in known or name in local:
                        continue
                    missing.append(f"{p.name}:{node.lineno}: {fn.name}() зовёт {name}")
        return missing

    def test_nothing_calls_a_name_that_does_not_exist(self):
        missing = self._called_undefined()
        assert missing == [], (
            "вызов функции, которой нет в пакете:\n" + "\n".join(sorted(set(missing)))
        )


class TestTheEchoBlockIsReachableAndWorking:
    """Функции на месте - но надо убедиться, что они делают то, о чём написано."""

    def _state_with(self, texts: list[str]):
        from kizurium_translator.live.state import State

        st = State()
        st.set(
            [
                {"x": i * 10, "y": 0, "src_w": 40, "src_h": 20, "text": t}
                for i, t in enumerate(texts)
            ],
            (0, 0, 100, 100),
        )
        return st

    def test_it_reads_what_the_overlay_drew(self):
        from kizurium_translator.live.echo import overlay_texts

        st = self._state_with(["Начать квест", "Счёт"])
        got = overlay_texts(st)
        assert "начать квест" in got, got
        assert "счёт" in got, got

    def test_it_matches_our_own_reading(self):
        from kizurium_translator.live.echo import overlay_texts, same_text

        ours = overlay_texts(self._state_with(["Начать квест"]))
        assert same_text("Начать квест", ours)
        assert same_text("НАЧАТЬ КВЕСТ", ours)
        assert same_text("Начать  квест.", ours)

    def test_it_does_not_match_someone_elses_text(self):
        from kizurium_translator.live.echo import overlay_texts, same_text

        ours = overlay_texts(self._state_with(["Начать квест"]))
        assert not same_text("Start Quest", ours)
        assert not same_text("", ours)

    def test_an_empty_overlay_matches_nothing(self):
        from kizurium_translator.live.echo import overlay_texts, same_text
        from kizurium_translator.live.state import State

        ours = overlay_texts(State())
        assert ours == set()
        assert not same_text("Любой текст", ours)

    @pytest.mark.parametrize(
        ("drawn", "read"),
        [
            ("Квест\nСчёт", "Квест Счёт"),
            ("Квест\n\nСчёт", "Квест Счёт"),
            ("Начать квест", "Начать  квест"),
        ],
    )
    def test_a_wrapped_card_matches_however_it_was_read(self, drawn, read):
        """Перенос в карточке ломает сравнение по фрагментам, поэтому сверяется
        и карточка целиком, и её строки по отдельности."""
        from kizurium_translator.live.echo import overlay_texts, same_text

        ours = overlay_texts(self._state_with([drawn]))
        assert same_text(read, ours), (drawn, read, ours)

    def test_word_boundaries_still_matter(self):
        """Схлопывание пробелов подряд не должно делать разные слова одним."""
        from kizurium_translator.live.echo import overlay_texts, same_text

        ours = overlay_texts(self._state_with(["Начать квест"]))
        assert not same_text("НачатьКвест", ours)
