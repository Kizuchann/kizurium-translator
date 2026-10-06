"""Run the translator against the real screen and check what it drew.

The screenshots this validates against are open fullscreen on separate
workspaces, each with a different text layout, so the checks below are about
the geometry of the result rather than any particular game's wording:

  - did the overlay appear at all
  - did a card land on the text it claims to replace
  - is the translation inside its own card, or spilling over the neighbour
  - did a stale card from a previous scene survive

Every run ends on workspace 1. The switch back is in a finally block, so a
failure part-way through still leaves the session where it was found.

Hyprland here is configured in Lua, so a workspace change is a dispatch of
`hl.dsp.focus({workspace=N})` rather than the usual `workspace N`.

    python scripts/screencheck.py --list
    python scripts/screencheck.py --live 5,4 --settle 9
    python scripts/screencheck.py --live 10 --ocr-copy
    python scripts/screencheck.py --scene 1,5 --settle 6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / ".local" / "screencheck"
HOME_WS = 1


# --------------------------------------------------------------- compositor


def _hypr(*args: str) -> str:
    return subprocess.run(
        ["hyprctl", *args], capture_output=True, text=True, timeout=15
    ).stdout.strip()


def active_ws() -> int:
    out = _hypr("activeworkspace")
    m = re.search(r"workspace ID (-?\d+)", out)
    return int(m.group(1)) if m else 0


def go_ws(n: int) -> bool:
    """Switch workspace. Hyprland is Lua-configured here, so the dispatch
    syntax differs from the stock one."""
    r = subprocess.run(
        ["hyprctl", "dispatch", f"hl.dsp.focus({{workspace={n}}})"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        return False
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if active_ws() == n:
            return True
        time.sleep(0.1)
    return False


def list_workspaces() -> list[dict]:
    """Workspaces that hold a window, with the window class and title.

    hyprctl indents with tabs and prints one block per window that opens with
    the address. Fields are gathered per block, and the block boundary is the
    next address rather than the workspace line, because class and title come
    after it.
    """
    blocks: list[dict] = []
    cur: dict = {}
    for raw_line in _hypr("clients").splitlines():
        line = raw_line.strip()
        if line.startswith("Window "):
            if cur:
                blocks.append(cur)
            cur = {"addr": line.split()[1].split("->")[0].strip()}
        elif cur:
            if line.startswith("class:"):
                cur["class"] = line.split(":", 1)[1].strip()
            elif line.startswith("title:"):
                cur["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("workspace:"):
                # "workspace: 3 (3)" in hyprctl clients, not the
                # "workspace ID 3" form used by `hyprctl workspaces`.
                m = re.match(r"workspace:\s*(-?\d+)", line)
                cur["ws"] = int(m.group(1)) if m else 0
    if cur:
        blocks.append(cur)
    return sorted((w for w in blocks if w.get("ws", 0) > 0), key=lambda w: w["ws"])


# ------------------------------------------------------------------ capture


def _active_geometry(ws: int = 0) -> str:
    """Capture area for the workspace under test, not a hardcoded 0,0 1920x1080.

    With a second monitor attached Hyprland stops putting the primary screen at
    the origin: eDP-1 moves to x=3286 and the new output takes 0..1920. Capturing
    "0,0 1920x1080" then photographs the wrong screen, and screencheck failed with
    "не удалось снять фон" on every window.

    Asking for the monitor of the *active window* was also wrong. screencheck
    switches workspace and shoots straight away, and focus need not have moved by
    then: measured on one workspace the frame came from the first monitor and the
    log filled with the text of an unrelated window ("Context 296,754 tokens",
    `import json, sys`). The whole second monitor was being measured as the first
    one, and reported as clean.

    What the harness has and the active-window version did not is the workspace
    it is about to check, so that is what the monitor is looked up by -
    `hyprctl monitors` reports the active workspace of every output.

    Focus is the fallback for a workspace that is on no output at all (empty
    workspace, window moved away), and the first output is the last resort. There
    is no "0,0 1920x1080" anywhere: on a two-monitor layout that rectangle is not
    the screen anything is drawn on.
    """
    import json

    try:
        ms = json.loads(
            subprocess.run(
                ["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=15
            ).stdout
        )
    except (ValueError, TypeError):
        ms = []
    if not ms:
 # Never invent a 1920×1080 panel — that only matched one laptop layout.
        raise RuntimeError(
            "hyprctl monitors пуст: нечего снимать. "
            "Укажи --region явно или проверь Wayland/Hyprland."
        )

    def geom(m):
        return f"{m['x']},{m['y']} {m['width']}x{m['height']}"

    for m in ms:
 # The workspace under test wins over anything the compositor considers
 # active: that is the whole point of passing it in.
        if ws and (m.get("activeWorkspace") or {}).get("id") == ws:
            return geom(m)

 # Fall back to the focused window. Its `monitor` field is a numeric id, not
 # the output name; matching on the name finds nothing and used to drop through
 # to the hardcoded rectangle.
    try:
        aw = json.loads(
            subprocess.run(
                ["hyprctl", "-j", "activewindow"],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )
        mon_id = aw.get("monitor")
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        mon_id = None
    for m in ms:
        if mon_id is not None and m.get("id") == mon_id:
            return geom(m)
    return geom(ms[0])


def shoot_best(path: Path, before: Path | None, ws: int, tries: int = 5) -> bool:
    """Shoot repeatedly and keep the frame that differs most from `before`.

    The overlay hides itself for a moment before every capture, so that the
    frame records what is under the cards rather than the cards. A single shot
    lands in that gap about as often as not, and the run then looks clean while
    showing no translation at all - which is exactly what one run reported, next
    to a screenshot full of English.

    The frames are compared to the pre-overlay shot of the same workspace: the
    cards change the picture, a hidden overlay changes nothing. More changed
    pixels means the overlay was actually up when that frame was taken.
    """
    base = None
    if before is not None and before.is_file():
        try:
            base = Image.open(before).convert("RGB")
        except OSError:
            base = None

    best_pixels = -1
    ok = False
    for attempt in range(max(1, tries)):
        target = path if attempt == 0 else path.with_suffix(f".try{attempt}.png")
        if not shoot(target, ws):
            continue
        ok = True
        if base is None:
            break
        try:
            shot = Image.open(target).convert("RGB")
        except OSError:
            continue
        if shot.size != base.size:
            continue
 # Downscaled before comparing: the two frames only differ where the cards
 # are, and a full-resolution pixel count is far more work than the answer
 # needs.
        small_s = shot.resize((320, 180), Image.BILINEAR)
        small_b = base.resize((320, 180), Image.BILINEAR)
        changed = sum(
            1
            for a, b in zip(small_s.getdata(), small_b.getdata())
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 24
        )
        if changed > best_pixels:
            best_pixels = changed
            if target != path:
                path.write_bytes(target.read_bytes())
        for extra in range(1, max(1, tries)):
            stale = path.with_suffix(f".try{extra}.png")
            if stale.is_file():
                stale.unlink()
    return ok


def shoot(path: Path, ws: int = 0) -> bool:
    p = path.parent / (path.stem + ".png")
    try:
        geom = _active_geometry(ws)
    except RuntimeError as exc:
        print(f"[screencheck] {exc}", flush=True)
        return False
    r = subprocess.run(
        ["grim", "-g", geom, "-"], capture_output=True, timeout=30
    )
    if r.returncode != 0 or not r.stdout:
        return False
    p.write_bytes(r.stdout)
    return True


# --------------------------------------------------------------- translator


def live_running() -> int:
    r = subprocess.run(
        ["kizurium-translator", "--status"], capture_output=True, text=True, timeout=30
    )
    m = re.search(r"pid=(\d+)", r.stdout)
    return int(m.group(1)) if m else 0


def stop_live() -> None:
    subprocess.run(
        ["kizurium-translator", "--stop"], capture_output=True, timeout=40
    )
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and live_running():
        time.sleep(0.2)


def start_live(region: str, log: Path, target: str = "") -> int:
    """Start the overlay detached and return its pid, or 0.

    The engine log is the state file, not the descriptor below: the logger was
    pointed at XDG_STATE_HOME so it survives the session that wrote it, and it
    is the same file every run. ``log`` receives whatever the process writes to
    stdout and stderr, which after the change is almost nothing.

    setsid matters: without it this process's own command line contains
    `--live`, and the translator's scan for an existing session matches it and
    refuses to start.
    """
    env = dict(os.environ, GTK_A11Y="none")
    cmd = ["kizurium-translator", "--live", "--region", region]
    if target:
        cmd += ["--target", target]
    with log.open("wb") as fh:
        subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if live_running():
            return live_running()
        if "layer-window" in engine_log_text():
            time.sleep(0.5)
            return live_running()
        time.sleep(0.4)
    return 0


# ------------------------------------------------------------------- checks


# The prefix carries a timestamp, a level and a logger name, so the pattern
# matches the card line itself and not the start of the line. The old prefix
# stopped matching the moment logging moved to the stdlib, and every check
# reported an empty screen while the overlay was drawing.
CARD = re.compile(r"card (\S+) ocr=(\d+)x(\d+) "
                  r"-> (\d+)x(\d+) font=(\d+) (\S+).* screen=\((\d+),(\d+)\)")


def parse_log(log: Path, since: int = 0) -> dict:
    """What the engine reported, as measurements rather than prose.

    Read from the state log rather than from ``log``: the engine writes there
    now, and the run-local file only carries whatever the process printed
    directly. ``since`` skips everything written before this run started.
    """
    text = engine_log_text()[since:]
    if not text and log.is_file():
        text = log.read_text(errors="replace")
    out: dict = {"cards": [], "cycles": [], "mode": None, "shown": None, "errors": []}
    for line in text.splitlines():
        m = CARD.search(line)
        if m:
            out["cards"].append(
                {
                    "kind": m.group(1),
                    "ocr_w": int(m.group(2)),
                    "ocr_h": int(m.group(3)),
                    "card_w": int(m.group(4)),
                    "card_h": int(m.group(5)),
                    "font": int(m.group(6)),
                    "x": int(m.group(8)),
                    "y": int(m.group(9)),
                }
            )
            continue
        m = re.search(r"cycle_ms=(\d+)", line)
        if m:
            out["cycles"].append(int(m.group(1)))
        m = re.search(r"shown=(\d+)/(\d+)", line)
        if m:
            out["shown"] = {"shown": int(m.group(1)), "total": int(m.group(2))}
        m = re.search(r"\bmode=(\S+)", line)
        if m:
            out["mode"] = m.group(1)
        if "FATAL" in line or "Traceback" in line:
            out["errors"].append(line[-200:])
    return out


def judge(stats: dict) -> tuple[list[str], list[str]]:
    """Findings split into problems and observations.

    The thresholds are not quality targets, they are the point where a result
    stops being explainable by the text being short. A card whose ink box and
    its drawn box disagree by a large factor is a layout decision, not a fit.
    """
    problems: list[str] = []
    notes: list[str] = []

    if not stats["cards"]:
        problems.append("ни одной карточки: оверлей не нарисовал ничего")
        return problems, notes

    if stats["cycles"]:
        worst = max(stats["cycles"])
        if worst > 6000:
            problems.append(f"цикл {worst} мс: медленно, карточки появляются с задержкой")
        else:
            notes.append(f"худший цикл {worst} мс")

 # The engine logs every card on every cycle, so two cycles of a static
 # screen produce two identical lines at the same position. Counting those
 # as an overlap reported thirteen problems on a screen that had none. The
 # verdict is about one frame, so cards are collapsed by position first and
 # the measurement kept is the first of each.
    one_frame: dict[tuple[int, int], dict] = {}
    for c in stats["cards"]:
        one_frame.setdefault((c["x"], c["y"]), c)
    frame = list(one_frame.values())

    grown = [c for c in frame
             if c["ocr_w"] and c["card_w"] > c["ocr_w"] * 1.6]
    if grown:
        worst = max(grown, key=lambda c: c["card_w"] / max(1, c["ocr_w"]))
        problems.append(
            f"{len(grown)} карточек шире оригинала более чем в 1.6 раза "
            f"(худшая {worst['card_w'] / max(1, worst['ocr_w']):.1f}x): "
            "текст вылезает за свою область"
        )

    shrunk = [c for c in frame
              if c["ocr_w"] and c["card_w"] < c["ocr_w"] * 0.55]
    if shrunk:
        problems.append(
            f"{len(shrunk)} карточек заметно уже оригинала: подгонка размера не сработала"
        )

    if stats["shown"] and stats["shown"]["shown"] < stats["shown"]["total"]:
        notes.append(
            f"показано {stats['shown']['shown']} из {stats['shown']['total']}"
        )
    if stats["errors"]:
        problems.append(f"ошибок в логе: {len(stats['errors'])}")
    return problems, notes


def engine_log_path() -> Path:
    """Where the overlay writes now that logging is set up properly."""
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "kizurium-translator" / "overlay.log"


def engine_log_text() -> str:
    try:
        return engine_log_path().read_text(errors="replace")
    except OSError:
        return ""


def mark_log() -> int:
    """Remember where the log ends, so one run reads only its own lines.

    The engine log is persistent by design, which means a run that appends to it
    also sees everything the previous runs wrote. Without this, a defect found
    two workspaces ago is reported again on every window after it, and the
    numbers grow with each pass.
    """
    return len(engine_log_text())


def _cache_path() -> Path:
    from kizurium_translator.paths import default_paths

    return default_paths().translate_cache


def cache_info() -> dict:
    """Что лежит в кэше переводов. Файл — SQLite, не JSON."""
    import sqlite3

    p = _cache_path()
    if not p.is_file():
        return {"path": str(p), "entries": 0, "exists": False}
    try:
        rows = sqlite3.connect(p).execute(
            "SELECT source_lang, target_lang FROM translations"
        ).fetchall()
    except sqlite3.Error:
        return {"path": str(p), "entries": 0, "exists": True, "error": "unreadable"}
    pairs: dict[str, int] = {}
    for src, tgt in rows:
        label = f"{src}->{tgt}"
        pairs[label] = pairs.get(label, 0) + 1
    return {"path": str(p), "entries": len(rows), "exists": True, "pairs": pairs}


def clear_cache() -> dict:
    """Удаляет кэш переводов. Старый JSON тоже, если он ещё лежит рядом."""
    from kizurium_translator.paths import retire_legacy_translation_caches

    p = _cache_path()
    removed = retire_legacy_translation_caches(p.parent)
    cleared = False
    for path in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
        if path.is_file():
            path.unlink()
            cleared = True
    return {"cleared": cleared or bool(removed), "path": str(p), "legacy": [str(x) for x in removed]}


# --------------------------------------------------------------------- runs


def check_ws(ws: int, region: str, settle: float, target: str = "") -> dict:
    """One workspace: before, overlay, after."""
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"ws{ws:02d}"
    res: dict = {"ws": ws}

    if not go_ws(ws):
        res["error"] = "не удалось переключиться на воркспейс"
        return res

    time.sleep(1.2)
 # Только теперь: `_active_geometry` смотрит на activeWorkspace каждого выхода,
 # а до переключения там ещё воркспейс предыдущего окна. Посчитанная заранее
 # область указывала на чужой монитор - переводчик читал первый экран, пока
 # снимок брался со второго.
    if not region:
        try:
            region = _active_geometry(ws)
        except RuntimeError as exc:
            res["error"] = str(exc)
            return res
    if not shoot(stem.with_name(stem.name + "-before.png"), ws):
        res["error"] = "не удалось снять фон"
        return res

    log = stem.with_suffix(".log")
    if log.exists():
        log.unlink()
    since = mark_log()
    pid = start_live(region, log, target)
    res["pid"] = pid
    if not pid:
        res["error"] = "live не стартовал"
        res["log_tail"] = engine_log_text()[-400:]
        stop_live()
        return res

    time.sleep(settle)
    shoot_best(
        stem.with_name(stem.name + "-after.png"),
        stem.with_name(stem.name + "-before.png"),
        ws,
    )
    stop_live()
    time.sleep(0.8)

    stats = parse_log(log, since)
    res["stats"] = stats
    res["problems"], res["notes"] = judge(stats)
    res["before"] = str(stem.with_name(stem.name + "-before.png"))
    res["after"] = str(stem.with_name(stem.name + "-after.png"))
    return res


def check_chain(workspaces: list[int], region: str, settle: float,
                target: str = "") -> dict:
    """One live session, walked across several scenes.

    This is the shape the overlay actually runs in. A per-workspace check
    starts the overlay, looks at one frame and stops, which cannot see the
    failure that matters most: what is left on screen when the scene underneath
    is replaced. Starting once and walking the workspaces reproduces it, and so
    does walking back, which is the direction where a stale card would return
    over a scene that no longer contains it.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    first = workspaces[0]
    res: dict = {"scene": "->".join(str(w) for w in workspaces), "steps": []}

    if not go_ws(first):
        res["error"] = f"не удалось перейти на {first}"
        return res
    time.sleep(1.2)
 # Область по умолчанию - монитор первого воркспейса цепочки, и берётся она
 # после переключения: `_active_geometry` смотрит на activeWorkspace каждого
 # выхода, а до `go_ws` там ещё воркспейс предыдущего окна.
    if not region:
        try:
            region = _active_geometry(first)
        except RuntimeError as exc:
            res["error"] = str(exc)
            return res

    log = OUT / f"chain-{'_'.join(str(w) for w in workspaces)}.log"
    if log.exists():
        log.unlink()
    since = mark_log()
    pid = start_live(region, log, target)
    res["pid"] = pid
    if not pid:
        res["error"] = "live не стартовал"
        res["log_tail"] = engine_log_text()[-400:]
        stop_live()
        return res

    for ws in workspaces:
        if not go_ws(ws):
            res["error"] = f"не удалось перейти на {ws}"
            break
        time.sleep(settle)
        shot = OUT / f"chain-{'_'.join(str(w) for w in workspaces)}-ws{ws:02d}.png"
        shoot(shot, ws)
        res["steps"].append({"ws": ws, "shot": str(shot)})
    else:
 # Returning to a scene already visited is the interesting direction: a
 # card left over from the first visit would now sit on top of it.
        if not go_ws(first):
            res["error"] = f"не удалось вернуться на {first}"
        else:
            time.sleep(settle)
            shot = OUT / f"chain-{'_'.join(str(w) for w in workspaces)}-ws{first:02d}-back.png"
            shoot(shot, first)
            res["steps"].append({"ws": first, "shot": str(shot), "return": True})

    stop_live()
    time.sleep(0.8)

    stats = parse_log(log, since)
    res["stats"] = stats
    res["problems"], res["notes"] = judge(stats)
    res["full_log"] = str(log)
    return res


def ocr_copy(ws: int, region: str) -> dict:
    """OCR mode: the text must arrive in the clipboard, untranslated."""
    OUT.mkdir(parents=True, exist_ok=True)
    res: dict = {"ws": ws, "mode": "ocr-copy"}
    if not go_ws(ws):
        res["error"] = "не удалось переключиться"
        return res
    time.sleep(1.0)
    before = subprocess.run(["wl-paste", "-n"], capture_output=True, timeout=15)
    before_text = before.stdout.decode("utf-8", "replace")

    r = subprocess.run(
        ["kizurium-translator", "--ocr-copy", "--region", region],
        capture_output=True, text=True, timeout=180,
    )
    res["returncode"] = r.returncode
    res["stdout"] = r.stdout.strip()[-400:]
    res["stderr"] = r.stderr.strip()[-400:]

    after = subprocess.run(["wl-paste", "-n"], capture_output=True, timeout=15)
    after_text = after.stdout.decode("utf-8", "replace")
    res["changed"] = after_text != before_text
    res["chars"] = len(after_text.strip())
    res["text"] = after_text.strip()[:1200]
    if not res["changed"]:
        res["error"] = "буфер не изменился"
    elif not res["chars"]:
        res["error"] = "в буфер попал пустой текст"
    return res


# --------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="показать воркспейсы")
    ap.add_argument("--live", metavar="WS", help="проверить live на воркспейсах, через запятую")
    ap.add_argument("--chain", metavar="WS,WS,...",
                    help="одна сессия live через несколько окон, с возвратом на первое")
    ap.add_argument("--ocr-copy", metavar="WS", action="append",
                    help="проверить копирование OCR на воркспейсе")
    ap.add_argument(
        "--region",
        default="",
        help="область live; пусто = весь монитор, на котором лежит проверяемый "
             "воркспейс (определяется после переключения на него)",
    )
    ap.add_argument(
        "--target",
        default="",
        help="язык назначения для проверки; пусто = что есть в конфиге. "
             "Нужен, чтобы убедиться, что перевод на не-русский работает, "
             "а не только что русский не сломан",
    )
    ap.add_argument("--settle", type=float, default=9.0,
                    help="секунд ждать перевода на сцену")
    ap.add_argument("--all", action="store_true", help="все занятые воркспейсы")
    ap.add_argument("--cache", action="store_true", help="показать состояние кэша")
    ap.add_argument("--clear-cache", action="store_true",
                    help="убрать кэш переводов в сторону, чтобы новая правка была видна")
    args = ap.parse_args()

    if args.cache:
        info = cache_info()
        print(f"  {info['path']}")
        print(f"  записей: {info['entries']}")
        for k, v in sorted((info.get("pairs") or {}).items()):
            print(f"    {k:12} {v}")
        return 0

    if args.clear_cache:
        r = clear_cache()
        print(f"  кэш убран: {r['cleared']}"
              + (f"\n  копия: {r['backup']}" if r.get("backup") else f" ({r.get('reason','')})"))
        return 0

    if args.list:
        for w in list_workspaces():
            print(f"  ws {w['ws']:>3}  {w.get('class', '?'):<28} {w.get('title', '')[:60]}")
        return 0

    results = []
    try:
        if args.live or args.all:
            targets = ([int(x) for x in args.live.split(",")] if args.live
                       else [w["ws"] for w in list_workspaces()
                             if w["ws"] != HOME_WS])
            for ws in targets:
                print(f"[screencheck] live ws={ws}", flush=True)
 # Пустая область = «весь монитор проверяемого воркспейса»,
 # и она разрешается уже после переключения на него. Раньше здесь
 # стояло «0,0 1920x1080»: переводчик всегда работал на первом
 # мониторе, пока снимок брался с другого.
                results.append(check_ws(ws, args.region, args.settle, args.target))
        if args.chain:
            chain = [int(x) for x in args.chain.split(",")]
            print(f"[screencheck] chain {'->'.join(map(str, chain))} (с возвратом)",
                  flush=True)
            results.append(check_chain(chain, args.region, args.settle, args.target))
        for ws in args.ocr_copy or []:
            print(f"[screencheck] ocr-copy ws={ws}", flush=True)
            results.append(ocr_copy(int(ws), args.region))
    finally:
        stop_live()
        if active_ws() != HOME_WS:
            go_ws(HOME_WS)
        print(f"[screencheck] вернулся на воркспейс {active_ws()}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))

    bad = 0
    for r in results:
        head = r.get("scene") or f"ws{r.get('ws')}"
        print(f"\n{'=' * 66}\n{head}")
        if r.get("error"):
            print(f"  ОШИБКА: {r['error']}")
            if r.get("log_tail"):
                print(f"  лог: {r['log_tail'][-260:]}")
            bad += 1
            continue
        st = r.get("stats") or {}
        print(f"  режим={st.get('mode')} карточек={len(st.get('cards', []))} "
              f"циклов={len(st.get('cycles', []))} {r.get('shown') or ''}")
        for n in r.get("notes", []):
            print(f"  · {n}")
        for p in r.get("problems", []):
            print(f"  ✗ {p}")
            bad += 1
        if r.get("text"):
            print(f"  текст ({r['chars']} симв.): {r['text'][:300]!r}")
    print(f"\n{'=' * 66}\nпроблем: {bad}\nснимки: {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())