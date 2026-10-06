"""A still screen must not take the worker down with it.

The live worker walks the same code on every frame, and the branch that handles
a frame where nothing moved read a name that only the branch where something
did move had ever bound. On a still screen - which is most screens, and every
frame of a paused one - that read raised, the worker died where it stood, and
the cards it had drawn stayed on the desktop with nothing left to replace them.

The symptom is not a crash message. From the other side of the screen it is a
translation that will not come back: the last set of cards sits over content
that has since changed, and switching windows does not fix it, because the thing
that would have redrawn them is not running any more.

So this is a test about a name being bound, which is a strange thing to test and
the only thing that was wrong. It is here because nothing else covers it: the
full suite exercises the functions this sits between, with the value supplied.
"""

from __future__ import annotations

import ast
from pathlib import Path

from _where import owner

# `live` стал каталогом, и инкрементальный шаг уехал из тела цикла в
# `live/incremental.py`. Проверка
# идёт по владельцу функции, а не по выписанному пути: иначе следующий
# перенос снова молча сдвинет её мимо.
NAME = "dirty_lines"
GUARD = "dirty_ids"


def _module_of(func_name: str) -> Path:
    """Файл, где функция определена, по её владельцу."""
    return Path(owner(func_name).__file__)


def _find(func_name: str, module: Path) -> ast.FunctionDef:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in {module}")


def _worker() -> ast.FunctionDef:
    return _find("worker", _module_of("worker"))


def _incremental() -> ast.FunctionDef:
    """Функция адресного перечитывания.

    Раньше тест смотрел в `worker` и находил нужный блок там. Теперь он
    смотрит туда, где блок живёт на самом деле, и если перенос когда-нибудь
    откатят, тест упадёт с «not found», а не с «не нашлось блока» - это
    разные вещи, и второе легко принять за «условие перестало выполняться».
    """
    return _find("read_dirty_regions", _module_of("read_dirty_regions"))


def _is_incremental(tested: set[str]) -> bool:
    """Условие инкрементального прохода.

    Раньше это было имя `INCREMENTAL`; теперь порог лежит в объекте настроек,
    и условие читает `SETTINGS.INCREMENTAL`. Проверка узнаёт обе формы: тест
    защищает от пропуска связывания, а не от того, как названо поле.
    """
    return "INCREMENTAL" in tested or "SETTINGS" in tested


def _incremental_block() -> list[ast.stmt]:
    """The statements under the frame's incremental entry condition.

    Раньше нужный блок искался как `if` внутри `worker`, который и писал, и
    читал имя. После переноса шага в `live/incremental.py` условие входа
    осталось, но тело разом ушло в отдельную функцию, и такой блок больше не
    существует нигде.

    Теперь искомое - тело функции шага: присваивание и чтение `dirty_lines`
    должны быть в одном теле, и тест спрашивает именно это, без оглядки на то,
    чем оно огорожено.
    """
    fn = _incremental()
    return fn.body


def _writes(stmts: list[ast.stmt], guarded: bool) -> tuple[list[int], list[int]]:
    """Line numbers where NAME is bound, split into guarded and unguarded.

    A write is guarded when it sits inside an `if` whose condition mentions the
    name the branch tests. `ast.walk` flattens the nesting and cannot tell the
    two apart - it reports a write inside a branch as a write of the enclosing
    block as well, which is the whole question here - so the statements are
    descended into by hand, and each one is only ever counted at the depth it
    actually sits.
    """
    guarded_lines: list[int] = []
    open_lines: list[int] = []
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            tested = {n.id for n in ast.walk(stmt.test) if isinstance(n, ast.Name)}
            inner_guarded, inner_open = _writes(stmt.body, guarded or GUARD in tested)
            guarded_lines += inner_guarded
            open_lines += inner_open
            else_guarded, else_open = _writes(stmt.orelse, guarded)
            guarded_lines += else_guarded
            open_lines += else_open
            continue
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Name)
                and node.id == NAME
                and isinstance(node.ctx, ast.Store)
            ):
                (guarded_lines if guarded else open_lines).append(node.lineno)
    return guarded_lines, open_lines


class TestStillScreenDoesNotKillTheWorker:
    def test_the_name_is_bound_before_the_branch_that_may_not_run(self):
        """The read sits outside `if dirty_ids:`; the binding must as well.

        The whole block is under `if INCREMENTAL and tracked and...`, which is
        the frame's own entry condition and holds for any pass that reaches the
        incremental path at all. Inside it, the re-read runs only when the
        thumbnail diff scheduled something - and on a still screen it does not,
        which is the ordinary case, not an edge one.
        """
        guarded, open_at = _writes(_incremental_block(), False)
        assert open_at, (
            f"every write to {NAME} is inside `if {GUARD}:`, which a still "
            "screen skips, while the read below it is not conditional"
        )
        assert guarded, f"{NAME} is never assigned at all"

    def test_the_binding_comes_before_the_unconditional_read(self):
        """Order matters: a binding after the read does not help it."""
        worker = _incremental()
        first_write = min(
            node.lineno
            for node in ast.walk(worker)
            if isinstance(node, ast.Name)
            and node.id == NAME
            and isinstance(node.ctx, ast.Store)
        )
        reads = sorted(
            node.lineno
            for node in ast.walk(worker)
            if isinstance(node, ast.Name)
            and node.id == NAME
            and isinstance(node.ctx, ast.Load)
        )
        assert reads, f"the incremental path never reads {NAME}"
        assert first_write < reads[0], (
            f"{NAME} is read at line {reads[0]} but first bound at {first_write}"
        )
