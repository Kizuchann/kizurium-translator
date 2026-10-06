#!/usr/bin/env python3
"""Рисует карточки поверх сохранённого кадра, без окна и без живой сессии.

Тот же путь, что проходит кадр в `worker`: чтение, раскладка строк, перевод,
построение карточек и их отрисовка. Нужен, чтобы сравнить две версии движка на
одних и тех же пикселях: живой прогон каждый раз видит чуть другой кадр и
другое состояние кэша, а здесь меняется только код.

    scripts/offline_render.py.local/frames/frame.png -o.local/renders/out
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cairo  # noqa: E402
from PIL import Image  # noqa: E402

from kizurium_translator import config as config_mod  # noqa: E402
from kizurium_translator.live import runtime  # noqa: E402
from kizurium_translator.live.build import build_blocks  # noqa: E402
from kizurium_translator.live.prepare import arrange_lines  # noqa: E402
from kizurium_translator.live.state import block_source_lang  # noqa: E402
from kizurium_translator.ocr.engine import is_garbage_ocr, skip_source  # noqa: E402
from kizurium_translator.ocr.frame import read_frame  # noqa: E402
from kizurium_translator.render.overlay import draw_blocks  # noqa: E402
from kizurium_translator.translation.batch import translate_many  # noqa: E402


def render(path: Path, out_dir: Path) -> Path:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    lines, mode = read_frame(img, None)
    japanese_mode = mode == "jpn-game"
    subtitle_mode = mode == "eng-subtitle"
    eng_ui_mode = mode == "eng-ui"
    lines = arrange_lines(
        lines, img, w, h, [], japanese_mode, subtitle_mode, eng_ui_mode, mode or ""
    )
    pars = [
        p
        for p in lines
        if float(p.get("conf", 0)) >= 18
        and (japanese_mode or not skip_source(p["text"]))
        and not is_garbage_ocr(p["text"])
    ]
    items = []
    spans = []
    for par in pars:
        parts = str(par["text"]).split("\n")
        spans.append((len(items), len(parts)))
        items.extend({"text": t, "source": block_source_lang(par, t)} for t in parts)
    flat = translate_many(items, {}) if items else []
    translations = ["\n".join(flat[s: s + n]) for s, n in spans]
    blocks = build_blocks(
        zip(pars, translations),
        japanese_mode=japanese_mode,
        dialogue_mode=subtitle_mode or japanese_mode,
        eng_ui_mode=eng_ui_mode,
        region_img=img,
        rx=0,
        ry=0,
        rw=w,
        rh=h,
    )
    if os.environ.get("KZ_DUMP"):
        keys = ("text", "source", "x", "y", "src_w", "src_h", "angle", "font", "grow_limit", "bg", "fg", "color_spans")
        for b in blocks:
            row = {k: b.get(k) for k in keys if k in b}
            for k in ("bg", "fg"):
                if isinstance(row.get(k), (tuple, list)):
                    row[k] = tuple(round(float(v), 2) for v in row[k])
            if row.get("color_spans"):
                row["color_spans"] = [
                    (tuple(round(float(c), 2) for c in sp["color"][:3]), round(sp["share"], 2))
                    for sp in row["color_spans"]
                ]
            print("  ", row)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    draw_blocks(cr, blocks, (0, 0, w, h), "")
    surface.flush()
    layer = Image.frombuffer(
        "RGBA", (w, h), bytes(surface.get_data()), "raw", "BGRa", surface.get_stride()
    )
    out = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / path.name
    out.save(target)
    print(f"{path.name}: mode={mode} lines={len(lines)} cards={len(blocks)} -> {target}")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("frames", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("/tmp/kz-offline"))
    args = ap.parse_args()
    runtime.configure(config_mod.load())
    for frame in args.frames:
        try:
            render(frame, args.out)
        except Exception as exc:  # noqa: BLE001
            print(f"{frame.name}: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
