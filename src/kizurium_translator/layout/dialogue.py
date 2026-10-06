"""Склейка реплик, фуриганы и двуязычных строк.

Тела перенесены из `live/session.py` дословно. Это раскладка уже прочитанных
строк, а не решение, читать ли кадр.
"""
from __future__ import annotations

import re
from statistics import median

from PIL import Image

from ..core.scripts import pattern_of, script_of, target_language
from ..core.text import (
    UI_SHORT_LABELS,
    box_overlap_ratio,
    is_hud_spam,
    looks_like_spoken_line,
    normalize_subtitle_ocr,
    tdetail,
    tlog,
)
from ..ocr.engine import (
    is_garbage_ocr,
    is_overlay_echo_ocr,
    normalize_japanese_text,
    stitch_rows_by_baseline,
)
from ..ocr.passes import _ocr_scaled_panel
from ..translation.service import is_furigana_reading, is_speaker_name
from ..typography.metrics import (
    RE_JPN,
    dedupe_near_ui_lines,
    is_latin_label,
    looks_like_game_ui,
    unglue_english,
)
from .grouping import filter_game_ui_lines, is_desktop_chrome


def merge_spoken_with_hud(
    region_img: Image.Image,
    spoken_blocks: list[dict],
    band_only: list[dict],
    choices: list[dict] | None = None,
) -> tuple[list[dict], str]:
    """Субтитры вместе с HUD (квест/Guard/Map).

    Область реплики не вытесняет остальные блоки: HUD на том же экране
    переводится независимо от того, распознан ли рядом диалог.
    """
    choices = choices or []
    w, h = region_img.size
    spoken_boxes = [b["box"] for b in spoken_blocks if b.get("box")]

    def _hits_spoken(p: dict) -> bool:
        pb = p.get("box")
        if not pb or not spoken_boxes:
            return False
        return any(box_overlap_ratio(pb, sb) >= 0.28 for sb in spoken_boxes)

    hud: list[dict] = []
    for p in band_only:
        t = re.sub(r"^[@✦◆◇●○■□★☆]+", "", str(p.get("text") or "").strip()).strip()
        if not t or looks_like_spoken_line(t) or _hits_spoken(p):
            continue
        if is_overlay_echo_ocr(t):
            continue
        copy = dict(p)
        copy["text"] = t
        hud.append(copy)

    # Верх (квест / Waiting) часто вне subtitle-band (y0≈30%)
    has_top = any(
        re.search(r"search\s+for|waiting\s+to\s+switch|objective|blinded", str(p.get("text") or ""), re.I)
        for p in hud
    )
    if not has_top:
        # Imported here, not at the top of the module: `live/__init__` imports
        # this module, so a module-level import of anything under `live` makes
        # `import kizurium_translator.layout.dialogue` fail on its own with a
        # half-initialised module. It only worked because the CLI happens to
        # import `live` first.
        from ..live.reconcile import rapid_ocr_lines

        try:
            y_top = max(80, int(h * 0.38))
            top = region_img.crop((0, 0, w, y_top))
            top2 = top.resize((top.width * 2, top.height * 2), Image.Resampling.LANCZOS)
            for p in rapid_ocr_lines(top2):
                t = unglue_english(normalize_subtitle_ocr(str(p.get("text") or "")))
                if len(t) < 3 or is_garbage_ocr(t) or looks_like_spoken_line(t):
                    continue
                if is_overlay_echo_ocr(t) or is_hud_spam(t) or is_desktop_chrome(t):
                    continue
                x1, y1, x2, y2 = p["box"]
                hud.append(
                    {
                        "text": t,
                        "box": (int(x1 / 2), int(y1 / 2), int(x2 / 2), int(y2 / 2)),
                        "line_height": max(8, int(p.get("line_height", 14) / 2)),
                        "conf": float(p.get("conf") or 0.0),
                        "kind": "ui",
                    }
                )
        except Exception:
            pass

    # Правый квест-тост: ocr_right_panel_toasts пропускает bottomish-сцены
    try:
        quest = _ocr_scaled_panel(
            region_img,
            (int(w * 0.52), int(h * 0.04), w, int(h * 0.30)),
            2,
            "hud-quest",
            try_descan=False,
        )
        for p in quest:
            if not _hits_spoken(p) and not is_overlay_echo_ocr(str(p.get("text") or "")):
                hud.append(p)
    except Exception:
        pass

    hud = filter_game_ui_lines(hud)
    spoken_norm = {re.sub(r"\W+", "", b["text"].lower()) for b in spoken_blocks}
    hud = [
        p
        for p in hud
        if re.sub(r"\W+", "", str(p.get("text") or "").lower()) not in spoken_norm
    ]
    hud = dedupe_near_ui_lines(hud)

    for b in spoken_blocks:
        b.setdefault("kind", "dialogue")
    combined = list(spoken_blocks) + list(choices) + hud
    if looks_like_game_ui(hud) or len(hud) >= 2:
        tlog(f"spoken+hud n={len(spoken_blocks)}+{len(choices)}+{len(hud)}")
        return combined[:40], "eng-ui"
    return (list(spoken_blocks) + list(choices))[:12], "eng-subtitle"


def drop_target_columns(lines: list[dict], target_lang: str | None = None) -> list[dict]:
    """На двуязычных страницах выкидывает колонки на языке назначения.

    Колонка, уже написанная на языке перевода, - это результат чужого
    перевода, и переводить её заново не нужно. Имя функции раньше говорило про
    русский, и это работало ровно при `target=ru`: русская колонка и была
    переведённой. При `target=en` русская колонка - это исходник, и её
    выбрасывание оставляло на странице ровно то, что переводить надо.

    Имя изменено вместе с поведением: `drop_russian_columns` описывало бы
    теперь не то, что функция делает.
    """
    if len(lines) < 3:
        return lines
    centers = [((p["box"][0] + p["box"][2]) / 2.0, p) for p in lines]
    xs = sorted(c for c, _ in centers)
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    gaps.sort(reverse=True)
    cuts = []
    for gap, i in gaps[:2]:
        if gap > 70:
            cuts.append((xs[i] + xs[i + 1]) / 2.0)
    if not cuts:
        return lines
    cuts.sort()

    def bucket(x: float) -> int:
        for i, cut in enumerate(cuts):
            if x < cut:
                return i
        return len(cuts)

    cols: dict[int, list[dict]] = {}
    for x, p in centers:
        cols.setdefault(bucket(x), []).append(p)

    keep: list[dict] = []
    for group in cols.values():
        blob = " ".join(p["text"] for p in group)
        letters = [c for c in blob if c.isalpha()]
        if not letters:
            continue
        pattern = pattern_of(script_of(target_language()) or "")
        own = sum(1 for c in letters if pattern.match(c))
        if own and (own / len(letters)) >= 0.35:
            continue
        keep.extend(group)
    keep.sort(key=lambda item: (item["box"][1], item["box"][0]))
    return keep if keep else lines


def _join_is_worth_it(
    joined: str, read: tuple[str, float], was: list[tuple[str, float]]
) -> bool:
    """Whether putting the pieces back together is an improvement.

    Not judged on confidence, which is the wrong question here and points the
    wrong way: two halves of a word are each an easier thing to read than the
    whole, so the pieces score higher than the line and a comparison on score
    would always keep the split. That is exactly the case this is here to fix -
    the recogniser was handed half a word and returned a word missing a letter.

    What decides it instead is that the line may not get shorter. Reassembling is
    allowed to fix the spacing, to put back a letter the pieces had lost between
    them, and to turn two cards into one; it is not allowed to throw away a
    letter the pieces did have. The score only has to stay in the neighbourhood -
    far enough that a confident pair of words is not replaced by a guess, close
    enough that a confident half-word can be.
    """
    if not read[0]:
        return False
    if len(_letters(read[0])) < len(_letters(joined)):
        return False
    floor = min((c for _t, c in was), default=0.0)
    return read[1] >= floor * 0.85


def _letters(text: str) -> str:
    """The characters of a reading, without its spacing or its punctuation.

    Punctuation is left out because it is the part a reassembly is least able to
    vouch for, and a missing full stop is not the thing worth refusing a whole
    line over.
    """
    return "".join(c for c in str(text) if c.isalnum())


def strip_furigana_noise(text: str) -> str:
    """Убирает только явный фуригана-спам, не трогает kana внутри нормальных фраз."""
    t = text.strip()
    if not t:
        return t
    if is_furigana_reading(t):
        return ""
    # повторы りようほうほうりようほうほう
    t2 = re.sub(r"([\u3040-\u30ff]{3,10})\1+", r"\1", t)
    if t2 != t:
        t = t2
    return t.strip(" 、")


def _stacked_in_one_flow(item: dict, row: list[dict]) -> bool:
    """Whether a box continues a run of text above it rather than starting a new row.

    A paragraph arrives from the recogniser one line at a time, and a test on
    vertical centres will never gather it: consecutive lines are a whole line
    height apart and the tolerance is a fraction of one. Each line then became
    its own card, and the translation came back as separate sentences with a
    sentence's punctuation split across two boxes.

    Two boxes are one flow when most of the width of each is over the other, they
    start at about the same place, the lower one is a good fraction of a line
    height down from the upper one, the gap is smaller than a line is tall, and
    the upper one does not end a sentence - the last is what a reader uses, since
    a line finishing with a full stop has finished. The fraction of a line height
    matters as much as any of it: two items side by side are a hair apart
    vertically, so a test that only asks "is there a gap" says yes to every pair
    of neighbours on a row and a column of interface labels arrives as one card.

    Measured across the line rather than down the screen, so that a slanted
    paragraph groups the way a level one does.
    """
    ix1, iy1, ix2, iy2 = item["box"]
    found = False
    for other in row:
        ox1, oy1, ox2, oy2 = other["box"]
        overlap = min(ix2, ox2) - max(ix1, ox1)
        if overlap <= 0:
            continue
        narrower = min(ix2 - ix1, ox2 - ox1)
        if narrower <= 0 or overlap * 10 < narrower * 6:
            continue
        height = max(8.0, float(min(iy2 - iy1, oy2 - oy1)))
        drop = abs(((iy1 + iy2) * 0.5) - ((oy1 + oy2) * 0.5))
        if drop < height * 0.35:
            continue
        gap = iy1 - oy2 if iy1 >= oy2 else oy1 - iy2
        if gap < 0:
            gap = 0
        if gap >= height:
            continue
        # The upper line has to look unfinished, or the lower one is a new item.
        text = str(other.get("text", "")).rstrip()
        if text and text[-1] in ".!?。！？…:;»":
            continue
        found = True
        break
    return found


def pair_bilingual_and_dedupe(lines: list[dict]) -> list[dict]:
    """Гача+GACHA / メンバー+MEMBER → один блок. Чипы TAP!/NEW! отдельно."""
    if len(lines) < 2:
        return lines
    menu_en = {"GACHA", "MEMBER", "STORY", "LIVE", "MYSEKAI", "MY SEKAI"}
    menu_jp = {"ガチャ", "メンバー", "ストーリー", "マイセカイ", "ライブ"}
    chips = {"TAP!", "NEW!", "TIPS"}
    # Only these are the same label in two languages. Pairing "Live" with
    # "ストーリー" because both happen to be menu words merged two different
    # items and dropped the English one.
    known_pairs = {
        ("GACHA", "ガチャ"),
        ("MEMBER", "メンバー"),
        ("STORY", "ストーリー"),
        ("LIVE", "ライブ"),
        ("MYSEKAI", "マイセカイ"),
        ("MY SEKAI", "マイセカイ"),
    }

    items = []
    for p in lines:
        q = dict(p)
        q["text"] = normalize_japanese_text(str(q.get("text", "")))
        if q["text"].startswith("ストーリー"):
            q["text"] = "ストーリー"
        items.append(q)

    def en_norm(t: str) -> str:
        return t.upper().replace(" ", "")

    used: set[int] = set()
    out: list[dict] = []

    for i, a in enumerate(items):
        if i in used:
            continue
        if not is_latin_label(a["text"]) and a["text"] not in menu_jp:
            continue
        for j, b in enumerate(items):
            if j == i or j in used:
                continue
            # нужна пара JP ↔ EN
            if is_latin_label(a["text"]) == is_latin_label(b["text"]):
                continue
            jp, en = (a, b) if not is_latin_label(a["text"]) else (b, a)
            if en_norm(en["text"]) not in {en_norm(x) for x in menu_en}:
                continue
            if jp["text"] not in menu_jp:
                continue
            if (en_norm(en["text"]), jp["text"]) not in known_pairs:
                continue
            # EN под JP
            if en["box"][1] + 4 < jp["box"][3] and jp["box"][1] + 4 < en["box"][3]:
                # пересечение по Y — допустим чуть
                pass
            top, bot = (jp, en) if jp["box"][1] <= en["box"][1] else (en, jp)
            # для меню JP всегда сверху
            if is_latin_label(top["text"]):
                continue
            gap = bot["box"][1] - top["box"][3]
            if gap < -6 or gap > 30:
                continue
            ax1, _, ax2, _ = jp["box"]
            bx1, _, bx2, _ = en["box"]
            overlap = max(0, min(ax2, bx2) - max(ax1, bx1))
            narrow = max(1, min(ax2 - ax1, bx2 - bx1))
            if overlap / narrow < 0.45:
                continue
            out.append(
                {
                    "text": jp["text"],
                    "box": (
                        min(jp["box"][0], en["box"][0]),
                        min(jp["box"][1], en["box"][1]),
                        max(jp["box"][2], en["box"][2]),
                        max(jp["box"][3], en["box"][3]),
                    ),
                    "line_height": int(median([jp.get("line_height", 12), en.get("line_height", 12)])),
                    "conf": max(float(jp.get("conf", 0)), float(en.get("conf", 0))),
                    "engine": "menu-pair",
                    "kind": "menu",
                    "wrap": False,
                    "oneline": True,
                }
            )
            used.add(i)
            used.add(j)
            break

    for i, a in enumerate(items):
        if i in used:
            continue
        copy = dict(a)
        key = en_norm(copy["text"])
        if key in chips or copy["text"].upper() in chips:
            copy["kind"] = "chip"
            copy["oneline"] = True
            copy["wrap"] = False
        elif key in {en_norm(x) for x in menu_en}:
            # An English menu word with no Japanese twin used to be dropped
            # outright, so the text silently disappeared from a screen that
            # simply had no JP label. Paired lines are already merged above and
            # never reach this point, so keeping it cannot double anything.
            copy["kind"] = "menu"
            copy["oneline"] = True
            copy["wrap"] = False
        elif copy["text"] in menu_jp:
            copy["kind"] = "menu"
            copy["oneline"] = True
            copy["wrap"] = False
        out.append(copy)

    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    tlog(f"dedupe {len(lines)}->{len(out)}")
    return out


def _gap_separates_elements(
    region_img: Image.Image | None,
    above: tuple[int, int, int, int],
    below: tuple[int, int, int, int],
) -> bool | None:
    """Разделены ли две строки по вертикали разным фоном.

    Перенос абзаца и соседние кнопки в списке по одной геометрии неразличимы:
    у обоих левый край один и тот же, шаг по вертикали меньше высоты строки.
    Разница видна в зазоре между ними. У переноса он залит той же панелью,
    что и сами строки, — абзац это одна плашка. Между кнопками в зазоре фон
    экрана, и кнопки нарисованы каждый на своей подложке.

    На экране выбора языка так и было: «日本語», «簡体中文», «繁體中文» стояли
    тремя отдельными кнопками в левой колонке, а сгруппировались в один блок с
    одним переводом на всех, где ещё и перевод оказался втрое уже оригинала.

    None, когда решить нечем: зазора нет, он слишком узок для честной выборки
    или картинки нет. None — это «не отделять», чтобы проверка не ломала то,
    что работает, когда данных нет.
    """
    if region_img is None:
        return None
    ax1, _ay1, ax2, ay2 = above
    bx1, by1, _bx2, _by2 = below
    gap_top, gap_bottom = ay2, by1
    if gap_bottom - gap_top < 3:
        return None
    x1 = max(0, max(ax1, bx1) + 2)
    x2 = min(region_img.size[0], min(ax2, _bx2) - 2)
    if x2 - x1 < 8:
        return None
    try:
        import numpy as np

        arr = np.asarray(region_img.convert("L"), dtype=np.float32)

        def band(y0: int, y1: int) -> float | None:
            a = arr[max(0, y0):min(arr.shape[0], y1), x1:x2]
            if a.size < 8:
                return None
            return float(np.median(a))

        # Зазор берётся без краёв: у кнопки с тенью или рамкой самый верх и самый
        # низ зазора ещё принадлежат кнопке, а не фону между ними.
        inset = max(1, (gap_bottom - gap_top) // 4)
        gap_v = band(gap_top + inset, gap_bottom - inset)
        # Панель — срединные строки самих элементов.
        above_mid = band((_ay1 + ay2) // 2 - 2, (_ay1 + ay2) // 2 + 2)
        below_mid = band(by1 + 2, by1 + 6)
        if gap_v is None or above_mid is None or below_mid is None:
            return None
        panel = (above_mid + below_mid) * 0.5
        # Зазор должен заметно отличаться от панели. Разница в несколько
        # уровней — это скорее сглаженная тень, а не другой элемент.
        return abs(gap_v - panel) >= 18.0
    except Exception:  # noqa: BLE001
        return None


def group_japanese_blocks(
    lines: list[dict], region_img: Image.Image | None = None
) -> list[dict]:
    """Группирует японский экран в смысловые зоны, а не переводит каждую строку отдельно.

    Строки, разделённые зазором чужого фона, в зону не входят: это соседние
    элементы списка, а не перенос одной реплики. Картинка нужна именно для
    этого — по одним боксам перенос и список неразличимы.
    """
    if not lines:
        return lines
    if len(lines) < 2:
        for item in lines:
            item["wrap"] = True
        return lines
    lines = stitch_rows_by_baseline(lines)
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for item in lines:
        text = normalize_japanese_text(item.get("text", ""))
        item = dict(item)
        item["text"] = text
        if is_furigana_reading(text):
            tdetail(f"group-skip-furi {text[:40]!r}")
            continue
        is_heading = text.startswith(("ご注意", "未成年", "お願い", "TIPS"))
        if not cur:
            cur = [item]
            continue
        if is_heading or is_furigana_reading(text):
            groups.append(cur)
            cur = [item]
            continue
        prev = cur[-1]
        prev_text = prev["text"]
        if is_furigana_reading(prev_text):
            groups.append(cur)
            cur = [item]
            continue
        gap = item["box"][1] - prev["box"][3]
        lh = max(8, median(p["line_height"] for p in cur + [item]))
        # большой вертикальный отступ = новый раздел (ご注意 / 未成年 / お願い)
        section_gap = gap > max(26, lh * 1.85)
        if section_gap:
            tlog(f"section-gap {gap:.0f}px after {prev_text[:28]!r}")
            groups.append(cur)
            cur = [item]
            continue
        px1, _, px2, _ = prev["box"]
        ix1, _, ix2, _ = item["box"]
        overlap = max(0, min(px2, ix2) - max(px1, ix1))
        narrow = max(1, min(px2 - px1, ix2 - ix1))
        pcx = (px1 + px2) / 2
        icx = (ix1 + ix2) / 2
        centered = abs(pcx - icx) <= max(px2 - px1, ix2 - ix1) * 0.45
        spatially_same_block = overlap / narrow >= 0.35 or centered
        buttons = ("キャンセル", "ダウンロード", "決定", "閉じる", "はい", "いいえ", "OK", "Yes", "No", "TIPS")
        is_button = text in buttons or prev_text in buttons
        # EN-лейбл (TIPS/TAP!/GACHA) никогда не клеим к японскому телу в group —
        # пары JP+EN собирает pair_bilingual_and_dedupe.
        mixed_script = is_latin_label(prev_text) or is_latin_label(text)
        chip = (
            prev_text.upper().replace(" ", "") in {"TAP!", "NEW!", "TIPS"}
            or text.upper().replace(" ", "") in {"TAP!", "NEW!", "TIPS"}
            or prev.get("kind") == "chip"
            or item.get("kind") == "chip"
        )
        # Имя персонажа над репликой — отдельный блок.
        speaker_break = is_speaker_name(prev_text) or is_speaker_name(text)
        menu_break = (
            prev_text in UI_SHORT_LABELS
            or text in UI_SHORT_LABELS
            or prev.get("kind") == "menu"
            or item.get("kind") == "menu"
        )
        # Разные «острова» UI по X не клеим (локация слева + tip справа и т.п.)
        far_x = abs(pcx - icx) > max(px2 - px1, ix2 - ix1) * 0.9 and overlap / narrow < 0.2
        is_followup_q = text.endswith(("？", "?", "。")) and len(text) <= 16 and not is_speaker_name(text)
        # Короткие JP-реплики НЕ считаем ui: иначе «またその話？」+вторая строка не сливаются.
        ui_like = is_button
        max_gap = max(20, lh * 1.65)
        if is_followup_q and not mixed_script and not speaker_break:
            max_gap = max(26, lh * 2.2)
        # Две строки одной реплики/описания — можно чуть шире.
        if (
            not mixed_script
            and not speaker_break
            and not is_button
            and len(RE_JPN.findall(prev_text)) >= 3
            and len(RE_JPN.findall(text)) >= 3
        ):
            max_gap = max(max_gap, lh * 2.15)
        seam = _gap_separates_elements(region_img, prev["box"], item["box"])
        if seam:
            tdetail(
                f"group-split-seam gap={gap:.0f}px "
                f"'{prev_text[:20]}' -> '{text[:20]}'"
            )
        merge = (
            gap <= max_gap
            and not seam
            and spatially_same_block
            and not ui_like
            and not mixed_script
            and not speaker_break
            and not menu_break
            and not chip
            and not far_x
        )
        if merge:
            cur.append(item)
        else:
            groups.append(cur)
            cur = [item]
    if cur:
        groups.append(cur)

    out: list[dict] = []
    for group in groups:
        x1 = min(p["box"][0] for p in group)
        y1 = min(p["box"][1] for p in group)
        x2 = max(p["box"][2] for p in group)
        y2 = max(p["box"][3] for p in group)
        parts = [p["text"] for p in group if not is_furigana_reading(p["text"])]
        if not parts:
            continue
        if parts and parts[0] in ("ご注意", "お願い"):
            parts[0] = parts[0] + "。"
        if any(is_latin_label(p) for p in parts):
            text = normalize_japanese_text(" ".join(parts))
        else:
            # сохраняем строки раздела — cover по высоте всего блока
            text = "\n".join(normalize_japanese_text(p) for p in parts)
        text = "\n".join(
            line
            for line in (
                strip_furigana_noise(x) for x in text.split("\n") if x.strip()
            )
            if line.strip() and not is_furigana_reading(line)
        )
        if not text or len(RE_JPN.findall(text)) < 2:
            continue
        # мусорные обрывки вроде か宙がめん…ちも
        compact_lines = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if ("…" in line or "..." in line) and len(RE_JPN.findall(line)) <= 8:
                tlog(f"drop-garbage-jp {line!r}")
                continue
            compact_lines.append(line)
        text = "\n".join(compact_lines)
        if not text or len(RE_JPN.findall(text)) < 2:
            continue
        engines = {p.get("engine") for p in group if p.get("engine")}
        out.append(
            {
                "text": text,
                "box": (x1, y1, x2, y2),
                "line_height": int(median(p["line_height"] for p in group)),
                "conf": sum(float(p.get("conf", 0)) for p in group) / len(group),
                "wrap": True,
                "engine": ",".join(sorted({str(e) for e in engines})) if engines
                else "tesseract",
                "kind": (
                    "latin"
                    if is_latin_label(text)
                    else ("name" if is_speaker_name(text) else "body")
                ),
            }
        )
    tlog(f"group={len(out)} kinds="
        + " ".join(f"{g.get('kind','?')}:{len(g['text'])}" for g in out[:8]))
    tdetail("group " + " | ".join(f"{g.get('kind','?')}:{g['text'][:40]!r}" for g in out[:8]))
    return out

