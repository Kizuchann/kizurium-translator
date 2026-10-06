"""Выбрать окна для живого прогона.

Прогоны шли по одному и тому же списку, и это плохо: дефект, который видно
на одном кадре, на остальных двадцати трёх может и не проявиться, а
исправление, проверенное двадцатью тремя раза, ни разу не встретит того,
ради чего чинилось. Окна должны меняться от прогона к прогону.

Что здесь сделано:

- `list_workspaces()` уже разбирает вывод `hyprctl`, поэтому источник истины
  один - тот же список, что видит `--list`;
- игнорируется рабочий воркспейс и всё, что не является игровым кадром:
  терминал и браузер трогаются только когда их явно дают как тестовое окно;
- `--seed` делает выбор воспроизводимым: тот же набор возвращается по тому
  же зерну, и прогон можно повторить, когда он что-то нашёл;
- размер набора по умолчанию - 6 окон плюс возврат на первое, то есть
  цепочка в семь переходов: этого хватает, чтобы увидеть и реакцию на смену
  сцены, и возврат на пройденную, не превращая прогон в двадцать минут.

Запуск:

    python scripts/pick_windows.py --print
    python scripts/pick_windows.py --count 8
    python scripts/pick_windows.py --count 8 --seed 42 --print
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from pathlib import Path

HOME_WS = 1

# Окна, которые не берутся без явного разрешения: терминал и браузер.
# Список назван здесь, а не молча пропущен, чтобы следующий человек увидел
# причину и не потратил прогон на выяснение, куда делось одно окно.
SKIP_APPS = frozenset({"kitty", "firefox", "alacritty", "foot", "ghostty"})

# Окно, занятое человеком. Переключение на него во время живой сессии
# выкидывает его из-под игры, поэтому оно исключается поимённо.
# Меняется вручную: `kizurium-translator` с окном на руках и прогон с
# переключениями несовместимы.
BUSY_WS: frozenset[int] = frozenset({2})


def list_workspaces() -> list[dict]:
    """Рабочие области как их видит compositor."""
    r = subprocess.run(
        ["hyprctl", "clients", "-j"],
        capture_output=True, text=True, timeout=20,
    )
    try:
        import json
    except ModuleNotFoundError:
        raise SystemExit("нужен json")
    try:
        clients = json.loads(r.stdout or "[]")
    except ValueError:
        raise SystemExit(f"hyprctl clients отдал не json: {r.stdout[:200]!r}")
    return clients


def by_workspace(clients: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for c in clients:
        ws = c.get("workspace", {})
        name = (ws.get("name") if isinstance(ws, dict) else ws) or ""
        try:
            num = int(str(name).lstrip("-"))
        except ValueError:
            continue
        cls = c.get("class", "")
        if num not in out or (not out[num].get("class") and cls):
            out[num] = {"ws": num, "class": cls, "title": c.get("title", "")[:48]}
    return out


def pick(count: int, seed: int | None, apps: set[str] | None = None) -> list[int]:
    """Разные окна, отличные от прошлых прогонов."""
    skip = SKIP_APPS if apps is None else apps
    rooms = [
        r for ws, r in sorted(by_workspace(list_workspaces()).items())
        if ws != HOME_WS and ws not in BUSY_WS and r["class"] not in skip
    ]
    if not rooms:
        raise SystemExit("нет ни одного игрового окна")
    rng = random.Random(seed)
 # count больше, чем окон, - берём сколько есть, и это не ошибка:
 # повторять окно дважды в одной цепочке бессмысленно.
    return sorted(rng.sample([r["ws"] for r in rooms], min(count, len(rooms))))


def chain(count: int, seed: int | None, apps: set[str] | None = None) -> list[int]:
    """Цепочка с возвратом на первое окно.

    Возврат - половина смысла: оставшаяся карточка из прошлого визита
    вернулась бы поверх сцены, которой на экране уже нет.
    """
    first = pick(count, seed, apps)
    if len(first) < 2:
        return first
    return first + [first[0]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=6, help="сколько окон (по умолчанию 6)")
    ap.add_argument("--seed", type=int, default=None, help="зерно: тот же набор по тому же зерну")
    ap.add_argument("--no-return", action="store_true", help="без возврата на первое окно")
    ap.add_argument("--print", action="store_true", help="напечатать окна и выйти")
    args = ap.parse_args()

    ws = pick(args.count, args.seed) if args.no_return else chain(args.count, args.seed)
    if args.print:
        rooms = by_workspace(list_workspaces())
        for n in ws:
            info = rooms.get(n, {})
            print(f"  ws{n:02d}  {info.get('class', '?'):22} {info.get('title', '')}")
        return 0
    print(",".join(str(n) for n in ws))
    return 0


if __name__ == "__main__":
    sys.exit(main())
