"""сборка карточек вынесена из `worker()` в `live.build`.

Тест на форму проходит и на мёртвом коде: функция может быть написана, но не
вызвана, а `worker()` может падать на `NameError` прямо в кадре - так и вышло
при первом переносе, когда заголовок цикла остался со ссылкой на
`to_translate`. Поэтому здесь проверяется поведение: функция зовётся с теми
же входами, что и раньше, и даёт то, что от неё ждёт кадр.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live import build  # noqa: E402


def _img():
    from PIL import Image

    return Image.new("RGB", (1920, 1080), (12, 14, 20))


def _par(text: str, kind: str = "ui", **extra) -> dict:
    par = {"text": text, "kind": kind, "box": (100, 100, 400, 140), "conf": 90}
    par.update(extra)
    return par


def _build(pairs, **modes):
    opts = {
        "japanese_mode": False,
        "dialogue_mode": False,
        "eng_ui_mode": True,
        "region_img": _img(),
        "rx": 0,
        "ry": 0,
        "rw": 1920,
        "rh": 1080,
    }
    opts.update(modes)
    return build.build_blocks(pairs, **opts)


class TestTheBlocksAreBuilt:
    def test_a_good_pair_becomes_a_card(self):
        blocks = _build([(_par("Settings"), "Настройки")])
        assert len(blocks) == 1, blocks
        assert blocks[0]["source"] == "Settings"
        assert blocks[0]["text"] == "Настройки"

    def test_several_pairs_keep_their_order(self):
        blocks = _build(
            [(_par("Settings"), "Настройки"), (_par("Reset"), "Сброс")]
        )
        assert [b["text"] for b in blocks] == ["Настройки", "Сброс"]

    def test_the_frame_does_not_depend_on_the_caller(self):
        """Пустой перевод и перевод, совпадающий с оригиналом, не рисуются."""
        blocks = _build([(_par("Settings"), ""), (_par("Reset"), "Reset")])
        assert blocks == []

    def test_a_cyrillic_translation_is_not_thrown_away_as_garbage(self):
        """Тот случай, ради которого условие и писалось (ws2."""
        blocks = _build([(_par("Distant Voice"), "Далёкий голос")])
        assert [b["text"] for b in blocks] == ["Далёкий голос"]


class TestTheFunctionIsWired:
    def test_worker_calls_it(self):
        """Функция, которую никто не зовёт, - это переименованный кусок кода."""

        source = pathlib.Path(
            pathlib.Path(build.__file__).parent / "session.py"
        ).read_text()
        assert "blocks = build_blocks(" in source

    def test_the_loop_header_no_longer_names_the_caller_variables(self):
        """Первый перенос оставил в заголовке `zip(to_translate,...)` -
        `NameError` в кадре, тесты были зелёные. Заголовок цикла принадлежит
        функции, значит и переменные в нём принадлежат ей."""
        import ast

        tree = ast.parse(pathlib.Path(build.__file__).read_text())
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "build_blocks"
        )
        params = {a.arg for a in fn.args.args}
        assigned = {
            n.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        for node in ast.walk(fn):
            if not isinstance(node, ast.For):
                continue
            it = node.iter
            names = (
                {n.id for n in ast.walk(it) if isinstance(n, ast.Name)}
                if it is not None
                else set()
            )
            unknown = names - params - assigned - {"zip", "enumerate", "range"}
            assert not unknown, f"цикл в build_blocks() читает внешние имена: {unknown}"


@pytest.mark.parametrize(
    ("name", "attr"),
    [
        ("build_blocks", "build_blocks"),
        ("expand_subtitle_line_blocks", "expand_subtitle_line_blocks"),
    ],
)
def test_the_package_re_exports_it(name, attr):
    from kizurium_translator import live

    assert getattr(live, attr) is getattr(build, attr)
    assert name in getattr(live, "__all__", [])
