"""Обёртка вокруг запуска не выдаётся за сам переводчик.

`running_pid()` приходится искать живой процесс по /proc: pid-файл живёт в
XDG_RUNTIME_DIR, то есть в tmpfs, и выносится вместе с logout или перезапуском
user manager, пока оверлей продолжает работать. Без этого скана состояние, из
которого нельзя выйти: `--toggle` не останавливает, потому что считает, что
ничего не запущено, и `--live` не стартует по той же причине.

Скан искал процесс, у которого «в аргументах есть kizurium и есть --live». Но
`timeout 30 kizurium-translator --live` - это argv `['/usr/bin/timeout', '30',
'.../kizurium-translator', '--live',...]`: и имя бинаря, и флаг в нём есть, а
самого переводчика там нет. Обёртка живёт всё время, пока работает переводчик, и
скан возвращал её. Итог: `--live` отказывался запускаться («уже запущен»), а
`--stop` останавливал обёртку вместо оверлея. Запустить live через `timeout`,
`nohup`, `env` или `bash -c` было нельзя вовсе.

Настоящий переводчик определяется по собственному исполняемому файлу, а не по
тому, что написано в аргументах.
"""

from __future__ import annotations

from kizurium_translator import cli

TRANSLATOR = "/home/u/.local/bin/kizurium-translator"


def test_the_translator_started_directly_is_a_live_session():
    assert cli._looks_like_live_session([TRANSLATOR, "--live"])
    assert cli._looks_like_live_session([TRANSLATOR, "--toggle"])
    assert cli._looks_like_live_session([TRANSLATOR, "--live", "-g", "0,0 800x600"])


def test_the_translator_in_another_mode_is_not():
    assert not cli._looks_like_live_session([TRANSLATOR, "--status"])
    assert not cli._looks_like_live_session([TRANSLATOR, "--doctor"])


def test_timeout_wrapping_a_live_run_is_not_a_live_session():
    """Ровно то, что ловила прежняя проверка по содержимому аргументов.

    Обёртка живёт всё время, пока работает переводчик, и отвечает тем же
    содержимым аргументов - но останавливать её вместо оверлея бессмысленно.
    """
    argv = ["/usr/bin/timeout", "30", TRANSLATOR, "--live", "-g", "0,0 800x600"]
    assert any("kizurium" in a for a in argv), "обёртка даже не выглядит похожей"
    assert any("--live" in a for a in argv), "обёртка даже не содержит флага"
    assert not cli._looks_like_live_session(argv)


def test_other_wrappers_are_not_a_live_session():
    for wrapper in ("/usr/bin/nohup", "/usr/bin/env", "/usr/bin/bash", "/usr/bin/sh"):
        argv = [wrapper, TRANSLATOR, "--live"]
        assert not cli._looks_like_live_session(argv), wrapper


def test_a_shell_carrying_the_whole_command_line_is_not_a_live_session():
    """`bash -c "... --live"` - весь текст команды в одном аргументе.

    Наивная проверка по `argv[1:]` находит здесь и имя, и флаг, потому что они
    лежат в одной строке.
    """
    argv = ["/usr/bin/bash", "-c", "cd /p &&.venv/bin/kizurium-translator --live"]
    assert not cli._looks_like_live_session(argv)


def test_the_module_invocation_is_a_live_session():
    """Модуль всё ещё запускается напрямую, и скан обязан его узнавать."""
    assert cli._looks_like_live_session(
        ["/usr/bin/python3", "-m", "kizurium_translator.live", "--live"]
    )
    assert cli._looks_like_live_session(
        ["/usr/bin/python3", "-m", "kizurium_translator.live", "--geom", "0,0 8x8"]
    )
    # Голый модуль без --geom не сессия: модуль требует область и выходит с
    # ошибкой, так что процесса-оверлея за ним не остаётся.
    assert not cli._looks_like_live_session(["kizurium_translator.live"])
    assert cli._is_translator_command(["kizurium_translator.live"])


def test_the_module_is_recognised_as_our_executable():
    assert cli._is_translator_executable("kizurium_translator.live")
    assert cli._is_translator_executable(TRANSLATOR)
    assert not cli._is_translator_executable("/usr/bin/python3")
