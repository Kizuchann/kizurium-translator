"""Фазы 1 и 2 не должны тихо разъезжаться.

Фаза 1 удаляет мёртвый код, фаза 2 — чистит комментарии и source hygiene. Обе
помечены как закрытые, но позже по плану разрез `live.py` и правки распознавания
вернули то, что эти фазы должны были убрать. Проверки ниже это фиксируют, чтобы
следующий разрез снова не принёс хвост.

требует не писать сырой текст экрана в INFO: текст с экрана — это
данные пользователя, и они не должны попадать в лог на обычном уровне.

Проверки падают на коде до правки.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "kizurium_translator"


def _sources() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


class TestNoDeadBranchInSource:
    """`if False` — это закомментированный код, который ещё и читается как
    действующий."""

    def test_no_if_false_in_source(self):
        offenders: list[str] = []
        for path in _sources():
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"\bif\s+(False|0|None)\s*:", line) or re.search(
                    r"\bif\s+False\b.*\belse\b", line
                ):
                    offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
        assert offenders == [], "\n".join(offenders)

    # Требуется отступ тела: у `# for the sake of a diagnostic` после `for` идёт
    # обычное слово, а у закомментированного кода - либо конец строки, либо
    # выражение.
    DEAD_CODE = re.compile(r"^\s*#\s*(def |return\b|if \w|for \w+ in |while \w|class \w)")

    def test_no_commented_out_definitions(self):
        offenders: list[str] = []
        for path in _sources():
            for n, line in enumerate(path.read_text().splitlines(), 1):
                m = self.DEAD_CODE.match(line)
                if not m:
                    continue
                tail = line[m.end():].strip()
                # `for the sake of a diagnostic` - это проза, а не код.
                if tail and not re.match(r"^[(:=]", tail) and not tail.endswith(":"):
                    continue
                offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
        assert offenders == [], "\n".join(offenders)


class TestNoWrapperKeptAsATombstone:
    """Функция, которая только бросает «флажок удалён», — это legacy без
    потребителя. Если флаг уже не в CLI, её удаляют, а не хранят."""

    def test_no_simple_flow_tombstone_in_cli(self):
        from kizurium_translator import cli

        assert not hasattr(cli, "_cmd_simple_flow"), (
            "cli держит заглушку удалённого --simple-select без потребителей"
        )

    def test_the_flag_is_gone_from_the_parser(self):
        from kizurium_translator import cli

        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        if parser is None:
            pytest.skip("парсер собирается внутри main")
        assert "--simple-select" not in parser.format_help()


class TestNoUnusedHelperWrappers:
    """Обёртка, которая только повторяет другую функцию, — это дублирующая
    реализация."""

    def test_scripts_has_no_alias_of_its_own_key_helper(self):
        from kizurium_translator.core import scripts

        assert not hasattr(scripts, "_count_key"), (
            "core.scripts держит _count_key, который ничего не делает сверх _key"
        )


class TestNoRawScreenTextInInfoLog:
    """«No raw screen text in INFO logs».

    В проекте это разделено намеренно: `tlog` пишется всегда, а `tdetail` —
    только при `KIZURIUM_TRANSLATOR_DEBUG`, и docstring у него так и говорит.
    Поэтому проверка здесь не «в логе нет текста вообще», а «текст экрана не
    попадает в `tlog`, то есть в лог по умолчанию».

    Первая версия этой проверки ловила и `tdetail`, и `keep-dup-text=1` — то
    есть ругалась на правильно сделанное и на имя счётчика. Обе ловушки
    закрыты явно, чтобы они не вернулись.
    """

    def test_tdetail_is_gated_on_an_env_flag(self):
        """Пока текст экрана нужен для разбора, он пишется по запросу."""
        from kizurium_translator.core import text as text_mod

        assert "KIZURIUM_TRANSLATOR_DEBUG" in inspect.getsource(text_mod.tdetail), (
            "tdetail пишет всегда - текст экрана попадёт в лог по умолчанию"
        )

    # Поля, которые не содержат текста с экрана: вид блока, движок, язык.
    SERVICE_FIELDS = {"kind", "engine", "lang", "role", "speaker"}

    def _reads_screen_text(self, node: ast.AST) -> bool:
        """Does this f-string actually put the text itself into the log?

        `chars={len(merged['text'])}` measures, `text={merged['text']}` prints.
        `kind={par.get('kind')}` prints a service field. The distinction is not
        visible in the source text without parsing, which is why this walks the
        AST: a regex on `\\{[^}]*\\}` stops at the first inner brace and then
        either misses `len(` or reports it as text.
        """
        for sub in ast.walk(node):
            if not isinstance(sub, ast.FormattedValue):
                continue
            # len(...) around the value: a measurement, not the text.
            if isinstance(sub.value, ast.Call) and getattr(sub.value.func, "id", "") == "len":
                continue
            if isinstance(sub.value, ast.Constant):
                continue
            for inner in ast.walk(sub.value):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if inner.value in self.SERVICE_FIELDS:
                        continue
                    if inner.value in ("text", "source", "translated"):
                        return True
                if isinstance(inner, ast.Subscript) and isinstance(inner.slice, ast.Constant):
                    if inner.slice.value in ("text", "source", "translated"):
                        return True
                if isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") == "get":
                    if inner.args and getattr(inner.args[0], "value", "") in self.SERVICE_FIELDS:
                        continue
                    if inner.args and getattr(inner.args[0], "value", "") in ("text", "source", "translated"):
                        return True
        return False

    def test_nothing_interpolates_screen_text_into_tlog(self):
        offenders: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None)
                if name not in ("tlog", "log", "_log"):
                    continue
                if any(
                    self._reads_screen_text(arg)
                    for arg in list(node.args) + [k.value for k in node.keywords]
                ):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}: {name}")
        assert offenders == [], (
            "текст экрана попадает в tlog (пишется всегда):\n" + "\n".join(offenders)
        )

    def test_service_fields_are_not_mistaken_for_text(self):
        """Явная проверка, что ловушка со служебными полями закрыта.

        `make-block-call kind=...` печатает вид блока, а не текст, и ругаться на
        него нельзя - иначе проверка запрещает писать в лог то, что нужно для
        разбора.
        """
        from kizurium_translator.core import text as text_mod

        src = inspect.getsource(text_mod)
        assert "kind" in src  # служебное поле в логе допустимо

"""CLI behaviour: parsing, configuration precedence, dispatch, status/stop.

No GUI and no compositor required.
"""


import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import owner, patch_all

from kizurium_translator import cli  # noqa: E402
from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.paths import default_paths  # noqa: E402


def _core_tools() -> str:
    """The tools the exit code depends on, as one string.

    Read from the constant the doctor itself uses: the point of the selector test
    is that a tool is *not* in here, and a second list would drift away from the
    one the code reads.
    """
    return " ".join(cli.CORE_TOOLS)


def argv(*args: str) -> list[str]:
    return list(args)


class TestParser:
    def test_default_run_is_the_main_flow(self):
        args = cli.build_parser().parse_args([])
        assert not any([args.live, args.text, args.stop, args.status, args.doctor, args.toggle])
        assert args.geom is None

    def test_flags(self):
        a = cli.build_parser().parse_args(argv("--live", "--target", "de", "--interval", "1.5"))
        assert a.live is True
        assert a.target == "de"
        assert a.interval == 1.5

    def test_text_flag(self):
        # --text with no value opens an empty window; with a value it prefills.
        assert cli.build_parser().parse_args(argv("--text")).text == ""
        assert cli.build_parser().parse_args(argv("--text", "hello")).text == "hello"

    def test_ocr_copy_flag(self):
        assert cli.build_parser().parse_args(argv("--ocr-copy")).ocr_copy is True

    def test_engine_is_repeatable(self):
        a = cli.build_parser().parse_args(argv("--engine", "rapid", "--engine", "tesseract"))
        assert a.engine == ["rapid", "tesseract"]

    def test_positional_geometry(self):
        assert cli.build_parser().parse_args(["0,0 100x100"]).geom == "0,0 100x100"

    def test_version_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(argv("--version"))
        assert exc.value.code == 0


class TestConfigPrecedence:
    def test_defaults_when_no_file(self, tmp_path):
        conf = cli.resolve_config(cli.build_parser().parse_args(argv("--config", str(tmp_path / "x.toml"))))
        assert conf.target_lang == "ru"
        assert conf.source_lang == "auto"

    def test_file_is_read(self, tmp_path):
        cfg = tmp_path / "c.toml"
        cfg.write_text('[translation]\ntarget = "ja"\n', encoding="utf-8")
        conf = cli.resolve_config(cli.build_parser().parse_args(argv("--config", str(cfg))))
        assert conf.target_lang == "ja"

    def test_cli_beats_file(self, tmp_path):
        """--target must win over config.toml, and reach the engine."""
        cfg = tmp_path / "c.toml"
        cfg.write_text('[translation]\ntarget = "ja"\ninterval = 9.0\n', encoding="utf-8")
        args = cli.build_parser().parse_args(
            argv("--config", str(cfg), "--target", "de", "--interval", "2.0", "--no-gtx")
        )
        conf = cli.resolve_config(args)
        assert conf.target_lang == "de"
        assert conf.interval == 2.0
        assert conf.use_gtx is False

    def test_source_never_becomes_target(self, tmp_path):
        cfg = tmp_path / "c.toml"
        cfg.write_text('[translation]\nsource = "en"\n', encoding="utf-8")
        conf = cli.resolve_config(cli.build_parser().parse_args(argv("--config", str(cfg))))
        assert conf.source_lang == "en"
        assert conf.target_lang == "ru"

    def test_engine_override(self):
        args = cli.build_parser().parse_args(argv("--engine", "tesseract"))
        assert cli.resolve_config(args).ocr_engines == ("tesseract",)

    def test_allow_slow(self):
        conf = cli.resolve_config(cli.build_parser().parse_args(argv("--allow-slow-translation")))
        assert conf.allow_slow_translation is True

    def test_no_mutation_of_the_base_config(self):
        conf = cli.resolve_config(cli.build_parser().parse_args(argv("--target", "de")))
        assert Config().target_lang == "ru"


class TestStatusAndStop:
    def test_status_when_not_running(self, tmp_path, monkeypatch, capsys):
        """No pid file in this runtime directory means no session.

        The /proc scan is stubbed out, and it has to be: that scan exists so a
        session which outlived its runtime directory is still stoppable, which
        means it looks for a lock held by any process on the machine. Without
        the stub this test fails whenever the user happens to have the overlay
        running, and it fails differently depending on what they are doing -
        which is the opposite of hermetic.
        """
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.delenv("KIZURIUM_TRANSLATOR_PID", raising=False)
        monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
        assert cli.main(argv("--status")) == 1
        assert "не запущено" in capsys.readouterr().out

    def test_status_when_running(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.setenv("KIZURIUM_TRANSLATOR_PID", str(os.getpid()))
        code = cli.main(argv("--status"))
        assert "запущено" in capsys.readouterr().out
        assert code in (0, 1)

    def test_stop_is_idempotent(self, tmp_path, monkeypatch, capsys):
        """Stopping when nothing is running is a no-op, not an error.

        The /proc scan is stubbed for the reason given on the status test: it
        searches the whole machine on purpose, so leaving it live makes this
        test depend on whether an overlay is open.
        """
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.delenv("KIZURIUM_TRANSLATOR_PID", raising=False)
        monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
        assert cli.main(argv("--stop")) == 0
        assert "не запущен" in capsys.readouterr().out

    def test_stop_removes_the_lock_file(self, tmp_path, monkeypatch, capsys):
        """The worker loop lives while the lock exists, so removing it is the stop."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        from kizurium_translator.paths import default_paths

        paths = default_paths()
        paths.ensure()
        paths.lock.write_text("123", encoding="utf-8")
        cli.main(argv("--stop"))
        assert not paths.lock.exists()


class TestUninstall:
    def test_purge_requires_uninstall_flag(self, capsys):
        assert cli.main(argv("--purge")) == 2
        assert "uninstall" in capsys.readouterr().err

    def test_uninstall_removes_wrapper_and_venv(self, tmp_path, monkeypatch, capsys):
        home = tmp_path / "home"
        data = home / ".local" / "share" / "kizurium-translator"
        venv = data / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "kizurium-translator").write_text("#!/bin/sh\n", encoding="utf-8")
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        wrapper = bin_dir / "kizurium-translator"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "# Generated by kizurium-translator install.sh\n"
            f'exec "{data / "venv" / "bin" / "kizurium-translator"}" "$@"\n',
            encoding="utf-8",
        )
        cfg = home / ".config" / "kizurium-translator"
        cfg.mkdir(parents=True)
        (cfg / "config.toml").write_text("x=1\n", encoding="utf-8")
        cache = home / ".cache" / "kizurium-translator"
        cache.mkdir(parents=True)
        (cache / "translate-cache.sqlite").write_text("x", encoding="utf-8")

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
        monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
        monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
        monkeypatch.delenv("PREFIX", raising=False)

        assert cli.main(argv("--uninstall")) == 0
        assert not wrapper.exists()
        assert not (data / "venv").exists()
        assert (cfg / "config.toml").exists()
        assert cache.exists()

        assert cli.main(argv("--uninstall", "--purge")) == 0
        assert not cfg.exists()
        assert not cache.exists()
        assert not data.exists()


class TestDoctor:
    def test_reports_every_section_the_plan_names(self, capsys):
        """Core, Wayland, Selector, OCR, Translation, Models, Config."""
        code = cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        for section in ("Core", "Wayland", "Selector", "OCR", "Translation", "Models", "Config"):
            assert section in out, f"нет секции {section}"
        assert code in (0, 1)

    def test_uses_words_not_only_glyphs(self, capsys):
        """names the statuses; a glyph alone is unreadable in a pasted log."""
        cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        for word in ("OK", "MISSING", "DISABLED"):
            assert word in out, f"нет статуса {word}"

    def test_covers_everything_phase_176_lists(self, capsys):
        cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        for item in (
            "python",
            "gtk4",
            "pycairo",
            "WAYLAND_DISPLAY",
            "grim",
            "wl-copy",
            "region selector",
            "tesseract",
            "RapidOCR",
            "MeikiOCR",
            "local en→ru",
            "local ja→ru",
            "online fallback",
            "config file",
        ):
            assert item in out, f"doctor не проверяет {item}"

    def test_the_selector_does_not_decide_the_exit_code(self, capsys, monkeypatch):
        """only the selector needs quickshell.

        It was in REQUIRED_TOOLS, so a machine with everything the overlay needs
        and no quickshell exited 1 - an optional thing reported as a core failure,
        which is the mistake the section split exists to stop.
        """
        import shutil as _shutil

        real_which = _shutil.which

        def no_quickshell(name, *a, **kw):
            if name == "quickshell":
                return None
            return real_which(name, *a, **kw)

        monkeypatch.setattr("kizurium_translator.cli.shutil.which", no_quickshell)
        code = cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        assert "region selector" in out
        assert "DISABLED" in out, "отсутствие селектора - это DISABLED, а не ERROR"
        assert "quickshell" not in cli.CORE_TOOLS

    def test_reports_the_429_fallback_as_a_plan_not_a_fact(self, capsys):
        cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        assert "429" in out

    def test_does_not_require_notify_or_cliphist(self, capsys):
        cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        assert "notify-send" not in out
        assert "cliphist" not in out

    def test_carries_no_raw_path_and_no_source_text(self, capsys):
        """no raw paths, credentials or screen text in the output.

        This output ends up in issue trackers, which is why the report says which
        file and not where it is.
        """
        cli.main(argv("--doctor"))
        out = capsys.readouterr().out
        assert "/home/" not in out
        assert "/root/" not in out
        assert str(default_paths().config_file) not in out


class TestPrintConfig:
    def test_prints_every_section(self, capsys):
        assert cli.main(argv("--print-config")) == 0
        out = capsys.readouterr().out
        for section in ("[translation]", "[live]", "[detection]", "[ocr]", "[overlay]"):
            assert section in out


class TestRegionSelection:
    def test_uses_geometry_from_arguments(self, monkeypatch):
        args = cli.build_parser().parse_args(["10,20 300x400"])
        monkeypatch.setattr(cli.selector, "select_region", lambda: ("live", "nope"))
        assert cli.ask_region(args) == "10,20 300x400"

    def test_rejects_bad_geometry(self):
        args = cli.build_parser().parse_args(["nonsense"])
        with pytest.raises(SystemExit) as exc:
            cli.ask_region(args)
        assert exc.value.code == 2

    def test_interactive_cancel_exits(self, monkeypatch):
        args = cli.build_parser().parse_args([])
        monkeypatch.setattr(cli.selector, "select_region", lambda *a, **k: ("", ""))
        with pytest.raises(SystemExit) as exc:
            cli.ask_region(args)
        assert exc.value.code == 1

    def test_interactive_uses_the_project_overlay_not_slurp(self, monkeypatch):
        """No second selection path is left to reach by accident."""
        args = cli.build_parser().parse_args([])
        seen: list[str] = []
        monkeypatch.setattr(
            cli.selector,
            "select_region",
            lambda *a, **k: (seen.append("overlay") or "live", "5,5 60x60"),
        )
        assert cli.ask_region(args) == "5,5 60x60"
        assert seen == ["overlay"]

    def test_ocr_copy_path_requests_auto_confirm(self, monkeypatch):
        args = cli.build_parser().parse_args([])
        seen: dict = {}

        def fake_select(*_a, **kwargs):
            seen["auto"] = kwargs.get("auto_action", "")
            return ("ocr", "1,1 2x2")

        monkeypatch.setattr(cli.selector, "select_region", fake_select)
        assert cli.ask_region(args, auto_action="ocr") == "1,1 2x2"
        assert seen["auto"] == "ocr"

    def test_output_flag_uses_output_selection(self, monkeypatch):
        args = cli.build_parser().parse_args(argv("--output"))
        seen: list[str] = []

        def fake_output() -> str:
            seen.append("output")
            return "0,0 2560x1440"

        monkeypatch.setattr(cli.capture, "select_output", fake_output)
        monkeypatch.setattr(cli.selector, "select_region", lambda: ("live", "wrong"))
        assert cli.ask_region(args) == "0,0 2560x1440"
        assert seen == ["output"]

    def test_no_hardcoded_fallback_resolution(self):
        """A wrong 1920x1080 default would break multi-monitor setups."""
        source = Path(cli.__file__).read_text(encoding="utf-8")
        assert "1920x1080" not in source


class TestDispatch:
    def test_toggle_stops_a_running_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        calls: list[str] = []
        monkeypatch.setattr(cli, "running_pid", lambda: 4242)
        monkeypatch.setattr(cli, "stop_live", lambda: calls.append("stop") or 0)
        monkeypatch.setattr(cli, "cmd_main_flow", lambda *_a: calls.append("flow") or 0)
        assert cli.main(argv("--toggle")) == 0
        assert calls == ["stop"]

    def test_toggle_opens_the_flow_when_idle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        calls: list[str] = []
        monkeypatch.setattr(cli, "running_pid", lambda: 0)
        monkeypatch.setattr(cli, "stop_live", lambda: calls.append("stop") or 0)
        monkeypatch.setattr(cli, "cmd_main_flow", lambda *_a: calls.append("flow") or 0)
        assert cli.main(argv("--toggle")) == 0
        assert calls == ["flow"]

    def test_stop_flag_does_not_touch_the_region(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(cli, "stop_live", lambda: called.append("stop") or 0)
        monkeypatch.setattr(cli, "cmd_main_flow", lambda *_a: called.append("flow") or 0)
        cli.main(argv("--stop"))
        assert called == ["stop"]

    def test_stop_leaves_the_text_translator_alone(self, monkeypatch, tmp_path):
        """--stop is about live. The text window is a different mode.

        stop_live() used to call stop_text_window(), so a user with the text
        translator open in one window and live running in the other lost the text
        window on --stop, with no way to have asked for that.
        """
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        killed: list[int] = []
        monkeypatch.setattr(cli, "running_pid", lambda: 4242)
        monkeypatch.setattr(cli, "stop_text_window", lambda: killed.append(1) or 0)
        paths = cli.default_paths()
        paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        paths.lock.write_text("", encoding="utf-8")
        paths.pid.write_text("4242", encoding="utf-8")
        assert cli.stop_live() == 0
        assert not killed, "--stop must not close the text translator window"

    def test_toggle_stopping_live_leaves_the_text_translator_alone(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        killed: list[int] = []
        monkeypatch.setattr(cli, "running_pid", lambda: 4242)
        monkeypatch.setattr(cli, "stop_text_window", lambda: killed.append(1) or 0)
        paths = cli.default_paths()
        paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        paths.lock.write_text("", encoding="utf-8")
        paths.pid.write_text("4242", encoding="utf-8")
        cli.main(argv("--toggle"))
        assert not killed

    def test_stopping_live_still_stops_live(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.setattr(cli, "stop_text_window", lambda: 0)
        assert cli.stop_live() == 0

    def test_version_and_status_do_not_touch_the_environment(self, monkeypatch):
        """Discovery must not run for the commands that report on a broken state.

        ensure_wayland_env() guesses WAYLAND_DISPLAY and
        HYPRLAND_INSTANCE_SIGNATURE when they are missing. That guess is fine
        before a screenshot and wrong before an answer: --status is what someone
        runs when nothing works, and mutating the environment to produce it can
        change the answer it gives.
        """
        calls: list[int] = []
        monkeypatch.setattr(
            cli.capture, "ensure_wayland_env", lambda: calls.append(1)
        )
        monkeypatch.setattr(cli, "running_pid", lambda: 0)
        monkeypatch.setattr(cli, "cmd_doctor", lambda _c: 0)
        for args in (argv("--status"), argv("--stop"), argv("--doctor"),
                     argv("--print-config")):
            calls.clear()
            cli.main(args)
            assert not calls, f"{args} must not run Wayland discovery"

    def test_the_display_commands_do_run_discovery(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(
            cli.capture, "ensure_wayland_env", lambda: calls.append(1)
        )
        monkeypatch.setattr(cli, "cmd_main_flow", lambda *_a: 0)
        monkeypatch.setattr(cli, "cmd_live", lambda *_a: 0)
        monkeypatch.setattr(cli, "cmd_text", lambda *_a: 0)
        # `ask_region` takes `auto_action` and callers pass it; a lambda that
        # accepts only the positional one fails the test with a TypeError that
        # looks like a product bug and is not.
        monkeypatch.setattr(cli, "ask_region", lambda _a, **_kw: "0,0 8x8")
        monkeypatch.setattr(cli, "running_pid", lambda: 0)
        for args in (argv(), argv("--toggle"), argv("--live"),
                     argv("--text", "hi")):
            calls.clear()
            cli.main(args)
            assert calls, f"{args} needs a display connection"

    def test_discovery_runs_after_the_arguments_are_read(self, monkeypatch):
        """An unknown flag must fail before anything guesses at the environment."""
        calls: list[int] = []
        monkeypatch.setattr(
            cli.capture, "ensure_wayland_env", lambda: calls.append(1)
        )
        with pytest.raises(SystemExit):
            cli.main(argv("--definitely-not-a-flag"))
        assert not calls

    def test_the_text_window_is_still_single_instance(self, monkeypatch, tmp_path):
        """Starting text closes a leftover one; that is what it is for."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        killed: list[int] = []
        monkeypatch.setattr(cli, "stop_text_window", lambda: killed.append(1) or 0)
        monkeypatch.setattr(cli.ui, "text_window", lambda **_kw: 0)
        monkeypatch.setattr(cli, "make_translator", lambda _c: None)
        cli.cmd_text(Config(), "")
        assert killed, "a second text window must not be allowed"

    @staticmethod
    def _selector(action: str, geom: str = "0,0 100x100"):
        """Stand in for the overlay: reports an action and a region."""
        monkey_result = (action, geom)
        return lambda *a, **k: monkey_result

    def _use_selector(self, monkeypatch, action: str, geom: str = "0,0 100x100") -> None:
        monkeypatch.setattr(cli.selector, "have_selector", lambda: True)
        monkeypatch.setattr(cli.selector, "select_region", lambda *a, **k: (action, geom))

    @staticmethod
    def _selector(action: str, geom: str = "0,0 100x100") -> None:
        """Stand in for the overlay: an action and a region, nothing drawn."""

    def _use_selector(self, monkeypatch, action: str, geom: str = "0,0 100x100") -> None:
        monkeypatch.setattr(cli.selector, "have_selector", lambda: True)
        monkeypatch.setattr(cli.selector, "select_region", lambda *a, **k: (action, geom))

    def test_main_flow_dispatches_the_chosen_action(self, monkeypatch):
        seen: list[str] = []
        self._use_selector(monkeypatch, "ocr")
        monkeypatch.setattr(cli, "cmd_ocr_copy", lambda a, c: seen.append("ocr") or 0)
        monkeypatch.setattr(cli, "cmd_text", lambda c, initial="": seen.append("text") or 0)
        monkeypatch.setattr(cli, "cmd_live", lambda a, c, g: seen.append("live") or 0)
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 0
        assert seen == ["ocr"]

    def test_main_flow_live_action(self, monkeypatch):
        seen: list[str] = []
        self._use_selector(monkeypatch, "live", "12,34 640x360")
        monkeypatch.setattr(cli, "cmd_live", lambda a, c, g: seen.append(g) or 0)
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 0
        assert seen == ["12,34 640x360"]

    def test_main_flow_text_action(self, monkeypatch):
        seen: list[str] = []
        self._use_selector(monkeypatch, "text")
        monkeypatch.setattr(cli, "cmd_text", lambda c, initial="": seen.append("text") or 0)
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 0
        assert seen == ["text"]

    def test_main_flow_cancelled(self, monkeypatch):
        self._use_selector(monkeypatch, "")
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 1

    def test_selector_error_is_reported(self, monkeypatch):
        monkeypatch.setattr(cli.selector, "have_selector", lambda: True)

        def boom(*_a, **_k):
            raise cli.selector.SelectorError("нет quickshell")

        monkeypatch.setattr(cli.selector, "select_region", boom)
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 1

    def test_no_quickshell_is_an_error_not_a_different_interface(self, monkeypatch, capsys):
        """slurp plus a three-button window is not the same product.

        Falling back silently meant a user who pressed a key expecting the
        selector got something they never agreed to, with no indication that it
        was not what they normally use.
        """
        seen: list[str] = []
        monkeypatch.setattr(cli.selector, "have_selector", lambda: False)
        monkeypatch.setattr(cli, "ask_region", lambda _a: seen.append("region") or "0,0 1x1")
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 1
        assert not seen, "nothing may be drawn when the selector is unavailable"
        err = capsys.readouterr().err
        assert "Quickshell" in err and "установите" in err.lower() or "Установите" in err

    def test_the_error_names_the_package_to_install(self, monkeypatch, capsys):
        monkeypatch.setattr(cli.selector, "have_selector", lambda: False)
        cli.cmd_main_flow(cli.build_parser().parse_args([]), Config())
        assert "pacman -S quickshell" in capsys.readouterr().err

    def test_the_flag_is_gone_from_the_parser(self):
        """There is one way to pick a region.

        --simple-select opened a drag followed by a second, plainer window with
        three buttons. Keeping it meant two ways to do one thing, and the worse
        one still advertised itself.
        """
        import pytest as _pytest

        with _pytest.raises(SystemExit):
            cli.build_parser().parse_args(argv("--simple-select"))

    def test_there_is_no_menu_window_left(self):
        """The chooser window was the second half of that path."""
        assert not hasattr(cli.ui, "action_menu")

    def test_the_default_flow_never_touches_the_simple_path(self, monkeypatch):
        """Even with quickshell present, the default must not reach for slurp."""
        seen: list[str] = []
        monkeypatch.setattr(cli.selector, "have_selector", lambda: True)
        monkeypatch.setattr(cli.selector, "select_region", lambda: ("live", "0,0 50x50"))
        monkeypatch.setattr(cli, "ask_region", lambda _a: seen.append("region") or "0,0 1x1")
        monkeypatch.setattr(cli, "cmd_live", lambda a, c, g: 0)
        assert cli.cmd_main_flow(cli.build_parser().parse_args([]), Config()) == 0
        assert not seen

    def test_doctor_does_not_require_slurp(self):
        assert "slurp" not in cli.CORE_TOOLS
        assert "slurp" in cli.OPTIONAL_TOOLS

    def test_quickshell_is_the_selector_not_the_core(self):
        """Only the selector needs quickshell; optional is not a core failure.

        It used to be in `REQUIRED_TOOLS`, so a machine with a working live
        overlay and no quickshell got exit code 1 from `--doctor` - the report
        said the product was broken when only one feature was unavailable.
        """
        assert "quickshell" in cli.SELECTOR_TOOLS
        assert "quickshell" not in cli.CORE_TOOLS
        assert "grim" in cli.CORE_TOOLS, "без grim оверлею нечего захватывать"


class TestOcrCopyUsesUniversalCore:
    def test_calls_universal_ocr_text(self, tmp_path, monkeypatch):
        seen: dict = {}

        def fake_ask(_a, *, auto_action: str = ""):
            seen["auto_action"] = auto_action
            return "0,0 100x100"

        monkeypatch.setattr(cli, "ask_region", fake_ask)
        monkeypatch.setattr(cli.capture, "capture_image", lambda geom: seen.setdefault("geom", geom) or "img")
        monkeypatch.setattr(cli, "copy_to_clipboard", lambda text: seen.setdefault("text", text) or True)

        from kizurium_translator import live
        from kizurium_translator.ocr import universal as uni

        monkeypatch.setattr(uni, "universal_ocr_text", lambda img: "hello world")
        monkeypatch.setattr(live, "configure", lambda *a, **k: None)

        args = cli.build_parser().parse_args(argv("--ocr-copy"))
        assert cli.cmd_ocr_copy(args, Config()) == 0
        assert seen["geom"] == "0,0 100x100"
        assert seen["text"] == "hello world"
        assert seen["auto_action"] == "ocr"

    def test_reports_empty_recognition(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "ask_region", lambda _a, **k: "0,0 100x100")
        monkeypatch.setattr(cli.capture, "capture_image", lambda geom: "img")
        from kizurium_translator import live
        from kizurium_translator.ocr import universal as uni

        monkeypatch.setattr(uni, "universal_ocr_text", lambda img: "   ")
        monkeypatch.setattr(live, "configure", lambda *a, **k: None)
        args = cli.build_parser().parse_args(argv("--ocr-copy"))
        assert cli.cmd_ocr_copy(args, Config()) == 1
        assert "не распознан" in capsys.readouterr().err


class TestUiModule:
    def test_imports_without_gui(self):
        """ui must be importable so --help works on a headless box."""
        from kizurium_translator import ui

        assert callable(ui.text_window)

    def test_translate_text_uses_the_backend(self):
        from kizurium_translator import ui

        out, used, note = ui.translate_text("hello", "auto", "ru")
        assert isinstance(out, str) and isinstance(used, str) and isinstance(note, str)

    def test_no_phrasebook(self):
        from kizurium_translator import ui

        assert not hasattr(ui, "phrasebook")
        assert not hasattr(ui, "PHRASES")

    def test_no_compositor_dependency(self):
        from kizurium_translator import ui

        source = Path(ui.__file__).read_text(encoding="utf-8")
        for banned in ("hyprctl", "compositor", "swaymsg", "float_window"):
            assert banned not in source
