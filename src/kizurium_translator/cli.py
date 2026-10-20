"""Command line interface.

    kizurium-translator            select a region, then pick an action
    kizurium-translator --toggle   same, but a running live session is stopped
    kizurium-translator --live     start live translation directly
    kizurium-translator --ocr-copy copy the recognised text to the clipboard
    kizurium-translator --text     open the text translator window
    kizurium-translator --stop     stop the live session
    kizurium-translator --status   report whether it is running
    kizurium-translator --doctor   check the environment
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import capture, selector
from . import ui as ui
from .config import Config, config_path, load, provider_options
from .paths import default_paths
from .translate import Translator

GEOM_HELP = "screen region as 'x,y WxH'"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kizurium-translator",
        description="Translate text on screen: OCR a region and draw the translation on top of it.",
    )
    p.add_argument("geom", nargs="?", metavar="GEOM", help=f"region to use, {GEOM_HELP}")

    action = p.add_argument_group("actions")
    action.add_argument("--action", choices=("settings", "select-region", "show-window", "capture-window", "toggle-offline", "start-auto-translate", "stop-auto-translate", "quit"))
    action.add_argument("--toggle", action="store_true", help="region + menu, or stop live")
    action.add_argument("--live", action="store_true", help="start live translation")
    action.add_argument(
        "--ocr-copy",
        action="store_true",
        help="drag a region → OCR → clipboard (no toolbar, no translation)",
    )
    action.add_argument(
        "--text",
        nargs="?",
        const="",
        metavar="TEXT",
        help=(
            "open the text translator window; pass TEXT to prefill it "
            "(also reads the Wayland clipboard with --text-from-clipboard)"
        ),
    )
    action.add_argument(
        "--text-from-clipboard",
        action="store_true",
        help="prefill the text translator with the clipboard contents",
    )
    action.add_argument("--stop", action="store_true", help="stop the live session")
    action.add_argument("--status", action="store_true", help="report the live session state")
    action.add_argument("--doctor", action="store_true", help="check the environment")
    action.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the installed command + venv (works even if the git folder is gone)",
    )
    action.add_argument(
        "--purge",
        action="store_true",
        help="with --uninstall: also wipe config, cache, models, and logs",
    )
    action.add_argument(
        "--import-dictionary",
        metavar="PATH",
        help=(
            "import an XUnity/TENUKI-style dictionary into "
            "~/.local/share/kizurium-translator/dictionaries/ (user-provided only)"
        ),
    )
    action.add_argument(
        "--pack-id",
        metavar="ID",
        help="pack id for --import-dictionary (default: derived from the file name)",
    )
    action.add_argument(
        "--dict-format",
        choices=("auto", "xunity", "plain"),
        default="auto",
        help="dictionary format hint for --import-dictionary",
    )
    action.add_argument(
        "--tm-list",
        action="store_true",
        help="list recent translation-memory entries (not the glossary)",
    )
    action.add_argument(
        "--tm-promote",
        metavar="TEXT",
        help="promote one translation-memory entry into a user glossary pack",
    )
    action.add_argument(
        "--tm-source-lang",
        default="en",
        metavar="LANG",
        help="source language for --tm-promote (default: en)",
    )
    action.add_argument(
        "--packs",
        action="store_true",
        help="list offline language packs (Installed / Download)",
    )
    action.add_argument(
        "--pack-install",
        metavar="ID",
        help="install offline pack by id, or 'all' for every catalog pack",
    )
    action.add_argument(
        "--model-status",
        action="store_true",
        help="OCR engines and languages, Bergamot runtime and models, offline packs",
    )
    action.add_argument(
        "--bergamot-install",
        metavar="SRC-DST",
        help="download and verify a Bergamot model (e.g. en-ru; Mozilla repository, sha256-checked)",
    )
    action.add_argument(
        "--bergamot-runtime-install",
        action="store_true",
        help="prepare the Bergamot engine: own Python 3.10 + the bergamot wheel (needs uv and network)",
    )
    action.add_argument(
        "--bergamot-runtime-remove",
        action="store_true",
        help="remove the Bergamot engine environment (models stay)",
    )
    action.add_argument(
        "--bergamot-remove",
        metavar="SRC-DST",
        help="remove a Bergamot model from Kizurium's cache (Firefox files are never touched)",
    )
    action.add_argument(
        "--ocr-warmup",
        metavar="ENGINE",
        help="fetch the models of an installed OCR engine now (rapid)",
    )
    action.add_argument(
        "--from",
        dest="pack_from",
        metavar="PATH",
        help="local CT2 model dir or archive for --pack-install (optional if catalog has url/hf)",
    )

    region = p.add_argument_group("region")
    region.add_argument("-g", "--region", metavar="GEOM", help=GEOM_HELP)
    region.add_argument("-r", "--select", action="store_true", help="select a region with the mouse")
    region.add_argument("--output", action="store_true", help="select one whole output")

    opts = p.add_argument_group("options")
    opts.add_argument("-t", "--target", metavar="LANG", help="target language (default: ru)")
    opts.add_argument("-s", "--source", metavar="LANG", help="source language (default: auto)")
    opts.add_argument("-c", "--config", metavar="PATH", help="configuration file")
    opts.add_argument(
        "--profile",
        metavar="ID",
        default="",
        help="game profile id from data/profiles/ (or KIZURIUM_TRANSLATOR_PROFILE). "
             "Empty = universal mode; profiles are opt-in priors, not auto-detect",
    )
    opts.add_argument(
        "--list-profiles",
        action="store_true",
        help="list bundled game profiles and exit",
    )
    opts.add_argument("--engine", action="append", choices=("rapid", "meiki", "tesseract", "paddle"),
                      help="OCR engine order, repeatable")
    opts.add_argument("--interval", type=float, metavar="SEC", help="live re-OCR interval")
    opts.add_argument("--no-gtx", action="store_true", help="disable the direct translate endpoint")
    opts.add_argument(
        "--offline-only",
        action="store_true",
        help="strict offline: never create online backends (needs a local language pack)",
    )
    opts.add_argument("--allow-slow-translation", action="store_true",
                      help="also try the slow fallback backends")
    opts.add_argument("--no-translate", action="store_true", help="OCR only, no translation")
    opts.add_argument("--print-config", action="store_true", help="print the effective configuration")
    opts.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    return p


def _version() -> str:
    from . import __version__

    return __version__


def resolve_config(args: argparse.Namespace) -> Config:
    """Configuration file first, then the command line on top."""
    conf = load(args.config or config_path())
    changes: dict = {}
    if args.target:
        changes["target_lang"] = args.target
    if args.source:
        changes["source_lang"] = args.source
    if args.engine:
        changes["ocr_engines"] = tuple(args.engine)
    if args.interval is not None:
        changes["interval"] = args.interval
    if args.no_gtx:
        changes["use_gtx"] = False
    if getattr(args, "offline_only", False):
        changes["offline_only"] = True
    if args.allow_slow_translation:
        changes["allow_slow_translation"] = True
    profile = str(getattr(args, "profile", "") or "").strip()
    if profile:
        changes["profile_id"] = profile
    return replace(conf, **changes) if changes else conf


def make_translator(conf: Config) -> Translator:
    paths = default_paths()
    paths.ensure()
    return Translator(
        provider=conf.translation_provider,
        provider_options=provider_options(conf),
        request_interval=conf.request_interval,
        ocr_debounce=conf.ocr_debounce,
        model_path=conf.bergamot_model_path,
        target=conf.target_lang,
        source=conf.source_lang,
        use_gtx=conf.use_gtx,
        allow_slow=conf.allow_slow_translation,
        offline_only=conf.offline_only,
        max_chars=conf.max_translate_chars,
        cooldown_s=conf.gtx_cooldown_s,
        cache_path=paths.translate_cache,
        cache_max_entries=conf.cache_max_entries,
        glossary=dict(conf.glossary),
    )


# --------------------------------------------------------------- live state


def _pid_of(pid_file) -> int:  # noqa: ANN001
    """Pid recorded in ``pid_file``, 0 when absent or not a live process."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    if pid <= 0:
        return 0
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            if fh.read().rpartition(")")[2].split()[0] == "Z":
                return 0
        os.kill(pid, 0)
    except OSError:
        return 0
    return pid


def _alive(pid: int) -> bool:
    """Whether a pid is a live process. A zombie counts as gone.

    os.kill(pid, 0) succeeds for a zombie - a process that has exited but has not
    been reaped - so the state is read explicitly rather than inferred.
    """
    if pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            if fh.read().rpartition(")")[2].split()[0] == "Z":
                return False
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _scan_live_pid() -> int:
    """Find the live session by what it is, for when the pid file is gone.

    The pid file lives in the XDG runtime directory, and the runtime directory is
    tmpfs. Anything that clears it - a logout, a restarted user manager, a
    stale directory from a previous boot - takes the pid file with it while the
    overlay process itself keeps running. The result was a state you could not
    get out of: --toggle did not stop it, because the toggle believed nothing
    was running, and --live refused to start, for the same reason. The overlay
    stayed on screen, click-through, until something tore down the output.

    Reading /proc costs nothing and there is one process at most, so the pid
    file is an optimisation rather than the only way of knowing.
    """
    me = os.getpid()
    lock_name = default_paths().lock.name
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if not raw:
            continue
        parts = raw.split(b"\0")
        argv = [a.decode("utf-8", "replace") for a in parts if a]
        if not argv:
            continue
        # The held lock descriptor is the exact answer and does not care what
        # the process was invoked as, so it is checked first.
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    target = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                if target.rsplit("/", 1)[-1].startswith(lock_name):
                    return pid
        except OSError:
            pass
        if not any("kizurium" in a for a in argv):
            continue
        if _looks_like_live_session(argv) and _alive(pid):
            return pid
    return 0


# How the translator can be started. The console script is what the keybind runs;
# the module form `python -m kizurium_translator.live` still works too.
_TRANSLATOR_EXECUTABLES = frozenset(
    {"kizurium-translator", "kizurium_translator.live"}
)


def _is_translator_executable(argv0: str) -> bool:
    """Is this process the translator itself, rather than something wrapping it?

    Only the executable names it. Matching on the words in the arguments cannot
    tell them apart: `timeout 30 kizurium-translator --live` carries both the
    binary's name and the flag, and lives for as long as the overlay does, so the
    scan kept returning the wrapper - `--live` then refused to start ("уже
    запущен") and `--stop` stopped the wrapper instead of the overlay. Starting
    live through timeout, nohup, env or `bash -c` was impossible at all.
    """
    return os.path.basename(argv0) in _TRANSLATOR_EXECUTABLES


def _is_translator_command(argv: list[str]) -> bool:
    """Is this command line ours, however it was invoked?

    Two supported shapes: the console script, and `python -m
    kizurium_translator.live`, which the module still supports. A wrapper is
    neither, even when it carries the whole command line inside one argument.
    """
    if not argv:
        return False
    if _is_translator_executable(argv[0]):
        return True
    # python -m <module>: the executable is the interpreter, and the module is
    # named by an argument of its own rather than buried inside a string.
    return (
        os.path.basename(argv[0]).startswith("python")
        and len(argv) >= 3
        and argv[1] == "-m"
        and argv[2] in _TRANSLATOR_EXECUTABLES
    )


def _looks_like_live_session(argv: list[str]) -> bool:
    """Is this command line a live translation session of ours?"""
    if not _is_translator_command(argv):
        return False
    return any(
        a in ("--live", "--toggle") or "kizurium_translator.live" in a for a in argv[1:]
    )


def running_pid() -> int:
    """Pid of the live session, or 0.

    The pid file first, because it is exact. A scan of /proc only when it says
    nothing, so a session that outlived its runtime directory is still a
    session that can be stopped.
    """
    return _pid_of(default_paths().pid) or _scan_live_pid()


def stop_live() -> int:
    """Stop the live overlay. The engine loop runs while the lock exists.

    Only live. The text translator is a separate mode with its own window, and
    stopping one is not a request to close the other: `--stop` closing the text
    window too was a mode switch dressed up as a stop.
    """
    paths = default_paths()
    pid = running_pid()
    if not pid:
        for leftover in (paths.lock, paths.pid):
            try:
                leftover.unlink()
            except OSError:
                pass
        print("не запущено")
        return 0

    # The loop watches the lock, so removing it is the polite request. The rest
    # exists because "polite" is not a guarantee: the worker is regularly
    # inside a blocking OCR or a network call, and an overlay that survives its
    # own stop command is worse than one that never appeared - it is
    # click-through, so there is nothing on it to click.
    try:
        paths.lock.unlink()
    except OSError:
        pass

    def gone(deadline: float) -> bool:
        while time.monotonic() < deadline:
            if not _alive(pid):
                return True
            time.sleep(0.05)
        return not _alive(pid)

    if gone(time.monotonic() + 2.0):
        print(f"остановлено, pid={pid}")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    if gone(time.monotonic() + 3.0):
        print(f"остановлено, pid={pid}")
        return 0
    # Asked once, waited for, and still there. Say so instead of reporting a
    # success that did not happen.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    if gone(time.monotonic() + 2.0):
        print(f"остановлено (SIGKILL), pid={pid}")
        return 0
    print(
        f"НЕ УДАЛОСЬ остановить live: pid={pid} жив после SIGKILL",
        file=sys.stderr,
    )
    return 1


# ------------------------------------------------------------------ regions


def ask_region(args: argparse.Namespace, *, auto_action: str = "") -> str:
    """Region from the command line, or selected with the project's own overlay.

    ``auto_action`` (``ocr`` / ``live`` / …): confirm on mouse-up after the drag
    — no toolbar. Plain interactive pick (default command) keeps the buttons.
    """
    raw = args.geom or args.region
    if raw:
        geom = capture.normalize_geom(raw)
        if not geom:
            print(f"error: не удалось разобрать область {raw!r}", file=sys.stderr)
            raise SystemExit(2)
        return geom
    if args.output:
        geom = capture.select_output()
        if not geom:
            print("выбор отменён", file=sys.stderr)
            raise SystemExit(1)
        return geom
    try:
        _action, geom = selector.select_region(auto_action=auto_action)
    except selector.SelectorError as exc:
        print(f"выделятель: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not geom:
        print("выбор отменён", file=sys.stderr)
        raise SystemExit(1)
    return geom


# -------------------------------------------------------------- clipboard


def read_clipboard() -> str:
    """Clipboard text via wl-paste, empty when unavailable."""
    if not shutil.which("wl-paste"):
        return ""
    try:
        res = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout.decode("utf-8", "replace").strip()


def copy_to_clipboard(text: str) -> bool:
    """wl-copy forks a daemon that inherits stdout, so it must get a real fd
    instead of a pipe, otherwise waiting for the child never returns."""
    if not shutil.which("wl-copy"):
        print("error: нет wl-copy (Arch: wl-clipboard)", file=sys.stderr)
        return False
    from .ocr.clipboard import clipboard_bytes

    data = clipboard_bytes(text)
    try:
        res = subprocess.run(
            ["wl-copy"], input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
        )
    except subprocess.TimeoutExpired:
        print("error: wl-copy не ответил", file=sys.stderr)
        return False
    if res.returncode != 0:
        print("error: wl-copy завершился с ошибкой", file=sys.stderr)
        return False
    return True


# ------------------------------------------------------------------ actions


def cmd_ocr_copy(args: argparse.Namespace, conf: Config) -> int:
    """screen → region → OCR → reconstruct → clipboard.

    No translation, glossary rewrite, or live/game heuristics.
    Drag confirms immediately (no action toolbar).
    """
    from .ocr.universal import universal_ocr_text

    geom = ask_region(args, auto_action="ocr")
    try:
        image = capture.capture_image(geom)
    except capture.CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Configure engines from config (order / enablement) without starting live.
    from . import live

    live.configure(conf)
    text = universal_ocr_text(image)
    if not text.strip():
        print("error: в области не распознан текст", file=sys.stderr)
        return 1
    if not copy_to_clipboard(text):
        return 1
    n_lines = text.count("\n") + (1 if text.strip() else 0)
    preview = " ".join(text.split())[:160]
    print(f"скопировано: {n_lines} строк, {len(text)} символов — {preview}")
    return 0


def cmd_live(args: argparse.Namespace, conf: Config, geom: str) -> int:
    from .desktop import run
    return run("start-auto-translate", conf, geometry=geom, path=args.config)


def stop_text_window() -> int:
    """Kill a leftover text translator so at most one window exists."""
    pid = _pid_of(default_paths().text_pid)
    if not pid:
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return 0
    for _ in range(30):
        if not _pid_of(default_paths().text_pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        default_paths().text_pid.unlink(missing_ok=True)
    except OSError:
        pass
    return pid


def cmd_text(conf: Config, initial: str = "") -> int:
    from .desktop import run
    return run("show-window", conf, initial=initial)


def cmd_main_flow(args: argparse.Namespace, conf: Config) -> int:
    """The default flow: pick a region and an action in one overlay.

    The selector returns only ``(action, geometry)``; OCR, the text window and
    live are the existing implementations called from here.

    There is no fallback. When the selector is unavailable the command says so
    and stops, rather than quietly putting a different interface in front of the
    user: a drag with a second three-button window is not the same product, and a
    user who hit a key expecting the selector would get something they never
    agreed to.
    """
    from .paths import emit_storage_banner

    emit_storage_banner(default_paths(), also_log=False)
    if not selector.have_selector():
        print(
            "Селектор недоступен: нужны GTK4 и gtk4-layer-shell с привязками Python "
            "(Arch: pacman -S gtk4 gtk4-layer-shell python-gobject).\n"
            "Обход без селектора:\n"
            "  kizurium-translator --live --region \"0,0 800x600\"\n"
            "  kizurium-translator --output   # выбрать монитор (нужен slurp)",
            file=sys.stderr,
        )
        return 1
    try:
        action, geom = selector.select_region()
    except selector.SelectorError as exc:
        print(f"выделятель: {exc}", file=sys.stderr)
        return 1
    if not action:
        print("отменено", file=sys.stderr)
        return 1
    if action == selector.SEL_SETTINGS:
        from .desktop import run

        return run("settings", conf, path=args.config)
    scoped = argparse.Namespace(**{**vars(args), "region": geom, "geom": None})
    if action == selector.SEL_OCR:
        return cmd_ocr_copy(scoped, conf)
    if action == selector.SEL_TEXT:
        return cmd_text(conf)
    return cmd_live(scoped, conf, geom)


# ------------------------------------------------------------------- doctor

# What the exit code depends on, and what it does not.
#
# The region selector is GTK4 + layer-shell and has its own section: a machine
# that cannot show it still translates (--live --region, --text), so it must not
# decide the exit code of `--doctor`.
#
# grim is what the overlay captures with outside KDE (there the portal is checked
# instead): no grim, no live translation. wl-copy
# backs `--ocr-copy` and slurp backs `--output`; both are features, not the core.
CORE_TOOLS = ("grim",)
SELECTOR_TOOLS: tuple[str, ...] = ()  # no external tools: GTK4 + layer-shell
OPTIONAL_TOOLS = ("wl-copy", "slurp")
REQUIRED_LANGS = ("eng", "rus")


def _wrapper_belongs_to_venv(wrapper: Path, venv: Path) -> bool:
    """True if ``wrapper`` is our install.sh script or a link into ``venv``."""
    venv_s = str(venv.resolve()) if venv.exists() else str(venv)
    if wrapper.is_symlink():
        try:
            target = str(wrapper.resolve())
        except OSError:
            # Broken symlink left after a repo move — still ours to remove.
            return "kizurium-translator" in str(wrapper)
        return venv_s in target or target.endswith("/kizurium-translator")
    if not wrapper.is_file():
        return False
    try:
        text = wrapper.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "Generated by kizurium-translator install.sh" in text:
        return True
    return venv_s in text


def cmd_uninstall(*, purge: bool) -> int:
    """Remove the installed command/venv; optional full XDG wipe.

    Works without the git checkout: after someone deletes or moves the repo,
    ``kizurium-translator --uninstall --purge`` still cleans up.
    """
    import shutil

    from .paths import default_paths

    stop_live()
    paths = default_paths()
    venv = paths.data_dir / "venv"
    removed_any = False

    bin_candidates = [
        Path.home() / ".local" / "bin" / "kizurium-translator",
    ]
    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        bin_candidates.append(Path(prefix) / "bin" / "kizurium-translator")
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    for bin_path in bin_candidates:
        try:
            key = bin_path.resolve() if bin_path.exists() else bin_path
        except OSError:
            key = bin_path
        if key in seen:
            continue
        seen.add(key)
        if not (bin_path.exists() or bin_path.is_symlink()):
            continue
        # Prefer our wrapper/venv link; still remove a leftover name clash
        # (old symlink into a moved repo.venv) so PATH is clean afterwards.
        if not (
            _wrapper_belongs_to_venv(bin_path, venv)
            or bin_path.name == "kizurium-translator"
        ):
            continue
        try:
            bin_path.unlink()
            print(f"удалён {bin_path}")
            removed_any = True
        except OSError as exc:
            print(f"не удалось удалить {bin_path}: {exc}", file=sys.stderr)

    if venv.is_dir():
        shutil.rmtree(venv, ignore_errors=True)
        print(f"удалён {venv}")
        removed_any = True
    elif (paths.data_dir / "venv").exists():
        pass

    if purge:
        for label, path in (
            ("config", paths.config_file.parent),
            ("cache", paths.cache_dir),
            ("data", paths.data_dir),
            ("state", paths.state_dir),
        ):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                print(f"удалён {path}  ({label})")
                removed_any = True
    else:
        print()
        print("Конфиг, кэш, логи и offline-модели оставлены:")
        print(f"  {paths.config_file.parent}")
        print(f"  {paths.cache_dir}")
        print(f"  {paths.data_dir}")
        print(f"  {paths.state_dir}")
        print("Полный снос: kizurium-translator --uninstall --purge")

    if not removed_any and not purge:
        print("нечего удалять (команда/venv не найдены)")
        return 1
    print("готово")
    return 0


def cmd_packs() -> int:
    from .translation.packs import list_pack_status

    rows = list_pack_status()
    if not rows:
        print("каталог пакетов пуст")
        return 0
    print("offline language packs:")
    for row in rows:
        if row.installed:
            state = f"Installed ({row.installed_version})"
        elif row.info.downloadable:
            state = "Download (one command)"
        else:
            state = "Download (local --from)"
        print(f"  {row.info.label:40} {state}")
        print(f"    id={row.info.id}  {row.info.source}→{row.info.target}  {row.info.engine}")
    print()
    print("установка всего:  kizurium-translator --pack-install all")
    print("один пакет:       kizurium-translator --pack-install opus-mt-en-ru")
    return 0


def _pair(raw: str) -> tuple[str, str]:
    src, _, dst = (raw or "").strip().partition("-")
    if not src or not dst:
        raise SystemExit(f"error: ожидается пара вида en-ru, получено {raw!r}")
    return src, dst


def cmd_model_status(conf: Config) -> int:
    """One report for everything local: OCR engines, Bergamot, offline packs."""
    from .ocr.selection import inventory
    from .translation import bergamot as bg

    print("OCR-движки:")
    for name, row in inventory(conf.ocr_options).items():
        print(f"  {'✓' if row['available'] else '✗'} {name:10} {row['detail']}")
    kind = bg.runtime_kind()
    print("\nBergamot (локальный перевод):")
    print(f"  {'✓' if kind else '✗'} движок: "
          + {"in-process": "установлен (в основном Python)", "helper": f"установлен ({bg.helper_python()})", "system": f"установлен ({bg.system_python()})"}.get(
              kind, "нет — kizurium-translator --bergamot-runtime-install (без него работают офлайн-паки OPUS-MT)"))
    root = bg.cache_root()
    found = False
    for manifest in sorted(root.glob("*/kizurium-model.json")) if root.is_dir() else []:
        found = True
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            bg.validate_files(manifest.parent, meta["source"], meta["target"])
            state, size = "проверена", sum(f["size"] for f in meta["files"].values()) / 1048576
            print(f"  ✓ {meta['source']}→{meta['target']}  v{meta.get('version', '?')}  {size:.0f} МиБ  {state}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {manifest.parent.name}  повреждена или несовместима ({type(exc).__name__})")
    if not found:
        print(f"  — моделей нет ({root}); установка: kizurium-translator --bergamot-install en-ru")
    print("\nОфлайн-паки (OPUS-MT):")
    return cmd_packs()


def cmd_bergamot_install(args: argparse.Namespace, conf: Config) -> int:
    from .translation import bergamot as bg
    from .translation.providers import ProviderError
    from .translation.scheduler import Cancelled

    if conf.offline_only:
        print("error: включён автономный режим — загрузки запрещены (offline_only в конфиге)", file=sys.stderr)
        return 1
    src, dst = _pair(args.bergamot_install)
    manager = bg.ModelManager(lambda: False, lambda status, pair, progress, detail: print(
        f"\r  {status} {pair} {progress:5.0%} {detail}".ljust(78), end="", flush=True))
    try:
        path = manager.download(src, dst)
    except Cancelled:
        print("\nотменено")
        return 1
    except ProviderError as exc:
        print(f"\nerror: {exc.category}: {exc.detail or 'не удалось'}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nпрервано; готовая модель (если была) не тронута")
        return 130
    finally:
        manager.shutdown()
    print(f"\n✓ {src}→{dst}: {path}")
    if not bg.runtime_available():
        print("  модель проверена по контрольной сумме; для перевода нужен движок: "
              "kizurium-translator --bergamot-runtime-install")
    return 0


def cmd_bergamot_runtime(conf: Config, remove: bool = False) -> int:
    from .translation import bergamot as bg
    from .translation.providers import ProviderError

    if remove:
        bg.remove_runtime()
        print(f"удалено: {bg.runtime_dir()}")
        return 0
    if conf.offline_only:
        print("error: включён автономный режим — загрузки запрещены (offline_only в конфиге)", file=sys.stderr)
        return 1
    try:
        venv = bg.install_runtime(lambda status, _what, progress, detail: print(
            f"  {status} {progress:4.0%} {detail}", flush=True))
    except ProviderError as exc:
        print(f"error: {exc.detail or exc.category}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"✓ движок Bergamot готов: {venv}")
    return 0


def cmd_bergamot_remove(args: argparse.Namespace) -> int:
    from .translation import bergamot as bg

    src, dst = _pair(args.bergamot_remove)
    path = bg.cache_root() / bg.pair_name(src, dst)
    if path.is_symlink() or not path.exists():
        print(f"модели {src}→{dst} в кэше нет")
        return 1
    shutil.rmtree(path)
    print(f"удалено: {path}")
    return 0


def cmd_ocr_warmup(engine: str) -> int:
    if engine != "rapid":
        print(f"error: прогрев поддерживается для rapid, получено {engine!r}", file=sys.stderr)
        return 2
    try:
        from rapidocr import RapidOCR

        RapidOCR()  # downloads its detection/classification/recognition models once
    except ImportError:
        print("error: RapidOCR не установлен (extra rapid)", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: не удалось подготовить модели RapidOCR: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("✓ модели RapidOCR на месте")
    return 0


def cmd_pack_install(args: argparse.Namespace) -> int:
    from .translation.packs import install_pack, install_recommended_packs

    pack_id = (args.pack_install or "").strip()
    if pack_id.lower() in {"all", "*"}:
        if args.pack_from:
            print("error: --from не сочетается с --pack-install all", file=sys.stderr)
            return 1
        if not shutil.which("aria2c"):
            print(
                "нет aria2 — качание будет медленным. Поставь пакет aria2.",
                flush=True,
            )
        rows = install_recommended_packs()
        failed = 0
        for pid, dest, note in rows:
            pair = pid.removeprefix("opus-mt-").replace("-", "→")
            if dest is not None and note == "installed":
                print(f"  {pair}  установлено", flush=True)
            elif note == "already installed":
                print(f"  {pair}  уже стоит", flush=True)
            else:
                failed += 1
                print(f"  {pair}  не скачалось: {note}", file=sys.stderr, flush=True)
        if failed:
            print("Не установилось.", flush=True)
            return 1
        print("Готово. en→ru и ja→ru на месте, без интернета переводят.", flush=True)
        return 0

    from_path = Path(args.pack_from).expanduser() if args.pack_from else None
    if from_path is not None and not from_path.exists():
        print(f"error: нет пути {from_path}", file=sys.stderr)
        return 1
    try:
        dest = install_pack(pack_id, from_path=from_path)
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"установлен {pack_id} → {dest}")
    return 0


def cmd_tm_list(conf: Config) -> int:
    from .lexicon.translation_memory import default_memory

    rows = default_memory().list_entries(target_lang=conf.target_lang, limit=40)
    if not rows:
        print("translation memory пуста")
        return 0
    print(f"translation memory ({len(rows)} последних), не глоссарий:")
    for row in rows:
        print(f"  [{row.source_lang}->{row.target_lang}] {row.source_text!r} → {row.translated_text!r}")
    print("в глоссарий: kizurium-translator --tm-promote '...'")
    return 0


def cmd_tm_promote(args: argparse.Namespace, conf: Config) -> int:
    from .lexicon.regex_rules import clear_cache
    from .lexicon.translation_memory import default_memory

    path = default_memory().promote_to_user_glossary(
        args.tm_promote,
        source_lang=args.tm_source_lang or conf.source_lang or "en",
        target_lang=conf.target_lang,
        pack_id=args.pack_id or "from-memory",
    )
    if path is None:
        print("error: такой записи в памяти нет", file=sys.stderr)
        return 1
    clear_cache()
    print(f"промоут в пользовательский глоссарий → {path}")
    return 0


def cmd_import_dictionary(args: argparse.Namespace) -> int:
    """Import a user-owned external dictionary into the local dictionaries dir."""
    from .lexicon.import_external import import_dictionary_file
    from .lexicon.regex_rules import clear_cache

    path = Path(args.import_dictionary).expanduser()
    if not path.is_file():
        print(f"error: нет файла {path}", file=sys.stderr)
        return 1
    fmt = None if args.dict_format == "auto" else args.dict_format
    try:
        result = import_dictionary_file(
            path,
            pack_id=args.pack_id,
            fmt=fmt,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    clear_cache()
    print(
        f"импорт → {result.pack_dir}\n"
        f"  exact={result.exact} regex={result.regex} skipped={result.skipped}"
    )
    print(
        "замечание: файл должен быть вашим / с правом использования; "
        "полные дампы игр в репозиторий не кладём."
    )
    for note in result.warnings[:5]:
        print(f"  · {note}", file=sys.stderr)
    return 0


def _tool_line(name: str, *, required: bool, hint: str = ""):
    """One external tool, found or not."""
    from .diagnose import Line, Status

    path = shutil.which(name)
    if path:
        return Line(name=name, status=Status.OK, detail="найден", required=required)
    return Line(name=name, status=Status.MISSING, detail=hint or "не найден", required=required)


def _portal_capture_line():
    """KDE captures through the ScreenCast portal: it needs GStreamer's pipewiresrc."""
    from .diagnose import Line, Status

    name = "screen capture (ScreenCast portal)"
    try:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        if Gst.ElementFactory.find("pipewiresrc") is None:
            return Line(name=name, status=Status.MISSING, required=True,
                        detail=("нет GStreamer pipewiresrc "
                                "(Arch: pacman -S gst-plugin-pipewire; Nix: pipewire + GST plugin path)"))
    except Exception as exc:  # noqa: BLE001
        return Line(name=name, status=Status.MISSING, required=True,
                    detail=(f"GStreamer недоступен ({type(exc).__name__}: {exc}); "
                            "нужны PyGObject и GStreamer typelibs (Nix: gst_all_1.gst-plugins-base)"))
    return Line(name=name, status=Status.OK, required=True, detail="GStreamer + pipewiresrc")


def _module_line(module: str, label: str, *, required: bool = False, hint: str = ""):
    """One Python dependency, importable or not."""
    from .diagnose import Line, Status

    if _module_present(module):
        return Line(name=label, status=Status.OK, detail="установлен", required=required)
    return Line(name=label, status=Status.MISSING, detail=hint or "не установлен", required=required)


def cmd_doctor(conf: Config) -> int:
    """Report what is installed and whether a missing part is the reason.

    Sections, not one flat list: Core, Wayland, Selector, OCR,
    Translation, Models, Config, and the reason for the sections is the exit
    code. quickshell was in `REQUIRED_TOOLS`, so a machine where everything the
    live overlay needs is present and only the region selector is missing exited
    1 - an optional thing reported as a core failure, which is a mistake worth
    naming. Selector is its own section now and nothing in it fails live.
    """
    from .compositor import detect, hotkey_snippet
    from .diagnose import Section, Status, render_doctor

    core = Section("Core")
    wayland = Section("Wayland")
    selector = Section("Selector")
    ocr = Section("OCR")
    translation = Section("Translation")
    models = Section("Models")
    cfg = Section("Config")

    # --- Core: nothing here can be optional. ---
    core.add("python", Status.OK, detail=platform.python_version())
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        core.add("gtk4 + layer-shell", Status.OK, detail="ok", required=True)
    except Exception as exc:  # noqa: BLE001
        core.add(
            "gtk4 + layer-shell",
            Status.ERROR,
            detail=f"{type(exc).__name__}: нужен gtk4 и gtk4-layer-shell",
            required=True,
        )
    try:
        from .layer_shell_lib import preload as _preload_layer_shell

        so_ok = _preload_layer_shell()
    except Exception:  # noqa: BLE001
        so_ok = False
    core.add(
        "libgtk4-layer-shell",
        Status.OK if so_ok else Status.UNKNOWN,
        detail=(
            "ok"
            if so_ok
            else "CDLL не видит.so (Nix: flake wrap / LD_LIBRARY_PATH); overlay может стать обычным окном"
        ),
    )
    try:
        import cairo  # noqa: F401

        core.add("pycairo", Status.OK, detail="ok", required=True)
    except Exception as exc:  # noqa: BLE001
        core.add("pycairo", Status.ERROR, detail=type(exc).__name__, required=True)
    if "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        # KWin has no wlr-screencopy: grim cannot capture there, the portal does.
        core.add_line(_portal_capture_line())
        core.add_line(_tool_line("grim", required=False, hint="не нужен на KDE: захват идёт через портал"))
    else:
        core.add_line(_tool_line("grim", required=True, hint="нет (нужен для захвата кадра)"))

    # --- Wayland: the session, not the tools. ---
    wayland_disp = os.environ.get("WAYLAND_DISPLAY")
    wayland.add(
        "WAYLAND_DISPLAY",
        Status.OK if wayland_disp else Status.MISSING,
        detail="задан" if wayland_disp else "не задан — запускать из Wayland-сессии",
        required=True,
    )
    comp = detect()
    wayland.add(
        "compositor",
        Status.OK if comp.id != "none" else Status.MISSING,
        detail=comp.label,
    )
    wayland.add(
        "XDG_RUNTIME_DIR",
        Status.OK if os.environ.get("XDG_RUNTIME_DIR") else Status.UNKNOWN,
        detail="задан" if os.environ.get("XDG_RUNTIME_DIR") else "не задан",
    )
    wayland.add_line(
        _tool_line("wl-copy", required=False, hint="нет (нужен для --ocr-copy)")
    )
    wayland.add_line(
        _tool_line("slurp", required=False, hint="нет (нужен для --output / выбора монитора)")
    )

    # --- Selector: its own section, and nothing in it fails live. ---
    try:
        from .selector import have_selector

        have_qml = have_selector()
    except Exception:  # noqa: BLE001
        have_qml = False
    if have_qml:
        selector.add("region selector", Status.OK, detail="GTK4 + layer-shell")
    else:
        # Not MISSING-and-required: its absence says nothing about live translation.
        selector.add(
            "region selector",
            Status.DISABLED,
            detail="нет gtk4-layer-shell; --live и --text работают, выбор области — нет",
        )

    # --- OCR: tesseract is required (eng+rus); the rest are alternatives. ---
    tess = shutil.which("tesseract")
    if not tess:
        ocr.add("tesseract", Status.MISSING, detail="не найден", required=True)
    else:
        try:
            res = subprocess.run(
                [tess, "--list-langs"], capture_output=True, text=True, timeout=5, check=False
            )
            langs = [lang.strip() for lang in res.stdout.splitlines()[1:] if lang.strip()]
        except (OSError, subprocess.SubprocessError):
            langs = []
        missing = [lang for lang in REQUIRED_LANGS if lang not in langs]
        ocr.add(
            "tesseract + eng/rus",
            Status.OK if not missing else Status.MISSING,
            detail=(
                f"языки: {', '.join(langs)}"
                if langs
                else "бинарь есть, языки не перечислились"
            ),
            required=True,
        )
        ocr.add(
            "tesseract jpn",
            Status.OK if "jpn" in langs else Status.DISABLED,
            detail="есть" if "jpn" in langs else "нет — японский refinement выключен",
        )
    ocr.add_line(
        _module_line("rapidocr", "RapidOCR", hint="нет (uv sync --extra rapid |./install.sh)")
    )
    ocr.add_line(
        _module_line("meikiocr", "MeikiOCR", hint="нет (uv sync --extra meiki)")
    )
    try:
        from .ocr.routing import available_engines

        engines = ", ".join(available_engines()) or "ничего"
        ocr.add("engines available", Status.OK if engines != "ничего" else Status.MISSING, detail=engines)
    except Exception as exc:  # noqa: BLE001
        ocr.add("engines available", Status.ERROR, detail=type(exc).__name__)

    # --- Translation: the backend order, and what each one is doing now. ---
    translation.add_line(
        _module_line(
            "ctranslate2", "CTranslate2", hint="нет (uv sync --extra local-mt)"
        )
    )
    translation.add_line(
        _module_line(
            "deep_translator", "deep-translator", hint="нет (uv sync --extra deep-translate)"
        )
    )
    provider = conf.translation_provider
    requires_network = provider != "bergamot"
    enabled = not requires_network or (not conf.offline_only and
                                       (provider != "google" or conf.use_gtx))
    translation.add(
        "translation provider",
        Status.OK if enabled else Status.DISABLED,
        detail=provider + ("; local/offline" if not requires_network else "; network required"),
    )
    translation.add(
        "online fallback", Status.DISABLED,
        detail="выбранный провайдер используется без автоматической смены сервиса",
    )
    translation.add(
        "HTTP 429 handling", Status.OK,
        detail="очередь, задержка, ограниченные повторы и cooldown; доступность сервиса не проверялась",
    )

    # --- Models: what is installed, not what is available. ---
    try:
        from .translation.packs import find_installed_pair, list_pack_status

        for row in list_pack_status():
            models.add(
                row.info.id,
                Status.OK if row.installed else Status.MISSING,
                detail=(
                    f"установлен {row.installed_version}"
                    if row.installed and row.installed_version
                    else "установлен"
                    if row.installed
                    else "нет (--pack-install all)"
                ),
            )
        for pair, label in (("en", "ru"), ("ja", "ru")):
            models.add(
                f"local {pair}→{label}",
                Status.OK if find_installed_pair(pair, label) else Status.MISSING,
                detail=(
                    "установлен"
                    if find_installed_pair(pair, label)
                    else "нет (--pack-install all)"
                ),
            )
    except Exception as exc:  # noqa: BLE001
        models.add("models", Status.ERROR, detail=type(exc).__name__)

    # --- Config: what is in effect, without printing a path or its contents. ---
    cfg.add("target", Status.OK, detail=f"{conf.target_lang} ← {conf.source_lang}")
    cfg.add("engine", Status.OK, detail=str(getattr(conf, "engine", "") or "auto"))
    cfg.add("offline_only", Status.DISABLED if conf.offline_only else Status.OK, detail=str(bool(conf.offline_only)))
    try:
        from .profile import active

        prof = active()
        cfg.add(
            "profile",
            Status.OK if prof else Status.DISABLED,
            detail=prof.id if prof else "нет — универсальный режим",
        )
    except Exception as exc:  # noqa: BLE001
        cfg.add("profile", Status.ERROR, detail=type(exc).__name__)
    cfg.add(
        "config file",
        Status.OK if default_paths().config_file.is_file() else Status.DISABLED,
        detail="есть" if default_paths().config_file.is_file() else "нет — используются умолчания",
    )
    try:
        from .threads import budget

        b = budget()
        cfg.add(
            "thread budget",
            Status.OK,
            detail=f"{b.cores} ядер → ocr {b.ocr}, local-mt {b.local_mt_inter}×{b.local_mt_intra}, pool {b.translation_executor}",
        )
    except Exception:  # noqa: BLE001
        cfg.add("thread budget", Status.UNKNOWN, detail="не удалось определить")

    notes = [
        "NOTES",
        "  - офлайн-пакеты: kizurium-translator --pack-install all",
        "  - docs/install.md · docs/trust.md",
        "",
        "HOTKEY HINT",
    ]
    notes.extend(f"  {line}" for line in hotkey_snippet(comp).splitlines())

    text, rc = render_doctor(
        [core, wayland, selector, ocr, translation, models, cfg],
        notes=notes,
    )
    print(text, end="")
    return rc


def _module_present(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------- main


def _needs_wayland(args: argparse.Namespace) -> bool:
    """Whether this invocation has to talk to the display.

    Only the commands that capture or draw do. --version, --status, --stop,
    --doctor and --print-config must not have the environment guessed at: they
    are what someone reaches for when something is already broken, and a
    discovery step that mutates WAYLAND_DISPLAY can turn a clean answer into a
    different one.
    """
    return not (
        args.stop
        or args.status
        or args.doctor
        or args.print_config
        or getattr(args, "uninstall", False)
        or getattr(args, "list_profiles", False)
        or getattr(args, "import_dictionary", None)
        or getattr(args, "tm_list", False)
        or getattr(args, "tm_promote", None)
        or getattr(args, "packs", False)
        or getattr(args, "pack_install", None)
    )


def main(argv: list[str] | None = None) -> int:
    # Before any GTK / fontconfig import path: silence a11y-bus noise and make
    # sure the private font registration has a writable cache dir.
    from .fonts import prepare_gui_env

    prepare_gui_env()
    try:
        return _main(argv)
    except KeyboardInterrupt:
        # Selector, Gtk live, or anything else that raises on Ctrl+C.
        print("остановлено", file=sys.stderr)
        return 130


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "purge", False) and not getattr(args, "uninstall", False):
        print("error: --purge только вместе с --uninstall", file=sys.stderr)
        return 2
    if getattr(args, "uninstall", False):
        return cmd_uninstall(purge=bool(args.purge))
    # Only now, after the arguments are understood, and only for the commands
    # that actually need it. A keybind runs through the compositor, which does
    # not always carry WAYLAND_DISPLAY, so the commands that shell out recover
    # it; --version and --status must not, or they are answering a different
    # question than the one that was asked.
    if _needs_wayland(args):
        capture.ensure_wayland_env()
    conf = resolve_config(args)

    if args.action:
        if args.action in ("quit", "stop-auto-translate"):
            # Only meaningful for a running instance: never start a second GUI
            # process just to tell it to stop.
            from .desktop import forward
            if not forward(args.action):
                print("не запущено", file=sys.stderr)
            return 0
        from .desktop import run
        return run(args.action, conf, path=args.config)
    if args.stop:
        from .desktop import forward
        if forward("stop-auto-translate"):
            return 0
        return stop_live()
    if args.status:
        pid = running_pid()
        print(f"запущено, pid={pid}" if pid else "не запущено")
        return 0 if pid else 1
    if getattr(args, "list_profiles", False):
        from . import profile as profile_mod

        catalog = profile_mod.all_profiles()
        if not catalog:
            print("профилей нет (data/profiles/ пуст)")
            return 1
        for pid, prof in sorted(catalog.items()):
            print(
                f"{pid:12} pack={prof.lexicon_pack or '-'} "
                f"ocr={','.join(prof.preferred_ocr) or '-'} "
                f"zones={len(prof.ui_zones)} "
                f"gap={prof.speaker.gap_min_px}x{prof.speaker.gap_glyph_mult}"
            )
        print(
            "\nвкл: --profile ID  или  KIZURIUM_TRANSLATOR_PROFILE=ID  "
            "или  [profile] id=… в config.toml\n"
            "без профиля = универсальный режим (экран сам не угадывается)"
        )
        return 0
    if args.doctor:
        return cmd_doctor(conf)
    if getattr(args, "import_dictionary", None):
        return cmd_import_dictionary(args)
    if getattr(args, "tm_list", False):
        return cmd_tm_list(conf)
    if getattr(args, "tm_promote", None):
        return cmd_tm_promote(args, conf)
    if getattr(args, "model_status", False):
        return cmd_model_status(conf)
    if getattr(args, "bergamot_install", None):
        return cmd_bergamot_install(args, conf)
    if getattr(args, "bergamot_runtime_install", False):
        return cmd_bergamot_runtime(conf)
    if getattr(args, "bergamot_runtime_remove", False):
        return cmd_bergamot_runtime(conf, remove=True)
    if getattr(args, "bergamot_remove", None):
        return cmd_bergamot_remove(args)
    if getattr(args, "ocr_warmup", None):
        return cmd_ocr_warmup(args.ocr_warmup)
    if getattr(args, "packs", False):
        return cmd_packs()
    if getattr(args, "pack_install", None):
        return cmd_pack_install(args)
    if args.print_config:
        for section, values in conf.to_dict().items():
            print(f"[{section}]")
            for key, value in values.items():
                print(f"{key} = {value}")
            print()
        return 0
    if args.text is not None or args.text_from_clipboard:
        initial = args.text if isinstance(args.text, str) else ""
        if args.text_from_clipboard:
            initial = read_clipboard()
        return cmd_text(conf, initial)
    if args.ocr_copy:
        return cmd_ocr_copy(args, conf)
    if args.live:
        return cmd_live(args, conf, ask_region(args, auto_action="live"))
    if args.toggle:
        from .desktop import run
        return run("toggle", conf, path=args.config)
    return cmd_main_flow(args, conf)
