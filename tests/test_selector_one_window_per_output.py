"""Выделятель открывается на том экране, где нажали, а не на первом.

Свойства `cursorScreen` у синглтона `Quickshell` в 0.3.x нет: документированы
`screens`, `processId`, `clipboardText` и прочие. Старый код его всё равно читал,
получал `undefined` и откатывался на `Quickshell.screens[0]` - то есть выделятель
всегда вставал на первый подключённый выход, независимо от того, куда указывал
курсор.

С одним монитором это неразличимо. Со вторым выделятель уезжал на чужой экран при
каждом нажатии, и переводчик становился непригоден на второй половине рабочего
стола.

Решения здесь нет по построению: окно создаётся на каждый выход, и нажатие получает
то, чей выход занят курсором, - так протокол присылает `wl_surface.enter` окну,
зашедшему на выход. Тесты ниже проверяют именно эту конструкцию и то, что старый
откат на первый выход не вернулся.

ws1-ws25 и конкретные координаты здесь не имеют значения.
"""

from __future__ import annotations

from pathlib import Path

from kizurium_translator import selector

QML = Path(selector.__file__).parent / "qml" / "selector" / "ScreenshotOverlay.qml"


def _source() -> str:
    return QML.read_text(encoding="utf-8")


def _code() -> str:
    """The QML without whole-line comments.

    The history of both mistakes is written down in the comments next to the code
    that replaced them, so searching the raw text finds the very thing that must
    not come back. Commenting on a fix has to stay possible, so the check runs on
    code only - the same way this suite already checks that `grim` is not invoked.
    """
    return "\n".join(
        ln for ln in _source().splitlines() if not ln.lstrip().startswith("//")
    )


def test_a_window_is_created_for_every_output():
    """`Variants` по `Quickshell.screens` — официальный способ окна на каждый экран.

    Без него пришлось бы угадывать выход заранее, а угадывать нечем: см. следующий
    тест.
    """
    src = _source()
    assert "Variants {" in src
    assert "model: Quickshell.screens" in src
    # Каждое окно привязано к своему выходу, а не к общему выражению.
    assert "screen: modelData" in src
    assert "required property var modelData" in src


def test_the_removed_cursor_screen_property_is_not_read_back():
    """`Quickshell.cursorScreen` в 0.3.x не существует.

    Обращение к отсутствующему свойству даёт `undefined`, и следующая за ним `??`
    срабатывала всегда. Возвращать чтение несуществующего свойства нельзя даже
    «на всякий случай»: оно всегда даёт undefined, то есть всегда откатывается
    на первый выход.
    """
    assert "cursorScreen" not in _code()


def test_nothing_falls_back_to_the_first_connected_output():
    """Первый подключённый выход - это догадка, и она была исходной ошибкой.

    `screens[0]` стоял в откате на несуществующее cursorScreen и работал всегда,
    то есть выделятель всегда открывался на первом экране. Ни в каком виде эта
    подсказка возвращаться не должна. Одиночный монитор (`length === 1`) — другое:
    там единственный выход и есть тот, на котором восстанавливают область.
    """
    src = _code()
    assert "?? Quickshell.screens[0]" not in src
    assert "Quickshell.screens[0]" not in src
    # Bare screens[0] assignment is the multi-monitor footgun.
    assert "pick = screens[0]" not in src
    assert "screens.length === 1" in src


def test_only_the_window_that_owns_the_run_draws_the_selection():
    """Рамка и панель принадлежат одному выходу, иначе рисуются сразу везде."""
    src = _source()
    assert "function owns(scr)" in src
    # Рамка, панель и вырез из затемнения — только у владельца.
    assert "visible: root.owns(modelData) && (root.hasSelection || root.isSelecting)" in src
    assert (
        "visible: root.owns(modelData) && root.hasSelection && !root.isSelecting"
        in src
    )
    assert "visible: root.owns(modelData) && root.hasSelection" in src


def test_the_dim_layer_is_shown_before_any_output_is_owned():
    """Затемнение видно на всех экранах, пока никто не нажал.

    Иначе на экране, который ещё не выбран, не видно ничего, а значит и нажать
    некуда: выглядит как «выделятель не открылся», хотя он открыт.
    """
    src = _source()
    assert "visible: !root.hasSelection || !root.owns(modelData)" in src


def test_a_press_claims_the_output_it_happened_on():
    """Нажатие в окне и означает «я этот выход», и размеры берутся у окна.

    Размеры у окон разные — у 1920x1080 и у 1366x768, — и общие для всех рамки
    с ними не совпали бы.
    """
    src = _source()
    assert "root.claimScreen(modelData, width, height)" in src
    # Без владения нельзя ни двигать, ни отпускать рамку.
    assert "if (!root.owns(modelData)) { cursorShape = Qt.CrossCursor; return; }" in src
    assert "if (!root.owns(modelData)) return;" in src


def test_each_output_gets_its_own_layershell_namespace():
    """Слой с уже занятым namespace композитор не принимает.

    На одном namespace второе окно просто не появлялось - ровно тот случай, ради
    которого окно и создаётся на каждый выход, и он выглядел как «выделятель не
    открылся вовсе».
    """
    src = _source()
    assert '"kizurium-translator-selector-" + modelData.name' in src


def test_the_remembered_region_is_restored_once_per_output():
    """Область восстанавливается для того выхода, который завладел.

    Повторное восстановление выкинуло бы рамку, которую пользователь уже начал
    тянуть, а одна и та же область на другом выходе означает совсем другое
    место.
    """
    src = _source()
    assert "property var restoredFor: ({})" in src
    assert "if (restoredFor[key] !== true)" in src


def test_remembered_region_is_restored_on_startup_without_a_click():
    """Прошлая область должна быть на экране сразу, а не после тыка.

    Раньше restore жил только в claimScreen от onPressed — рамка появлялась
    лишь после клика, и казалось что «каждый раз выделяй заново».
    """
    src = _source()
    assert "function autoRestoreRemembered()" in src
    assert "Qt.callLater(autoRestoreRemembered)" in src
    prepare = src[src.index("function prepare()"):]
    prepare = prepare[: prepare.index("\n    function ")]
    assert "autoRestoreRemembered" in prepare
    # Нельзя снова угадывать screens[0] при нескольких мониторах без памяти.
    auto = src[src.index("function autoRestoreRemembered()"):]
    auto = auto[: auto.index("\n    function ")]
    assert "screens.length === 1" in auto
    assert "geometryFor(screens[i])" in auto
