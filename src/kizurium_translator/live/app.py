"""Окно-оверлей: слой поверх области, клики насквозь.

Цикл кадра живёт в `session.worker`. Здесь только GTK-окно, которое этот
цикл просит перерисовать.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

from .. import autotrace
from .. import watch as watch_mod
from ..core import text
from ..core.text import tlog
from ..render.cards import click_through
from ..render.overlay import draw_blocks
from .runtime import TRANSLATION, shutdown
from .session import (
    APP_ID_OVERLAY,
    LAYER_NAMESPACE,
    Gdk,
    GLib,
    Gtk,
    LayerShell,
    State,
    _lock_paths,
    worker,
)
from .snap import parse_geom


def main_for_cli(geom: str) -> int:
    """Entry point for the CLI: the region is already selected."""
    return main(["--geom", geom])


def main(argv: list[str] | None = None) -> int:

    ap = argparse.ArgumentParser()
    ap.add_argument("--geom", required=True, help="region as 'x,y WxH'")
    ap.add_argument(
        "--no-translate",
        action="store_true",
        help="OCR only, no translation (debug)",
    )
    args = ap.parse_args(argv)

    TRANSLATION.disabled = bool(args.no_translate)

    rx, ry, rw, rh = parse_geom(args.geom)
    geom = args.geom
    text.PATHS.ensure()

    # Всё наблюдение - и то, что снаружи (команды, падения), и то, что внутри
    # (каждый вызов) - ставится здесь и до первого кадра. Иначе первый кадр
    # проходит без трассировки, а это тот самый кадр, на котором всё ломается.
    watch_mod.install_all()
    autotrace.install(skip=("worker", "main", "_worker_wrap"))

    # не стартовать второй слой поверх уже живого
    # shell может уже записать наш pid в pid-файл до входа сюда — себя не считаем «чужим»
    lock, pidfile = _lock_paths()
    if os.path.exists(pidfile):
        try:
            other = int(open(pidfile, encoding="utf-8").read().strip())
            if other > 0 and other != os.getpid():
                os.kill(other, 0)
                tlog(f"refuse-second pid={other}")
                return 0
        except (OSError, ValueError):
            pass

    # The lock is held open for the whole run, not opened and closed. The file
    # itself lives in tmpfs and can be unlinked out from under a running session,
    # but an open descriptor survives that: /proc/<pid>/fd still names it, so
    # "which process is the overlay" stays answerable even after the runtime
    # directory has been cleared and the pid file with it. Without this a
    # session could not be stopped, only waited out.
    lock_fd = os.open(lock, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
    except OSError:
        pass
    with open(pidfile, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    state = State()
    stop = threading.Event()

    def _worker_wrap() -> None:
        try:
            worker(state, geom, rx, ry, rw, rh, stop)
        except Exception as exc:
            tlog(f"worker-FATAL {type(exc).__name__}: {exc}")
            import traceback

            traceback.print_exc()

    threading.Thread(target=_worker_wrap, daemon=True).start()

    # UNIQUE: второй процесс не рисует ещё один fullscreen-слой поверх
    from gi.repository import Gio

    app = Gtk.Application(
        application_id=APP_ID_OVERLAY,
        flags=Gio.ApplicationFlags.FLAGS_NONE,
    )

    def _gdk_monitor_at(x: int, y: int):
        """The Gdk monitor holding the layout point ``(x, y)``, or None.

        Layout coordinates, so the same ones `--geom` is written in and the ones
        grim was asked to capture. Matching the monitor by that point is what
        ties the overlay to the screen the region was actually selected on.

        Half-open ranges, so a point exactly on a shared edge belongs to the
        output that starts there rather than the one that ends there.

        None when the point is outside every monitor - a region dragged past the
        edge of the layout. Then no monitor is set and the layer shell falls back
        to its own choice, which is the old behaviour rather than a wrong one.

        A failure here is *not* swallowed into None: this was written against
        `get_n_monitors()`/`get_monitor(i)`, which GTK4 does not have, so every
        call raised and the except turned it into "no monitor", and the overlay
        went back to the first output without a word. GDK's API is
        `get_monitors()`, and the call is left unguarded so a future change fails
        loudly in the log instead of silently reverting to the old placement.
        """
        display = Gdk.Display.get_default()
        for mon in display.get_monitors():
            geo = mon.get_geometry()
            if geo.x <= x < geo.x + geo.width and geo.y <= y < geo.y + geo.height:
                return mon
        return None

    def on_activate(_app: Gtk.Application) -> None:
        if _app.get_windows():
            return
        win = Gtk.ApplicationWindow(application=app)
        win.set_title("KizuriumTranslatorOverlay")
        win.set_decorated(False)

        LayerShell.init_for_window(win)
        # Какой выход занимает окно. Без этого GTK4 выбирает первый, и при двух
        # мониторах перевод области со второго экрана уезжал на первый: он
        # вычислялся верно, но был не виден нигде.
        monitor = _gdk_monitor_at(rx, ry)
        if monitor is not None:
            LayerShell.set_monitor(win, monitor)
        # окно = ровно выделенный geom: локальные OCR-координаты совпадают с cairo
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
        LayerShell.set_anchor(win, LayerShell.Edge.RIGHT, False)
        LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM, False)
        LayerShell.set_namespace(win, LAYER_NAMESPACE)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        try:
            # -1 = игнорировать reserved/панель при позиционировании (как grim: от края экрана).
            # 0 = Hyprland 0.53+ кладёт слой ПОД бар → карточки на ~bar_h ниже текста.
            LayerShell.set_exclusive_zone(win, -1)
        except Exception:
            pass
        try:
            # Отступы у layer shell отсчитываются от края ВЫХОДА, на котором живёт
            # окно, а не от начала раскладки. На первом мониторе они совпадают, и
            # поэтому всё работало; на втором (начало 1920) глобальный отступ
            # сдвигал окно на 1920 пикселей вправо от его же левого края, то
            # есть целиком за экран. Карточки при этом исправно создавались и в
            # лог писались верные координаты, а на обоих мониторах было пусто:
            # за экраном не видно, и логом это не поймать.
            #
            # grim снимает область по глобальным координатам, поэтому отсчёт от
            # выхода - единственное, при чём снимок и окно совпадут.
            mon_x, mon_y = 0, 0
            if monitor is not None:
                try:
                    mg = monitor.get_geometry()
                    mon_x, mon_y = mg.x, mg.y
                except Exception:  # noqa: BLE001
                    mon_x, mon_y = 0, 0
            LayerShell.set_margin(win, LayerShell.Edge.TOP, ry - mon_y)
            LayerShell.set_margin(win, LayerShell.Edge.LEFT, rx - mon_x)
            LayerShell.set_margin(win, LayerShell.Edge.RIGHT, 0)
            LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, 0)
        except Exception:
            pass
        win.set_default_size(rw, rh)
        try:
            win.set_size_request(rw, rh)
        except Exception:
            pass
        mon_name = ""
        if monitor is not None:
            try:
                mon_name = monitor.get_connector() or monitor.get_model() or ""
            except Exception:  # noqa: BLE001
                mon_name = ""
        tlog(f"layer-window output={mon_name or 'default'} geom={rx},{ry} {rw}x{rh} "
            f"exclusive=-1 (full-output coords, match grim)")

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            window,
            window.background,
.background {
              background: transparent;
              background-color: transparent;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        area = Gtk.DrawingArea()
        try:
            area.set_content_width(rw)
            area.set_content_height(rh)
        except Exception:
            pass
        area.set_hexpand(True)
        area.set_vexpand(True)

        def on_draw(_area, cr, _width, _height, _data):
            # always paint from current state on redraw. Do not try to
            # preserve a partial cairo buffer across frames — GTK clears the
            # surface; we redraw from ``state.snapshot()``. Expensive layout is
            # cached separately; here we only avoid queue_draw when
            # state.rev did not move.
            blocks, region, status = state.snapshot()
            draw_blocks(cr, blocks, region, status)
            state.mark_painted()

        area.set_draw_func(on_draw, None)
        win.set_child(area)
        try:
            win.set_can_focus(False)
        except Exception:
            pass

        last_rev = {"n": -1}

        def tick() -> bool:
            if not os.path.exists(lock):
                stop.set()
                app.quit()
                return False
            # queue_draw only when state revision advanced.
            rev = state.version()
            if rev != last_rev["n"]:
                last_rev["n"] = rev
                area.queue_draw()
                GLib.idle_add(lambda: (click_through(win), False)[1])
            return True

        def on_mapped(*_args):
            click_through(win)
            return False

        win.connect("realize", on_mapped)
        win.connect("map", on_mapped)
        GLib.timeout_add(25, tick)
        area.queue_draw()
        win.present()
        GLib.idle_add(on_mapped)

    app.connect("activate", on_activate)
    try:
        return app.run([sys.argv[0]])
    except KeyboardInterrupt:
        # Ctrl+C via gi's sigint fallback: quit cleanly, no traceback dump.
        return 130
    finally:
        stop.set()
        shutdown()
        for path in (lock, pidfile):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.close(lock_fd)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
