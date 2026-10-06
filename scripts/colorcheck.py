#!/usr/bin/env python3
"""Цвета, которые оверлей возьмёт из скриншота, и какие пиксели на экране есть.

Скрипт отвечает на вопрос, который иначе приходится решать глазами: тот ли цвет
плашки и тот ли цвет букв, что на кадре. На входе — PNG из screencheck
(`wsNN-before.png`), тот самый кадр, из которого движок читает глифы, поэтому
здесь видно ровно то, что увидит подложка.

Цвета сравниваются с оригиналом по каждому каналу и по контрасту, а не «на глаз»:
промах в один канал на 8 уровней — это уже та разница, из-за которой карточка
читается как подставленная.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live import (
    rapid_ocr_lines,  # noqa: E402
    sample_ocr_cover_colors,  # noqa: E402
)

# Порог, за которым разницу видно глазом на соседних участках экрана.
CHANNEL_TOLERANCE = 8


def _rgb255(c: tuple[float, float, float, float]) -> tuple[int, int, int]:
    return tuple(int(round(v * 255)) for v in c[:3])


def _dominant_near(
    img: Image.Image, box: tuple[int, int, int, int], margin: int = 2
) -> tuple[int, int, int] | None:
    """Цвет подложки вплотную к боксу — то, что плашка закрывает.

    Внутри бокса лежит сам текст: у крупного заголовка бокс забит глифами целиком,
    и «самый частый цвет» внутри оказывается цветом букв. Сравнивать плашку с
    этим нельзя — ложная ошибка на каждом жирном слове.

    Кольцо наружу берётся тонким и прилегающим: широкое кольцо находит соседние
    панели и элементы интерфейса, и тогда сравнение снова говорит не о том. Здесь
    нужен цвет в двух пикселях от границы глифов, а не цвет экрана вообще.
    """
    import numpy as np

    x1, y1, x2, y2 = box
    w, h = img.size
    ox1, oy1 = max(0, x1 - margin), max(0, y1 - margin)
    ox2, oy2 = min(w, x2 + margin), min(h, y2 + margin)
    if (ox2 - ox1) * (oy2 - oy1) - (x2 - x1) * (y2 - y1) <= 8:
        return None  # окно у края экрана — вокруг него может не быть места

    outer = np.asarray(img.crop((ox1, oy1, ox2, oy2)).convert("RGB"), dtype=np.uint8)
    inner_mask = np.zeros(outer.shape[:2], bool)
    inner_mask[y1 - oy1 : y2 - oy1, x1 - ox1 : x2 - ox1] = True
    arr = outer[~inner_mask].reshape(-1, 3)
    if arr.size == 0:
        return None
    q = (arr >> 3) << 3
    keys, counts = np.unique(q, axis=0, return_counts=True)
    pick = keys[int(np.argmax(counts))]
    exact = arr[np.all(q == pick, axis=1)]
    med = np.median(exact, axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def check(path: Path, verbose: bool) -> int:
    img = Image.open(path).convert("RGB")
    rows = rapid_ocr_lines(img)
    if not rows:
        print(f"{path.name}: OCR ничего не нашёл")
        return 1

    bad = 0
    print(f"{path.name}: {len(rows)} строк(ы)")
    for r in rows:
        text = r["text"]
        box = tuple(int(v) for v in r["box"])
        got = sample_ocr_cover_colors(img, box, prefer_light_bg=False)
        if got is None:
            if verbose:
                print(f"  — нет пары цветов: {text[:44]!r}")
            continue
        bg, fg = got
        bgpx, fgpx = _rgb255(bg), _rgb255(fg)
        real_bg = _dominant_near(img, box)
        d_bg = (
            max(abs(a - b) for a, b in zip(bgpx, real_bg)) if real_bg else 0
        )
        flag = "OK " if d_bg <= CHANNEL_TOLERANCE else "Δ  "
        if d_bg > CHANNEL_TOLERANCE:
            bad += 1
        if verbose or d_bg > CHANNEL_TOLERANCE:
            print(
                f"  {flag} {text[:34]:36s} плашка={bgpx} глиф={fgpx} "
                f"фон_на_экране={real_bg} расхождение={d_bg}"
            )

    print(f"расхождений фона: {bad}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("shot", type=Path, help="PNG из screencheck (wsNN-before.png)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    a = ap.parse_args()

    if not a.shot.is_file():
        print(f"нет файла: {a.shot}")
        return 2

    if a.json:
        img = Image.open(a.shot).convert("RGB")
        out = []
        for r in rapid_ocr_lines(img):
            box = tuple(int(v) for v in r["box"])
            got = sample_ocr_cover_colors(img, box, prefer_light_bg=False)
            if got is None:
                continue
            bg, fg = got
            real = _dominant_near(img, box)
            out.append(
                {
                    "text": r["text"],
                    "card_bg": _rgb255(bg),
                    "glyph": _rgb255(fg),
                    "screen_bg": list(real) if real else None,
                    "delta": (
                        max(abs(a - b) for a, b in zip(_rgb255(bg), real))
                        if real
                        else 0
                    ),
                }
            )
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    return 1 if check(a.shot, a.verbose) else 0


if __name__ == "__main__":
    raise SystemExit(main())