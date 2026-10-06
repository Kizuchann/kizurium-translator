"""GTK4 windows: the action menu and the text translator.

Both the text window and the live overlay translate through
:mod:`kizurium_translator.translate`, so there is one network path and one
cache. GTK is imported lazily so the module can be imported on a machine
without the GUI stack.
"""

from __future__ import annotations

import os
import threading

from . import translate as translate_mod

WIN_W, WIN_H = 880, 460
APP_ID_TEXTUI = "ru.kizurium.translator.textui"

DEBOUNCE_MS = 380
# How long a new request waits for the one it is replacing to give up. Long
# enough for the old one to be marked obsolete and stop, short enough that
# typing never feels like it is waiting on the network.
OBSOLETE_WAIT_S = 0.25

LANGS = [
    ("auto", "Определить язык"),
    ("ru", "Русский"),
    ("en", "Английский"),
    ("ja", "Японский"),
    ("zh-CN", "Китайский"),
    ("ko", "Корейский"),
    ("de", "Немецкий"),
    ("fr", "Французский"),
    ("es", "Испанский"),
    ("it", "Итальянский"),
    ("pt", "Портагальский"),
    ("pl", "Польский"),
    ("uk", "Украинский"),
    ("tr", "Турецкий"),
    ("ar", "Арабский"),
    ("hi", "Хинди"),
]

_GUI_ERROR = ""


def _gtk():
    """Import GTK on demand and report a useful error if it is missing."""
    global _GUI_ERROR
    if _GUI_ERROR:
        raise RuntimeError(_GUI_ERROR)
    # Before Gtk loads: the a11y bus is often masked on gaming/minimal sessions.
    os.environ.setdefault("GTK_A11Y", "none")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, GLib, Gtk
    except Exception as exc:  # noqa: BLE001
        _GUI_ERROR = (
            f"GUI dependencies unavailable ({type(exc).__name__}: {exc}). "
            "Install gtk4, python-gobject and python-cairo, then run --doctor."
        )
        raise RuntimeError(_GUI_ERROR) from exc
    return Gtk, Gdk, GLib


def copy_to_clipboard(text: str) -> bool:
    """Put text on the clipboard, through the tool that is present.

    wl-copy is what the OCR mode already uses, so this is not a new dependency.
    If it is missing the caller is told, rather than the click doing nothing
    silently.
    """
    if not text:
        return False
    import shutil
    import subprocess

    tool = shutil.which("wl-copy")
    if not tool:
        return False
    try:
        res = subprocess.run(
            [tool],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def is_bad_translation(src: str, out: str, target: str = "") -> bool:
    return translate_mod.is_error_response(src, out, target)


def translate_text(
    text: str,
    source: str,
    target: str,
    translator: translate_mod.Translator | None = None,
) -> tuple[str, str, str]:
    """(translation, used_source, status)."""
    text = (text or "").strip()
    if not text:
        return "", source, ""
    tr = translator or translate_mod.Translator(target=target, source=source)
    used = tr.resolve_source(text)
    if used == tr.target:
        return text, used, "Тот же язык"

    out = tr.translate(text, source=source)
    if out and not is_bad_translation(text, out, target):
        return out, used, "Готово"
    return "", used, "Сервис недоступен или ответил 429 — попробуй позже"


# ------------------------------------------------------------- text window


def text_window(
    source: str = "auto",
    target: str = "ru",
    initial: str = "",
    translator: translate_mod.Translator | None = None,
) -> int:
    """Two-pane translator window."""
    Gtk, Gdk, GLib = _gtk()
    tr = translator or translate_mod.Translator(target=target, source=source)
    app = Gtk.Application(application_id=APP_ID_TEXTUI)
    win_ref: list = []

    def on_activate(_app) -> None:  # noqa: ANN001
        # GApplication forwards every later launch to the running instance, so
        # building a window per activate piled up duplicate windows. One window,
        # re-presented.
        if win_ref:
            existing = win_ref[0]
            existing.present()
            return

        win = Gtk.ApplicationWindow(application=app)
        win_ref.append(win)
        win.set_title("Переводчик")
        win.set_default_size(WIN_W, WIN_H)
        win.set_resizable(True)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            window { background-color: #1f1f1f; }
.card { background-color: #2b2b2b; border-radius: 12px; }
.big textview,.big textview text {
              background-color: transparent;
              color: #e8eaed;
              font-size: 22px;
            }
.roman { color: #9aa0a6; font-size: 13px; }
.hint { color: #9aa0a6; font-size: 12px; }
.swap {
              background-color: transparent;
              color: #e8eaed;
              border: none;
              font-size: 18px;
              min-width: 40px;
              min-height: 36px;
              border-radius: 18px;
            }
.swap:hover { background-color: rgba(255,255,255,0.08); }
.iconbtn {
              background-color: transparent;
              color: #9aa0a6;
              border: none;
              border-radius: 18px;
              min-width: 36px;
              min-height: 36px;
            }
.iconbtn:hover { background-color: rgba(255,255,255,0.08); color: #e8eaed; }
            dropdown { min-height: 36px; min-width: 150px; }
.status { color: #9aa0a6; font-size: 12px; }
.status-err { color: #f28b82; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        state = {
            "source": tr.source or "auto",
            "target": tr.target,
            "job": 0,
            "thread": None,  # the request in flight, so it can be waited on
        }

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(14)

        lang_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lang_bar.set_halign(Gtk.Align.CENTER)

        src_codes = [c for c, _ in LANGS]
        dst_codes = [c for c, _ in LANGS if c != "auto"]
        src_dd = _lang_dropdown(Gtk, src_codes, state["source"])
        dst_dd = _lang_dropdown(Gtk, dst_codes, state["target"])
        btn_swap = Gtk.Button(label="⇄")
        btn_swap.add_css_class("swap")
        btn_swap.set_tooltip_text("Поменять языки местами")

        lang_bar.append(src_dd)
        lang_bar.append(btn_swap)
        lang_bar.append(dst_dd)
        root.append(lang_bar)

        status = Gtk.Label(label="", xalign=0.5)
        status.add_css_class("status")
        root.append(status)

        panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        panes.set_vexpand(True)

        left = _pane(Gtk, editable=True, hint="Введите текст")
        # Read-only on purpose. The right pane is the result, and a user who can
        # type into it has no way to tell an edited translation from a real one.
        # Editing the source is what they want; the result is what they read.
        right = _pane(Gtk, editable=False, hint="Перевод")
        panes.append(left["card"])
        panes.append(right["card"])
        root.append(panes)

        def selected(dd) -> str:  # noqa: ANN001
            idx = dd.get_selected()
            return src_codes[idx] if idx < len(src_codes) else "auto"

        def selected_dst(dd) -> str:  # noqa: ANN001
            idx = dd.get_selected()
            return dst_codes[idx] if idx < len(dst_codes) else "ru"

        def schedule(*_a) -> bool:
            state["job"] += 1
            job = state["job"]
            GLib.timeout_add(DEBOUNCE_MS, run, job)
            return False

        def run(job: int) -> bool:
            if job != state["job"]:
                return False
            state["source"] = selected(src_dd)
            state["target"] = selected_dst(dst_dd)
            tr.source = state["source"]
            tr.target = state["target"]
            text = left["buffer"].get_text(
                left["buffer"].get_start_iter(), left["buffer"].get_end_iter(), False
            )
            if not text.strip():
                right["buffer"].set_text("")
                status.set_label("")
                return False
            status.set_label("Перевод…")
            status.remove_css_class("status-err")

            def work() -> None:
                out, used, note = translate_text(text, state["source"], state["target"], tr)

                def done() -> None:
                    if job != state["job"]:
                        # Obsolete: the user has typed since this was sent. The
                        # answer is dropped rather than displayed, and the
                        # request that replaces it is already under way.
                        return
                    right["buffer"].set_text(out)
                    label = f"{note} · {used}" if note else ""
                    status.set_label(label)
                    if not out and note:
                        status.add_css_class("status-err")

                GLib.idle_add(done)

            previous = state["thread"]
            thread = threading.Thread(target=work, daemon=True)
            state["thread"] = thread
            # At most one request in flight per window. The old guard only
            # stopped an obsolete answer being displayed; the request itself
            # still ran to the end. So typing five characters sent five
            # requests, spent five answers' worth of the endpoint's rate limit,
            # and returned them in whatever order the network finished. Waiting
            # briefly for the previous one to notice it is obsolete costs a few
            # milliseconds and keeps the old request from finishing at all when
            # it can be helped along.
            if previous is not None and previous.is_alive():
                previous.join(timeout=OBSOLETE_WAIT_S)
            thread.start()
            return False

        left["buffer"].connect("changed", schedule)

        def on_swap(_b) -> None:
            src_codes_cur = [c for c, _ in LANGS]
            dst_cur = [c for c, _ in LANGS if c != "auto"]
            new_src = selected_dst(dst_dd)
            new_dst = selected(src_dd)
            if new_src == "auto":
                new_src = "en"
            src_dd.set_selected(src_codes_cur.index(new_src) if new_src in src_codes_cur else 0)
            dst_dd.set_selected(dst_cur.index(new_dst) if new_dst in dst_cur else 0)
            original = right["buffer"].get_text(
                right["buffer"].get_start_iter(), right["buffer"].get_end_iter(), False
            )
            if original.strip():
                left["buffer"].set_text(original)
            else:
                schedule()

        btn_swap.connect("clicked", on_swap)
        src_dd.connect("notify::selected", schedule)
        dst_dd.connect("notify::selected", schedule)

        def on_clear(_b) -> None:
            left["buffer"].set_text("")
            right["buffer"].set_text("")
            status.set_label("")

        left["clear"].connect("clicked", on_clear)

        def on_copy(_b) -> None:
            text = right["buffer"].get_text(
                right["buffer"].get_start_iter(), right["buffer"].get_end_iter(), False
            )
            if not text.strip():
                return
            if copy_to_clipboard(text):
                status.set_label("Скопировано")
            else:
                status.set_label("Не удалось скопировать (нет wl-copy)")
            status.remove_css_class("status-err")

        right["clear"].connect("clicked", on_copy)

        win.set_child(root)
        def on_close(*_a) -> bool:
            tr.cache_flush(force=True)
            if win_ref and win_ref[0] is win:
                win_ref.clear()
            # Without quitting, the process kept running with no window and the
            # next launch added one more instead of reusing this one.
            app.quit()
            return False

        win.connect("close-request", on_close)
        if initial:
            left["buffer"].set_text(initial)
            schedule()
        win.present()

    app.connect("activate", on_activate)
    return int(app.run([]))


def _lang_dropdown(Gtk, codes: list[str], value: str):  # noqa: ANN001
    labels = [dict(LANGS).get(c, c) for c in codes]
    dd = Gtk.DropDown.new_from_strings(labels)
    try:
        dd.set_selected(codes.index(value))
    except ValueError:
        dd.set_selected(0)
    return dd


def _pane(Gtk, editable: bool, hint: str) -> dict:  # noqa: ANN001
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.add_css_class("card")
    card.set_hexpand(True)
    card.set_vexpand(True)
    for m, v in (("top", 12), ("bottom", 10), ("start", 14), ("end", 14)):
        getattr(card, f"set_margin_{m}")(v)

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    top.set_halign(Gtk.Align.END)
    if editable:
        clear_btn = Gtk.Button(label="✕")
        clear_btn.add_css_class("iconbtn")
    else:
        # The result is read-only, so what a user wants from it is to take it
        # away. Without this the only way to get the translation out was to
        # select the text by hand in a field that used to be editable.
        clear_btn = Gtk.Button(label="Копировать")
        clear_btn.add_css_class("iconbtn")
    top.append(clear_btn)
    card.append(top)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_vexpand(True)
    scrolled.set_hexpand(True)
    scrolled.set_min_content_height(220)

    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD)
    view.set_left_margin(10)
    view.set_right_margin(10)
    view.set_top_margin(8)
    view.set_bottom_margin(8)
    view.set_editable(editable)
    view.add_css_class("big")
    scrolled.set_child(view)
    card.append(scrolled)

    foot = Gtk.Label(label=hint, xalign=0.0)
    foot.add_css_class("hint")
    card.append(foot)

    return {"card": card, "view": view, "buffer": view.get_buffer(), "clear": clear_btn}
