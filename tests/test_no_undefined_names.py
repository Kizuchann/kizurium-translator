"""Ни одно имя в `src/` не должно быть необъявленным.

Что было сломано.

`live/build.py` звал `is_subtitle_junk_line`, а импортировал его соседние
имена из того же модуля - забытая строка в списке импорта. Модуль импортировался
без ошибки, тесты на форму проходили, и падение случалось в кадре: после
успешного OCR живой перевод выходил с `NameError` на первом же абзаце из двух
строк субтитров.

`translation/glossary.py` звал `_script_of` - приватное имя, которого в
проекте нет: публичное называется `script_of` и лежит в `core/scripts.py`.
Тот же класс ошибки, тот же зелёный прогон тестов.

Оба нашлись правилом F821 (`ruff check --select F821`), а не чтением: имя,
которое не импортировано и не объявлено, не видно ни в одном тесте, который
проверяет форму. Поэтому здесь F821 - часть тестов, а не только часть CI.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "kizurium_translator"


def _ruff() -> str:
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("ruff не установлен в этом окружении")
    return probe.stdout.strip()


def test_ruff_is_available():
    assert _ruff().startswith("ruff ")


def test_no_name_in_src_is_used_without_being_defined():
    """F821 по всему пакету.

    Пустой отчёт - условие. Иначе падение будет в кадре на реальном экране,
    а не здесь.
    """
    _ruff()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F821",
            "--no-cache",
            "--output-format",
            "concise",
            str(SRC),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, (
        "необъявленные имена в исходниках:\n" + proc.stdout.strip()
    )
