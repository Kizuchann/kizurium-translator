"""Захват кадра при нескольких мониторах — по целевому воркспейсу.

Второй ноут подключается как headless-выход, и Hyprland перестаёт ставить
основной экран в начало координат: eDP-1 уезжает на x=3286, а новый выход
занимает 0..1920. Захват «0,0 1920x1080» после этого снимает чужой экран.

Первая правка брала монитор активного окна. Это тоже неверно: `screencheck`
переключает воркспейс и сразу снимает кадр, а фокус к этому моменту не
обязан переехать. На практике при проверке ws13 снимок приходил с первого
монитора, и в лог попадал текст чужого окна — «Context 296,754 tokens»,
`import json, sys`. Проверка второго монитора измеряла первый, и рапортовала
о нуле проблем.

У harness есть то, чего не было у первого варианта: он знает, какой
воркспейс проверяет. Монитор и берётся по нему — `hyprctl monitors` показывает
`activeWorkspace` каждого выхода.

Активное окно остаётся запасным путём: если у нужного воркспейса нет своего
монитора (пустой воркспейс, окно уехало), берём тот, где фокус.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SC = Path(__file__).resolve().parents[1] / "scripts" / "screencheck.py"


def _load():
    spec = importlib.util.spec_from_file_location("screencheck_mod", SC)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Второй монитор слева, основной уехал вправо — ровно то, что делает Hyprland
# после подключения headless-выхода. У каждого выхода своя активная рабочая
# область, и в этом суть: монитор ищется по номеру воркспейса, а не по фокусу.
MONITORS = [
    {
        "id": 1,
        "name": "VAIO",
        "x": 1920,
        "y": 0,
        "width": 1366,
        "height": 768,
        "activeWorkspace": {"id": 13, "name": "13"},
    },
    {
        "id": 0,
        "name": "eDP-1",
        "x": 3286,
        "y": 0,
        "width": 1920,
        "height": 1080,
        "activeWorkspace": {"id": 2, "name": "2"},
    },
]


class _FakeHyprctl:
    """Отвечает на `hyprctl monitors` и `hyprctl activewindow`."""

    def __init__(self, monitors, monitor_id=None):
        self._monitors = monitors
        self._monitor_id = monitor_id

    def __call__(self, argv, **kwargs):
        if "monitors" in argv:
            body = json.dumps(self._monitors)
        elif "activewindow" in argv:
            body = json.dumps({"monitor": self._monitor_id})
        else:
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, body, "")


@pytest.fixture
def patched(monkeypatch):
    mod = _load()
    return mod, monkeypatch


def test_geometry_comes_from_the_workspace_under_test(patched):
    """Проверяем ws13 — снимаем тот выход, на котором он лежит.

    Не тот, где фокус: сразу после переключения фокус может быть ещё на
    прошлом воркспейсе, и снимок приходит с чужого монитора.
    """
    mod, monkeypatch = patched
    # Фокус намеренно оставлен на первом мониторе: именно это и ломало замер.
    monkeypatch.setattr(mod.subprocess, "run", _FakeHyprctl(MONITORS, 0))
    assert mod._active_geometry(13) == "1920,0 1366x768"


def test_geometry_follows_the_workspace_when_it_moves_monitors(patched):
    """Воркспейсы меняются местами между выходами - меняются и кадры."""
    mod, monkeypatch = patched
    swapped = [
        dict(MONITORS[0], activeWorkspace={"id": 2, "name": "2"}),
        dict(MONITORS[1], activeWorkspace={"id": 13, "name": "13"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _FakeHyprctl(swapped, 0))
    assert mod._active_geometry(2) == "1920,0 1366x768"
    assert mod._active_geometry(13) == "3286,0 1920x1080"


def test_geometry_falls_back_to_focus_when_the_workspace_is_nowhere(patched):
    """Воркспейса нет ни на одном выходе — берём монитор активного окна."""
    mod, monkeypatch = patched
    empty = [dict(m, activeWorkspace={"id": 99, "name": "99"}) for m in MONITORS]
    monkeypatch.setattr(mod.subprocess, "run", _FakeHyprctl(empty, 1))
    assert mod._active_geometry(13) == "1920,0 1366x768"


def test_geometry_falls_back_to_first_output_as_a_last_resort(patched):
    """Ни воркспейса, ни фокуса — первый выход, а не выдуманные 0,0 1920x1080."""
    mod, monkeypatch = patched
    empty = [dict(m, activeWorkspace={"id": 99, "name": "99"}) for m in MONITORS]
    monkeypatch.setattr(mod.subprocess, "run", _FakeHyprctl(empty, None))
    assert mod._active_geometry(13) == "1920,0 1366x768"


def test_single_monitor_keeps_the_origin(patched):
    one = [
        {
            "id": 0,
            "name": "eDP-1",
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080,
            "activeWorkspace": {"id": 2, "name": "2"},
        }
    ]
    mod, monkeypatch = patched
    monkeypatch.setattr(mod.subprocess, "run", _FakeHyprctl(one, 0))
    assert mod._active_geometry(2) == "0,0 1920x1080"


def test_a_monitor_is_matched_by_id_not_by_name(patched):
    """У activewindow поле monitor — числовой id, а не имя выхода.

    Сопоставление по имени не находит ничего, и тогда остаётся откат на
    жёсткие координаты, то есть на чужой экран.
    """
    mod, monkeypatch = patched
    empty = [dict(m, activeWorkspace={"id": 99, "name": "99"}) for m in MONITORS]
    monkeypatch.setattr(mod.subprocess, "run", _FakeHyprctl(empty, 99))
    assert mod._active_geometry(13) == "1920,0 1366x768"
