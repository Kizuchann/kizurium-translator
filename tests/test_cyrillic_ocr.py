"""Кириллица на экране должна читаться, а не превращаться в латинский мусор.

Проверки ниже фиксируют то, чего в коде не было. Русский текст переводился
только потому, что латинская модель читала его как латиницу (`Сайт` -> `sayt`,
`Сущ.` -> `Cy.`), а на самом деле он не читался вовсе.

`TestTheReaderReadsCyrillicInARealProcess` — главная проверка. Она повторяет
то, что делает настоящее приложение: сначала загружает GTK, потом читает
кадр. При этом порядке ONNX-модели кириллицы возвращали пустоту: 39 шагов из
40 с уверенностью выше 0.9 без GTK и 0 из 40 с ним. Проверка проходит
именно потому, что GTK загружен - без этого она была бы зелёной на сломанном
коде, как и все предыдущие.

Часть проверок падает на коде, который был до правки. Это и есть причина их
написать до того, как править.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import sys
import time

import pytest

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

# GTK в приложении загружен до первого кадра. Это условие проверок, а не
# украшение: именно из-за него молча пропадал кириллический OCR.
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from _where import patch_all  # noqa: E402
from gi.repository import Gtk  # noqa: E402,F401

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import reconcile  # noqa: E402

CYRILLIC_FRAME = str(
    pathlib.Path(__file__).resolve().parents[1] / ".local" / "screencheck" / "cyr-synth.png"
)

# The frame a real run produced. It is a working artefact rather than a fixture,
# so it is not in the repository, and a clone has no way to make it. The synthetic
# frame below covers the same ground without it.
_has_saved_frame = os.path.isfile(CYRILLIC_FRAME)
requires_saved_frame = pytest.mark.skipif(
    not _has_saved_frame,
    reason="нет сохранённого кадра: .local/ не отслеживается git",
)


def _synth_frame() -> Image.Image:
    """Кадр с русским текстом. Шрифт с кириллицей есть в системе."""
    img = Image.new("RGB", (560, 150), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans.ttf", 34)
    draw.text((14, 16), "Перевод Корректор", font=font, fill=(0, 0, 0))
    draw.text((14, 66), "Настройки сайта", font=font, fill=(0, 0, 0))
    draw.text((14, 110), "Войти", font=font, fill=(0, 0, 0))
    return img


def _line(text: str, box=(0, 0, 100, 20)) -> dict:
    return {"box": box, "text": text, "conf": 95.0, "engine": "tesseract"}


def _frame(w: int = 640, h: int = 200) -> Image.Image:
    return Image.new("RGB", (w, h), (18, 18, 24))


def _nothing(monkeypatch):
    """Make every ordinary reader come back empty, so the frame reaches the end."""
    patch_all(monkeypatch, "rapid_ocr_lines", lambda *a, **k: [])
    patch_all(monkeypatch, "subtitle_ocr_lines", lambda *a, **k: [])
    patch_all(monkeypatch, "ocr_region_by_columns", lambda *a, **k: [])
    patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))


class TestTheReaderReadsCyrillicInARealProcess:
    """Сквозная проверка на настоящем кадре при загруженном GTK."""

    @pytest.mark.needs_host
    def test_it_reads_russian_off_the_screen(self):
        got = reconcile.rapid_ocr_lines_cyrillic(_synth_frame())
        joined = " ".join(str(p.get("text", "")) for p in got)
        assert "Перевод" in joined, joined
        assert "Корректор" in joined, joined

    @pytest.mark.needs_host
    def test_it_drops_what_is_not_cyrillic(self):
        """Латинская строка и одиночные символы — не кириллическое чтение."""
        got = reconcile.rapid_ocr_lines_cyrillic(_synth_frame())
        for p in got:
            text = str(p.get("text", ""))
            assert text.strip(), p
            assert any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text), text

    @requires_saved_frame
    def test_the_saved_frame_on_disk_reads_the_same_way(self):
        """Тот же кадр, сохранённый на диск, - как в живом прогоне."""
        import os

        if not os.path.exists(CYRILLIC_FRAME):
            _synth_frame().save(CYRILLIC_FRAME)
        img = Image.open(CYRILLIC_FRAME).convert("RGB")
        joined = " ".join(str(p.get("text", "")) for p in reconcile.rapid_ocr_lines_cyrillic(img))
        assert "Перевод" in joined, joined


class TestTheDecisionAsksTheTableNotTheLetters:
    """Почерк определяется таблицей, а не подсчётом букв в коде."""

    def test_the_filter_goes_through_the_script_helper(self):
        source = inspect.getsource(reconcile.rapid_ocr_lines_cyrillic)
        assert "block_script" in source, "почерк определяется мимо core.text"

    def test_the_language_set_is_a_constant_not_an_inline_literal(self):
        source = inspect.getsource(reconcile)
        assert "TESSERACT_CYRILLIC" in source, "набор языков tesseract не назван"


class TestAnEmptyFrameAsksTheCyrillicReader:
    """Пустой кадр - это повод спросить другой почерк, а не сдаться."""

    def test_a_frame_with_no_usable_text_still_returns_the_cyrillic_reading(
        self, monkeypatch
    ):
        _nothing(monkeypatch)
        asked: list[bool] = []

        def cyrillic(img, *a, **k):
            asked.append(True)
            return [_line("Перевод"), _line("Корректор")]

        patch_all(monkeypatch, "rapid_ocr_lines_cyrillic", cyrillic)
        reconcile.reset_cyrillic_probe()

        got, _mode = live.read_frame(_frame())

        assert asked, "кириллический проход не был запрошен вообще"
        assert [str(b["text"]) for b in got] == ["Перевод", "Корректор"]

    def test_an_empty_frame_is_still_empty_when_there_is_nothing_to_read(
        self, monkeypatch
    ):
        """Ничего не нашлось - значит нечего рисовать."""
        _nothing(monkeypatch)
        patch_all(monkeypatch, "rapid_ocr_lines_cyrillic", lambda img, *a, **k: [])
        reconcile.reset_cyrillic_probe()

        got, _mode = live.read_frame(_frame())

        assert got == [], got


class TestTheProbeStaysOffTheHotPath:
    """Игра на английском переводится каждый цикл. Доплачивать за кириллицу
    на каждом кадре нельзя - это полторы-две секунды на кадр."""

    def test_a_frame_that_already_has_blocks_never_asks_for_cyrillic(self, monkeypatch):
        asked: list[bool] = []

        def latin(img, *a, **k):
            return [
                _line("Settings", box=(0, 0, 100, 20)),
                _line("Confirm", box=(0, 30, 100, 50)),
            ]

        patch_all(monkeypatch, "rapid_ocr_lines", latin)
        patch_all(monkeypatch, "subtitle_ocr_lines", lambda *a, **k: [])
        patch_all(monkeypatch, "ocr_region_by_columns", lambda *a, **k: [])
        patch_all(monkeypatch, "refine_japanese_blocks", lambda img, lines, now: (lines, 0))
        patch_all(
            monkeypatch,
            "rapid_ocr_lines_cyrillic",
            lambda img, *a, **k: asked.append(True) or [_line("Перевод")],
        )
        reconcile.reset_cyrillic_probe()

        live.read_frame(_frame())

        assert not asked, "кириллица спросина на кадре, который и так разобран"

    def test_repeated_empty_frames_do_not_pay_for_it_every_cycle(self, monkeypatch):
        """Пустой кадр в игре (смаз, тёмная сцена) не должен зондировать
        кириллицу каждый цикл - иначе цикл удлиняется навсегда."""
        _nothing(monkeypatch)
        calls: list[int] = []

        def cyrillic(img, *a, **k):
            calls.append(1)
            return []

        patch_all(monkeypatch, "rapid_ocr_lines_cyrillic", cyrillic)
        reconcile.reset_cyrillic_probe()

        live.read_frame(_frame())
        live.read_frame(_frame())
        live.read_frame(_frame())

        assert len(calls) == 1, f"зонд сработал {len(calls)} раз подряд без паузы"

    def test_the_pause_is_a_real_number_of_seconds(self):
        cooldown = getattr(reconcile, "CYRILLIC_PROBE_COOLDOWN_S", 0)
        assert isinstance(cooldown, (int, float))
        assert cooldown >= 1.0, cooldown


class TestTheProbeIsNotForeverStuck:
    """Пауза не должна превращаться в «никогда»: экран мог смениться."""

    def test_the_probe_returns_after_the_pause(self, monkeypatch):
        _nothing(monkeypatch)
        calls: list[float] = []

        def cyrillic(img, *a, **k):
            calls.append(time.monotonic())
            return [_line("Перевод")]

        patch_all(monkeypatch, "rapid_ocr_lines_cyrillic", cyrillic)
        reconcile.reset_cyrillic_probe()
        monkeypatch.setattr(reconcile, "CYRILLIC_PROBE_COOLDOWN_S", 0.01)

        live.read_frame(_frame())
        time.sleep(0.03)
        live.read_frame(_frame())

        assert len(calls) == 2, "после паузы зонд не повторился"


class TestTheProbeIsWiredWhereEveryEmptyFramePasses:
    """Зонд обязан стоять там, где виден любой пустой исход.

    Внутри `ocr_image` это невозможно: кадр уходит на любой из десятка ранних
    выходов, и каждый возвращает пустой список, не доходя до конца функции.
    Именно это и случилось на живом окне: кадр ушёл по раннему выходу с
    `final-ui n=0`, а зонд, стоявший перед последним `return`, не сработал.
    """

    def test_the_worker_reads_through_the_probe(self):
        source = inspect.getsource(live.worker)
        assert "read_frame(" in source, "worker зовёт ocr_image напрямую и зонд мимо"

    def test_the_probe_sits_after_the_frame_reader(self):
        source = inspect.getsource(live.read_frame)
        assert source.index("ocr_image(") < source.index("cyrillic_probe_due()")

    def test_the_probe_only_runs_on_an_empty_frame(self):
        source = inspect.getsource(live.read_frame)
        assert "if lines or not cyrillic_probe_due():" in source
