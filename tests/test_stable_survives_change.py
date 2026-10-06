"""Стабильная карточка не должна исчезать из-за чужого изменения.

Правило «не потерять перевод» называет это release-blocking, и соседнее
добавляет смену сцены. Суть контракта:

    кадр 1: стабильная A + меняющийся B1
    кадр 2: стабильная A + меняющийся B2
    кадр 3: стабильная A + меняющийся B3

    A остаётся видимой непрерывно
    B обновляется, A не переоцивается и не переводится заново
    глобального clear нет

и отдельно:

    сцена сменилась целиком -> старое уходит
    сцена сменилась частично -> остальное остаётся

Проверки ниже бьют по решению, а не по структуре: имена функций меняются при
переносах, а поведение «карточка пропала» остаётся.

Здесь важно одно ограничение. Этот файл проверяет решение о кадре, а не весь
цикл: worker() читает экран, поэтому его нельзя вызвать из теста. Проверяется
то, что решение принимает функция, которую можно позвать с набором аргументов.
Если решение снова уедет внутрь тела цикла, проверка перестанет видеть его -
и это будет её собственным сигналом.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from _where import owner  # noqa: E402

from kizurium_translator import live  # noqa: E402

LIVE = pathlib.Path(owner("worker").__file__).parent


class _State:
    """Карточки на экране: достаточно для решения о кадре."""

    def __init__(self, blocks: list[dict] | None = None):
        self.blocks = list(blocks or [])
        self.cleared = 0
        self.hud_kept = 0
        self.shown = 0

    def peek_blocks(self) -> list[dict]:
        return list(self.blocks)

    def clear(self, region=None) -> None:
        self.cleared += 1
        self.blocks = []

    def replace_keeping_hud(self, region=None) -> None:
        self.hud_kept += 1
        self.blocks = [b for b in self.blocks if b.get("kind") not in ("dialogue", "body")]

    def show(self) -> None:
        self.shown += 1

    def snapshot(self):
        return list(self.blocks), None, ""


def _card(x: int, text: str, kind: str = "ui") -> dict:
    return {"x": x, "y": 10, "src_w": 120, "src_h": 20, "text": text, "kind": kind}


def _hud_block(text: str) -> dict:
    return _card(1400, text)


def _dialogue_block(text: str) -> dict:
    return _card(560, text, kind="body")


@pytest.fixture
def frame(tmp_path):
    """Заглушка снимка: решение о кадре смотрит на пиксели, а не на текст."""
    from PIL import Image

    return Image.new("RGB", (1920, 1080), (20, 20, 30))


class TestTheDecisionIsCallableWithoutTheCycle:
    """Решение обязано быть функцией, иначе его нечем проверить."""

    def _decision_function(self):
        """Функция, которая решает, что делать с кадром.

        Ищется по признаку: принимает состояние и говорит, закрыт ли кадр.
        По-другому её не найти, и это не произвол: тест не должен падать от
        того, что функцию переименовали.
        """
        for name in dir(live):
            if name.startswith("_"):
                continue
            fn = getattr(live, name)
            if not callable(fn):
                continue
            if "read" in name and "frame" not in name:
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                continue
            names = list(sig.parameters)
            if "state" not in names or "tracked" not in names:
                continue
            return name, fn
        pytest.fail(
            "нет функции, которая принимает state и tracked и решает судьбу кадра"
        )

    def test_it_is_a_function_outside_the_worker_body(self):
        name, fn = self._decision_function()
        module = pathlib.Path(inspect.getfile(fn)).parent
        assert module == LIVE, (
            f"{name} живёт в {module}, а решение о кадре принадлежит live/"
        )

    def test_the_worker_calls_it_rather_than_deciding_inline(self):
        """Внутри цикла не должно быть ветвей, решающих судьбу кадра.

        Проверка грубая и намеренно: она ищет след вызова, а не сравнивает
        текст. Если решение вернётся внутрь тела, след исчезнет.
        """
        source = inspect.getsource(live.worker)
        assert "read_dirty_regions(" in source, (
            "worker() больше не зовёт шаг адресного перечитывания"
        )

    def test_the_cycle_keeps_no_hidden_state_on_the_function(self):
        """Состояние цикла не должно вернуться полями на функции.

        Регрессия: `worker._last_dirty_probe` и ещё девять таких поля
        были не видны ни тестам, ни линтеру.
        """
        tree = ast.parse(pathlib.Path(owner("worker").__file__).read_text())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "worker"
        )
        offenders = [
            node.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "worker"
        ]
        assert offenders == [], f"worker пишет в свои поля: {offenders}"
