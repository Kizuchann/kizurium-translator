"""Регулярки, скрипты и текстовые примитивы.

Нижний слой: ни от чего не зависит. Сюда едет всё, что является данными
о тексте, а не логикой над текстом: что считается японским, чем
выглядит зона интереса, какие строки - служебные. Класть это выше нельзя:
тогда `core.models` пришлось бы смотреть вверх, и порядок слоёв перестал
бы быть порядком.
"""

from __future__ import annotations

import logging
import math
import re

from PIL import Image, ImageOps

from .. import fonts as fonts_mod
from .. import logging_setup
from .. import trace as trace_mod
from ..paths import (  # noqa: F401
    Paths,
    default_paths,
)

OCR_ERROR = ""


TARGET_LANG = "ru"


OCR_ENGINE_ORDER: tuple[str,...] = ("rapid", "meiki", "tesseract")


RE_CYR = re.compile("[Ѐ-ӿёЁ]")


RE_LAT = re.compile("[A-Za-z]")


RE_CJK = re.compile("[぀-ヿ㐀-鿿豈-﫿가-힯]")


RE_KANA = re.compile("[ぁ-ゖァ-ヺー]")


RE_HAN = re.compile("[一-鿿々〆ヵヶ]")


RE_HANGUL = re.compile("[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")


PATHS: Paths = default_paths()


def _log() -> logging.Logger:
    """The overlay logger, pointed at the path that is current.

    Built on first use rather than at import: configure() replaces PATHS, and a
    logger bound at import would keep writing to wherever the module happened
    to find at startup, which is the previous run's directory under a test.
    """
    return logging_setup.get_logger("overlay", PATHS.log)


def tlog(msg: str) -> None:
    """One line of engine diagnostics."""
    _log().info("%s", msg)


def tdetail(msg: str) -> None:
    """A line that includes text read off the screen.

    Kept out of the default log on purpose: the file records what was displayed
    and is readable by anyone with access to the account. Measurements that need
    the text set KIZURIUM_TRANSLATOR_DEBUG.
    """
    if logging_setup.debug_enabled():
        _log().info("%s", msg)


def engine_enabled(name: str) -> bool:
    from . import active

    return name in active.ocr_engines()


UI_SHORT_LABELS = {
    "ガチャ", "メンバー", "ストーリー", "マイセカイ", "ライブ", "センター街",
    "キャンセル", "ダウンロード", "決定", "閉じる", "はい", "いいえ",
    "GACHA", "MEMBER", "STORY", "LIVE", "MYSEKAI", "MY SEKAI", "TIPS", "NEW!", "TAP!",
}


from ..lexicon.store import groups as _speaker_groups

SPEAKER_NAMES = _speaker_groups().speakers


def _relative_luminance(c: tuple) -> float:
    """WCAG relative luminance: how bright a colour is to the eye, weighted.

    Not the same as an average of the channels, because the eye is far more
    sensitive to green than to blue, and the channels are not linear in light.
    A green at 0.72 is much brighter than a blue at 0.72, and every judgement
    about whether two colours can be read against each other has to start from
    that or it will be wrong on exactly the pairs it matters for.
    """
    out = 0.0
    for i, w in ((0, 0.2126), (1, 0.7152), (2, 0.0722)):
        v = float(c[i]) if len(c) > i else 0.0
        v = min(1.0, max(0.0, v))
        out += w * (v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return out


def _contrast_ratio(a: tuple, b: tuple) -> float:
    """How far apart two colours read, from 1 (identical) to 21 (black/white).

    This is the measure a legibility check should use. A difference of average
    brightness treats two colours as unreadable whenever they are at the same
    level, which is false for anything with colour in it - a saturated green on
    a darker green of the same brightness is one of the most readable pairs a
    game screen puts on a display, and it is what a button label looks like.
    """
    la = _relative_luminance(a)
    lb = _relative_luminance(b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _real_english_words(text: str) -> int:
    """How many tokens look like genuine English words.

    Used to veto the mojibake heuristics: "Press E to interact with the door"
    has plenty of short words, which used to be enough to be classified as
    Cyrillic-read-as-Latin garbage and silently dropped from translation.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'\u2019-]*", text or "")
    if not words:
        return 0
    known = _english_word_set()
    good = 0
    for w in words:
        low = w.casefold()
        if low in known:
            good += 1
            continue
        # Two capitals that are not an acronym (WMee, BWAy) is a strong sign of
        # Cyrillic read as Latin, not of an English word.
        caps = sum(1 for c in w if c.isupper())
        if caps > 1 and not w.isupper():
            continue
        if re.search(r"[a-z][A-Z]", w):
            continue
        core = low.replace("'", "").replace("\u2019", "")
        if len(core) < 3:
            continue
        if not re.search(r"[aeiouy]", core):
            continue
        if re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", core):
            continue
        good += 1
    return good


_VOWELS_ANY = "aeiouAEIOUаеёиоуыэюяАЕЁИОУЫЭЮЯ"


def _digit_spam(text: str, digits: int, compact: str) -> bool:
    """Whether a line is a digit blob rather than a word plus a number.

    The old test was "digits make up a fifth of the line and there is at most
    one word", which deleted "Bonds 14820", "HP 120/120" and "Lv. 12 to Lv. 13" -
    most of what a game HUD actually shows. Spam is a run of digits with no
    words around it.
    """
    if digits < 3 or digits / max(1, len(compact)) < 0.18:
        return False
    if re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", text.strip()):
        return False  # a date on an event banner is real
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", text)
    if not tokens:
        return True  # pure digit blob: 1482014820, 1 2 3 4 5 6 7 8
    spelled_out = [w for w in tokens if len(w) >= 3 and any(c in _VOWELS_ANY for c in w)]
    abbrev = [w for w in tokens if len(w) <= 3 and w.isupper()]
    if len(spelled_out) >= 2 or (spelled_out and abbrev) or len(abbrev) >= 2:
        return False
    if spelled_out and digits < 8:
        return False  # "SCORE 999999" is a HUD line, not a blob
    if abbrev and digits < 8:
        return False
    if len(tokens) >= 2:
        return False
    # One short wordless token like "Mw" with a long digit run is still noise.
    return True


def _merge_language_halves(
    en_lines: list[dict],
    jp_lines: list[dict],
) -> list[dict]:
    """Union of both halves of a mixed frame, ordered top to bottom."""
    merged: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for item in list(en_lines) + list(jp_lines):
        box = tuple(int(v) for v in item.get("box", (0, 0, 0, 0)))
        key = (box[0] // 4, box[1] // 4)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    merged.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return merged


def clean_ocr_text(text: str) -> str:
    """Чинит типичные ошибки Tesseract в латинице (is→15, of→0f и т.п.).
    Переносы строк (\n) сохраняем — это оригинальная вёрстка субтитров/JP-блоков."""
    text = text.replace("Remore Butterfly", "Remora Butterfly")
    text = text.replace("Marivi Pullen", "Marivia Pollen")
    text = text.replace("Marivi Pollen", "Marivia Pollen")
    # чистим каждую строку отдельно, \n не трогаем
    out_lines = []
    for line in text.split("\n"):
        t = re.sub(r"[^\S\n]+", " ", line).strip()
        fixes = (
            (r"^15\s+", ""),
            (r"\b15\s+(?=[A-Za-zА-Яа-я])", ""),
            (r"\bsite\s+15\b", "site is"),
            (r"\bthis\s+15\b", "this is"),
            (r"\bthat\s+15\b", "that is"),
            (r"\bit\s+15\b", "it is"),
            (r"\bthere\s+15\b", "there is"),
            (r"\b(\w+)\s+15\s+to\b", r"\1 is to"),
            (r"\b1s\b", "is"),
            (r"\bl5\b", "is"),
            (r"\b0f\b", "of"),
            (r"\bt0\b", "to"),
            (r"\bw1th\b", "with"),
            (r"\bEngl1sh\b", "English"),
            (r"\bengl1sh\b", "english"),
            (r"\bf0r\b", "for"),
            (r"\ba11\b", "all"),
        )
        for pat, rep in fixes:
            t = re.sub(pat, rep, t, flags=re.IGNORECASE)
        out_lines.append(t.strip())
    return "\n".join(out_lines).strip()


def _ink_column_profile(crop: Image.Image) -> list[bool]:
    """True для столбцов, где в вертикальном срезе есть пиксель, отличный от фона.

    Фон оценивается медианой среза, а не «самым светлым» или «самым тёмным»:
    надпись в интерфейсе может быть светлее подложки или темнее неё, и профиль
    должен видеть обе. Второй фон (подложка панели, градиент неба) не считать
    чернилами, поэтому мера отклонения, а не порог по абсолютной яркости.
    """
    w, h = crop.size
    if w < 2 or h < 3:
        return []
    try:
        gray = crop.convert("L")
    except Exception:
        return []
    px = gray.load()
    values = sorted(px[x, y] for y in range(h) for x in range(w))
    # Разброс среза решает, что считать чернилами. Медиана фона берётся
    # отдельно для каждого столбца: у надписи, стоящей на градиенте, общий
    # фон не годится - в одном конце столбца фон тёмный, в другом светлый.
    spread = values[int(len(values) * 0.95)] - values[int(len(values) * 0.05)]
    thresh = max(18, int(spread * 0.45))
    prof: list[bool] = []
    for x in range(w):
        col = [px[x, y] for y in range(h)]
        col_bg = sorted(col)[len(col) // 2]
        prof.append(max(abs(v - col_bg) for v in col) >= thresh)
    return prof


def _empty_run_right(
    region_img: Image.Image,
    py1: int,
    px2: int,
    py2: int,
    limit: int = 400,
    px1: int | None = None,
) -> int:
    """How many clear pixels there are to the right of a box, in a band with it.

    Whether a card may be wider than the label it covers is not a question about
    the translation, it is a question about what stands next to it. A label in
    the middle of an empty panel has all the room it wants; a stat screen puts
    the value in the same row, a fixed distance to the right, and a card grown
    past the label lands on the number.

    The band is the row's own height, extended a little below, because a number
    in the same line may sit on a different baseline than the label's text.

    What stands there is told apart from what lies behind by its edges. A
    number, an icon, another label are drawn sharp; artwork under a frosted
    panel is blurred, and it changes brightness across the band as much as
    the letters do - a legend over a character's jacket read as full, and every
    longer translation in it was set smaller instead of reaching into the empty
    panel. Given the label's own left edge, an edge counts when it is a fair
    share as sharp as the label's own letters, so dim grey text beside a dim
    grey label still counts as standing there.
    """
    try:
        import numpy as np

        w, h = region_img.size
        y1 = max(0, min(py1, h - 1))
        y2 = max(y1 + 1, min(py2, h))
        x_start = max(0, min(px2, w - 1))
        x_end = min(w, x_start + limit)
        if x_end - x_start < 8 or y2 - y1 < 4:
            return 0

        def sharpness(box) -> np.ndarray:
            arr = np.asarray(region_img.crop(box).convert("L"), dtype=np.int16)
            col = np.zeros(arr.shape[1], dtype=np.int16)
            col = np.maximum(col, np.abs(np.diff(arr, axis=0)).max(axis=0))
            gx = np.abs(np.diff(arr, axis=1)).max(axis=0)
            col[:-1] = np.maximum(col[:-1], gx)
            col[1:] = np.maximum(col[1:], gx)
            return col

        thresh = 40.0
        if px1 is not None and px2 - px1 >= 6:
            own = sharpness((max(0, px1), y1, x_start, y2))
            if own.size:
                thresh = max(20.0, float(np.percentile(own, 90)) * 0.4)
        prof = list(sharpness((x_start, y1, x_end, y2)) >= thresh)
        if not prof:
            return 0
        run = 0
        for has_ink in prof:
            if has_ink:
                break
            run += 1
        return run
    except Exception:
        # A measurement that fails has to look like no room, not like plenty:
        # the consequence of being wrong is a card that overlaps its neighbour.
        return 0


def _empty_column_runs(prof: list[bool]) -> list[tuple[int, int]]:
    """Промежутки столбцов без чернил, от края до края."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, has in enumerate(prof):
        if not has and start is None:
            start = i
        elif has and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(prof) - 1))
    return runs


def text_by_x_range(
    words: list[str], line_x1: int, line_x2: int, left: int, right: int
) -> str:
    """Отбирает слова, чья доля по ширине строки попадает в [left, right).

    Доли считаются от всей строки, а не от ширины куска: иначе каждый кусок
    растягивал бы слова на свою ширину и получал бы их все.
    """
    if right <= left or line_x2 <= line_x1:
        return ""
    weights = [max(1, len(w)) for w in words]
    total = sum(weights)
    span = line_x2 - line_x1
    picked: list[str] = []
    cursor = line_x1
    for w, weight in zip(words, weights):
        # Доля от ширины всей строки, округление без искусственного минимума:
        # округление каждого слова вверх на пиксель съезжает к концу строки и
        # последнее слово выпадает за её правый край.
        ww = max(1, int(round(weight * span / total)))
        mid = cursor + ww // 2
        if left <= mid < right:
            picked.append(w)
        cursor += ww
    return " ".join(picked)


CORE_INK_PERCENTILE = 85.0


def _core_read_beats(old: list[tuple[str, float]], new: list[tuple[str, float]]) -> bool:
    """Whether a second reading is one to keep, judged by the engine's own scores.

    Compared as totals rather than one box against one box, because the second
    pass may find a different number of lines than the first did, and a pair of
    good lines is a better answer than one good line and one guess. The margin
    keeps a tie with the reading already in hand, which is the one that has the
    screen's own spacing and punctuation in it.
    """
    if not new:
        return False
    return sum(c for _t, c in new) > sum(c for _t, c in old) + 5.0 * max(
        len(new), len(old), 1
    )


def _attached_to_kanji(line: dict, lines: list[dict]) -> bool:
    """Whether a small kana line reads as ruby for a neighbouring kanji line."""
    x1, y1, x2, y2 = line["box"]
    for other in lines:
        if other is line:
            continue
        text = str(other.get("text", ""))
        if not RE_HAN.search(text):
            continue
        ox1, oy1, ox2, oy2 = other["box"]
        if not (oy2 - 4 <= y1 or oy1 >= y2 + 4):  # must be directly above/below
            continue
        overlap = min(x2, ox2) - max(x1, ox1)
        if overlap > 0.4 * max(1, x2 - x1):
            return True
    return False


def _continues_flow(candidate: dict, above: dict) -> bool:
    # Только ровный текст. Строка на уклоне - это строка, а не абзац, и
    # склеивать их по вертикали нельзя: в одном ритм-игровом экране соседние
    # подписи под уклоном (GREAT и MISS, PERFECT и PASS) перекрываются по
    # ширине, стоят в полустроке друг от друга и не заканчиваются точкой, то
    # есть проходят по всем остальным проверкам. Наклонную строку склеивает
    # своя геометрия, и трогать её здесь нечего.
    if abs(float(candidate.get("angle", 0.0) or 0.0)) >= 1.0:
        return False
    if abs(float(above.get("angle", 0.0) or 0.0)) >= 1.0:
        return False
    cx1, cy1, cx2, cy2 = candidate["box"]
    ax1, ay1, ax2, ay2 = above["box"]
    overlap = min(cx2, ax2) - max(cx1, ax1)
    if overlap <= 0:
        return False
    narrower = min(cx2 - cx1, ax2 - ax1)
    if narrower <= 0 or overlap * 10 < narrower * 6:
        return False
    height = max(8.0, float(min(cy2 - cy1, ay2 - ay1)))
    drop = abs(((cy1 + cy2) * 0.5) - ((ay1 + ay2) * 0.5))
    if drop < height * 0.35:
        return False
    # Строки абзаца стоят ближе, чем высока строка: интерлиньяж кегля меньше
    # кегля. Список - наоборот, элементы списка разнесены больше высоты строки,
    # и это ровно то, чем список отличается от абзаца. Без этой проверки в один
    # блок собирались "Mob Bestiary", "Player Stats" и "Extra Damage:" - три
    # подписи интерфейса, идущие столбиком с шагом в две строки.
    if drop >= height:
        return False
    gap = cy1 - ay2 if cy1 >= ay2 else ay1 - cy2
    if gap < 0:
        gap = 0
    if gap >= height:
        return False
    text = str(above.get("text", "")).rstrip()
    return not (text and text[-1] in ".!?\u3002\uff01\uff1f\u2026:;\u00bb")


def subtitle_preprocess(img: Image.Image, threshold: int = 145) -> Image.Image:
    gray = ImageOps.grayscale(img)
    # Белые субтитры -> чёрный текст на белом фоне; фон/персонажи почти исчезают.
    return gray.point(lambda p: 0 if p >= threshold else 255, mode="1").convert("L")


def box_overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area = max(1, min((ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1)))
    return inter / area


def _family_for(block: dict) -> str:
    """The faces a card is drawn in, chosen by what kind of line it covers.

    Not by what the line says. A game has a vocabulary no glossary covers, and
    the face a screen is set in is a property of the screen: a row of statistics
    is condensed and tracked whatever the words in it are, a heading is the
    widest thing in its region, a plain label is neither. So the role is read
    off the shape and the faces come from the role, and the role is why a line
    set in a condensed face is drawn in one - the only thing a translation can
    measure about the original and cannot change is how it looks.

    The list ends in a face with Cyrillic because the overlay translates into
    Russian and most of the game faces have no Russian letters in them at all.
    """
    return fonts_mod.family_list(_role_for(block))


def _role_for(block: dict) -> str:
    text = str(block.get("source") or block.get("text") or "")
    w = float(block.get("src_w", 0) or 0)
    h = float(block.get("src_h", 0) or 0)
    if h <= 0 or w <= 0:
        return "label"
    kind = str(block.get("kind") or "")
    stroke = float(block.get("stem") or 0.0)
    italic = bool(block.get("italic"))
    lines = float(block.get("src_lines", 1) or 1)
    digits = sum(1 for c in text if c.isdigit())
    letters = sum(1 for c in text if c.isalpha())
    words = len(text.split())
    aspect = w / h
    # The ink's own shape, measured off the original. The card's
    # rectangle is a decision - it grows to hold the translation - so judging the
    # face by the card's proportions is judging it by our own answer. These two
    # are the letters themselves: how wide they are for their height, and how
    # tall they stand against the box that was drawn around them. Both are what
    # a condensed or a wide face looks like, and neither depends on the words.
    ink_aspect = float(block.get("ink_aspect") or 0.0)
    if ink_aspect <= 0:
        ink_h = float(block.get("ink_h") or 0.0)
        ink_w = float(block.get("ink_w") or 0.0)
        ink_aspect = (ink_w / ink_h) if ink_h > 0 else 0.0
    condensed = ink_aspect > 0 and ink_aspect < aspect * 0.82
    # Dialogue in a novel is almost always a serif or a book sans. A HUD
    # label is condensed. A name plate is heavy display. The role is the
    # shape, not the words: the same "Buro" on a character sheet and in a
    # tooltip are different objects.
    if kind in ("dialogue", "dialogue-line") or (italic and kind not in ("ui", "chip")):
        return "serif"
    if kind == "name" or (
        h >= 42 and words <= 3 and letters >= 2 and digits == 0 and lines <= 1
    ):
        if stroke >= 0.16 or h >= 56:
            return "display"
        return "rounded"
    if digits >= max(2, letters) and aspect >= 3.0 and w <= h * 9:
        return "stat"
    # A label whose letters are much narrower than the card it sits in was set
    # in a condensed face, and a condensed face has a condensed Cyrillic
    # counterpart in the pool on purpose. Judged by the card alone this reads as
    # a wide flat line and lands on the score list instead.
    if condensed and kind in ("ui", "chip", "latin", "menu") and words <= 4:
        return "stat"
    if kind in ("ui", "chip", "latin") and h <= 32 and aspect >= 3.4 and words <= 4:
        return "score"
    if h >= 28 and aspect >= 3.8 and words <= 5 and lines <= 1:
        return "score" if stroke >= 0.12 else "display"
    if lines > 1 or aspect >= 6.0:
        return "body"
    return "label"


def _bucket_median(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Median channel value of a pixel bucket, channel by channel.

    Named out of the body of sample_ocr_cover_colors because the colour of a
    glyph is a property of the pixel, and the only way to read it is to look at
    one. An average across a bucket is a property of the bucket.
    """
    if not pixels:
        return (0, 0, 0)
    n = len(pixels)
    mid = n // 2

    def med(channel: int) -> int:
        vals = sorted(p[channel] for p in pixels)
        return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) // 2

    return (med(0), med(1), med(2))


def _rgb(r: float, g: float, b: float) -> str:
    """Colour as the log reads it, so a trace line can be compared to a pixel."""
    return f"({int(round(r * 255))},{int(round(g * 255))},{int(round(b * 255))})"


def _band_pixels(
    region_img: Image.Image,
    band: tuple[float, float, float, float, float],
    ring: float = 3.0,
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    """Pixels inside a rotated rectangle, and the ring just outside it."""
    import numpy as np

    cx, cy, bw, bh, angle = (float(v) for v in band)
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    half_w, half_h = bw / 2.0, bh / 2.0
    out_w, out_h = half_w + ring, half_h + ring
    ext_x = abs(out_w * cos_a) + abs(out_h * sin_a)
    ext_y = abs(out_w * sin_a) + abs(out_h * cos_a)
    w, h = region_img.size
    x1, y1 = max(0, int(cx - ext_x)), max(0, int(cy - ext_y))
    x2, y2 = min(w, int(math.ceil(cx + ext_x))), min(h, int(math.ceil(cy + ext_y)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return [], []
    rgb = np.asarray(region_img.crop((x1, y1, x2, y2)).convert("RGB"))
    ys, xs = np.mgrid[y1:y2, x1:x2]
    dx = xs + 0.5 - cx
    dy = ys + 0.5 - cy
    along = np.abs(dx * cos_a + dy * sin_a)
    across = np.abs(-dx * sin_a + dy * cos_a)
    inside = (along <= half_w) & (across <= half_h)
    outer = (along <= out_w) & (across <= out_h) & ~inside
    return (
        [tuple(int(c) for c in p) for p in rgb[inside]],
        [tuple(int(c) for c in p) for p in rgb[outer]],
    )


def _edge_pixels(crop: Image.Image, ring: int = 2) -> list[tuple[int, int, int]]:
    """The outermost rows and columns of a crop."""
    import numpy as np

    rgb = np.asarray(crop.convert("RGB"))
    h, w = rgb.shape[:2]
    if h <= ring * 2 or w <= ring * 2:
        return [tuple(int(c) for c in p) for p in rgb.reshape(-1, 3)]
    parts = (
        rgb[:ring].reshape(-1, 3),
        rgb[-ring:].reshape(-1, 3),
        rgb[ring:-ring, :ring].reshape(-1, 3),
        rgb[ring:-ring, -ring:].reshape(-1, 3),
    )
    return [tuple(int(c) for c in p) for part in parts for p in part]


def sample_bubble_cover_colors(
    region_img: Image.Image | None, box: tuple[int, int, int, int]
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]] | None:
    """Фон/текст под цвет пузыря (розовый SoftMoney → тёмный текст на светлом)."""
    return sample_ocr_cover_colors(region_img, box, prefer_light_bg=True)


def sample_ocr_cover_colors(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    prefer_light_bg: bool = False,
    band: tuple[float, float, float, float, float] | None = None,
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]] | None:
    """Сэмпл цвета глифов и фона из OCR-бокса (magenta Preferences / серый сайдбар).

    ``band`` is ``(cx, cy, w, h, angle)``: the rotated strip the line actually
    occupies. A slanted line's axis-aligned box also holds whatever sits above
    and below the line, and that is often a different panel in a different
    colour. Counting it makes the panel below the line the second-largest
    colour in the box, and it is then taken for the ink.
    """
    if region_img is None:
        return None
    try:
        x1, y1, x2, y2 = box
        w, h = region_img.size
        pad = 2
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, max(x1 + 2, x2 + pad)), min(h, max(y1 + 2, y2 + pad))
        crop = region_img.crop((x1, y1, x2, y2))
        edge: list[tuple[int, int, int]] = []
        if band is not None:
            px, edge = _band_pixels(region_img, band)
            if len(px) < 24:
                px = []
        if band is None or not px:
            px = list(crop.get_flattened_data())
            edge = _edge_pixels(crop)
        if not px:
            return None

        def lum(c):
            return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

        # Two colours, and which of them is the panel is a question about how
        # much of the box each one fills. Text is drawn on top of a background,
        # so the background is the one that covers most of the area - and at
        # full resolution that is measurable exactly, which it is not after the
        # crop has been shrunk: a white letter on pink downsampled to one pixel
        # is a pink pixel, and a card built from that is pink letters on a pink
        # panel with the original showing through both.
        buckets: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
        for c in px:
            buckets.setdefault((c[0] >> 3, c[1] >> 3, c[2] >> 3), []).append(c)
        ranked = sorted(buckets.values(), key=len, reverse=True)
        bg_bucket = ranked[0]
        # Bold type in a tight box covers more of it than the panel does, and
        # a slanted line's strip is mostly letters. Area then names the ink
        # as the panel, and the card is the ink colour with the ink colour on
        # it. The panel is what surrounds the line: the outermost pixels of
        # the box are the panel wherever the letters do not touch the edge.
        edge_counts: dict[tuple[int, int, int], int] = {}
        for c in edge:
            k = (c[0] >> 3, c[1] >> 3, c[2] >> 3)
            edge_counts[k] = edge_counts.get(k, 0) + 1
        if edge_counts:
            edge_key, edge_n = max(edge_counts.items(), key=lambda kv: kv[1])
            if edge_n >= len(edge) * 0.30 and edge_key in buckets:
                edge_bucket = buckets[edge_key]
                if edge_bucket is not bg_bucket and len(edge_bucket) >= len(px) * 0.12:
                    ranked = [edge_bucket] + [b for b in ranked if b is not edge_bucket]
                    bg_bucket = edge_bucket
            top = ranked[0]
            top_key = (top[0][0] >> 3, top[0][1] >> 3, top[0][2] >> 3)
            if edge_counts.get(top_key, 0) < len(edge) * 0.08:
                # Behind the line is artwork, so no one colour holds the border,
                # and the biggest colour by area is a colour the border hardly
                # touches: the letters. The panel is then the border itself,
                # less whatever of the letters reached it.
                tm = _bucket_median(top)
                rim = [
                    c
                    for c in edge
                    if (c[0] - tm[0]) ** 2 + (c[1] - tm[1]) ** 2 + (c[2] - tm[2]) ** 2 > 60 * 60
                ]
                if len(rim) >= len(edge) * 0.5:
                    ranked = [rim] + ranked
                    bg_bucket = rim
        # Медиана, а не среднее. Среднее по бакету уводит цвет туда, где его нет:
        # бакет «тёмный зелёный» собирает и ядро глифа, и каждую сглаженную
        # кайму, и в сумме даёт оттенок, которого на экране не нарисовали.
        # Медиана берёт настоящий цвет ядра.
        bg_med = _bucket_median(bg_bucket)
        br, bg_g, bb = (v / 255.0 for v in bg_med)
        # фон: светлый (VN bubble) — по требованию вызывающего
        if prefer_light_bg:
            if 0.299 * br + 0.587 * bg_g + 0.114 * bb < 0.45:
                return None
            # Раньше и здесь стоял ×1.02 на каналы. Ровно то же, что снято ниже:
            # плашка светлее оригинала — и отличается от него глазом.
            return (br, bg_g, bb, 0.99), (0.18, 0.14, 0.22, 1.0)

        bg_lum = 0.299 * br + 0.587 * bg_g + 0.114 * bb
        # Which colour is the ink, where there is more than one candidate. The
        # one furthest from the panel, not the next biggest: outlined type has
        # two inks and the outline is the bigger of them, and a yellow word with
        # a dark border on a pink panel came out with the border's colour as its
        # text - a purple word where the game wrote a yellow one. Size says which
        # colour covers the line; distance says which one the line is *read* by,
        # and the second is the question.
        #
        # The bar for being a different colour at all is low on purpose: this
        # asks "are there two colours here", not "is the design good". A button
        # label in bright green on dark green measures under two to one, the game
        # ships it, and it reads - so a threshold set at a legibility standard
        # would throw the pair away and paint the card from the defaults instead.
        ink_px = None
        best = 0.0
        need = max(12, int(len(px) * 0.04))
        for bucket in ranked[1:6]:
            if len(bucket) < need:
                # A colour this thin is the edge of another one, not a colour of
                # its own, and picking it puts a fringe colour on the whole word.
                continue
            mr, mg, mb = (v / 255.0 for v in _bucket_median(bucket))
            ratio = _contrast_ratio((mr, mg, mb, 1.0), (br, bg_g, bb, 1.0))
            trace_mod.consider(
                "ink-candidate",
                f"rgb=({mr:.3f},{mg:.3f},{mb:.3f}) px={len(bucket)}",
                contrast=round(ratio, 3),
                bar=1.25,
            )
            if ratio >= 1.25 and ratio > best:
                ink_px = bucket
                best = ratio
        if ink_px is None or best < 3.0:
            # Thin antialiased type is spread over many shades, and no single
            # bucket of it reaches the bar. Together they are still the ink.
            # Over artwork the same happens to bold type: the panel's shades
            # fill the biggest buckets, one of them passes for the ink at a
            # contrast of two, and white letters were drawn dark grey.
            far = [
                c
                for c in px
                if (c[0] - bg_med[0]) ** 2 + (c[1] - bg_med[1]) ** 2 + (c[2] - bg_med[2]) ** 2
                > 90 * 90
            ]
            if len(far) >= max(12, int(len(px) * 0.02)):
                # The outer half of them is edge blended into the panel.
                far.sort(
                    key=lambda c: (c[0] - bg_med[0]) ** 2
                    + (c[1] - bg_med[1]) ** 2
                    + (c[2] - bg_med[2]) ** 2,
                    reverse=True,
                )
                far = far[: max(12, len(far) // 2)]
                mr, mg, mb = (v / 255.0 for v in _bucket_median(far))
                ratio = _contrast_ratio((mr, mg, mb, 1.0), (br, bg_g, bb, 1.0))
                if ratio >= 1.25 and (ink_px is None or ratio >= best * 1.5):
                    ink_px = far
                    best = ratio
        if ink_px is None:
            trace_mod.decide(
                "ink", "none", bg=_rgb(br, bg_g, bb), px=len(px)
            )
            return None
        fr, fg_g, fb = (v / 255.0 for v in _bucket_median(ink_px))

        # Дальше — ровно то, что на экране. Ни масштабирования, ни подъёма
        # яркости: обе правки стояли здесь с времён большого рефакторинга и
        # означали, что плашка никогда не совпадает с оригиналом по цвету.
        # Тёмное-фиолетовое поле с малиновыми буквами уезжало в фиолет,
        # и подложка была темнее оригинала на 8% — это и читалось как
        # «текст подставлен». Точность важнее разборчивости; разборчивость
        # обеспечивает альфа плашки, а не искажение цвета.
        bg = (br, bg_g, bb, 0.97)
        fg = (fr, fg_g, fb, 1.0)
        trace_mod.decide(
            "ink",
            "exact",
            bg=_rgb(br, bg_g, bb),
            fg=_rgb(fr, fg_g, fb),
            bg_lum=round(bg_lum, 3),
            contrast=round(best, 3),
        )
        return bg, fg
    except Exception:
        return None


def weight_for_stroke(stroke: float, box_h: int) -> object:
    """The weight to letter a translation in, from the weight the original is.

    A translation set at a fixed weight does not match the screen it covers
    whichever face it picks, and that mismatch is the more obvious half of "the
    font does not match": game UI leans on very heavy display faces, and a
    book-weight rendering of the same word is a visibly different object from the
    one lying under it. The stroke is the one property of a typeface that can be
    read off the original, so it decides rather than a preference.

    The bands are measured, not guessed: on the screens this was checked against
    a heavy display face runs at 0.15 to 0.26 of its own ink height, and ordinary
    interface text below that. A card with nothing to measure - an empty picture,
    or a line so short that its median is noise - is left at the default rather
    than guessed at.
    """
    import gi

    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    if box_h < 10 or stroke <= 0.0:
        return Pango.Weight.BOLD
    # Stem thickness as a share of ink height. Ordinary UI sits well
    # below the old 0.19 floor, which mapped every thin label onto Bold
    # and every heavy title that measured 0.18 onto the same Bold — the
    # two cases the overlay was getting backwards.
    if stroke >= 0.24:
        return Pango.Weight.ULTRAHEAVY
    if stroke >= 0.20:
        return Pango.Weight.HEAVY
    if stroke >= 0.175:
        return Pango.Weight.BOLD
    if stroke >= 0.15:
        return Pango.Weight.SEMIBOLD
    if stroke >= 0.12:
        return Pango.Weight.MEDIUM
    if stroke >= 0.09:
        return Pango.Weight.NORMAL
    if stroke >= 0.06:
        return Pango.Weight.LIGHT
    return Pango.Weight.THIN


def quad_depth(pts) -> int:
    """How thick the line an OCR quadrilateral found is, across the line.

    The recogniser draws a box round the shape it detected, and for a line of
    text that shape is long and thin whatever the angle. The long sides are the
    length of the line and the short ones are the type, so the shortest side is
    the size of the letters - which is the one number about a slanted line that
    cannot be read off the axis-aligned box at all, because that box is as tall
    as the climb.

    Asked of the quadrilateral rather than of pixels, so it costs nothing and
    cannot be thrown off by whatever else is in the crop: a result screen's
    background is crossed by the game's own diagonal bands, and measured ink in
    a box like that includes them, which is how a card came out five times the
    height of its own text with the text sitting at the top of it and the rest
    empty.

    Zero when the quadrilateral is not elongated, because then there is no line
    to measure and the caller has something else to try.
    """
    try:
        pts = [(float(p[0]), float(p[1])) for p in pts]
    except Exception:
        return 0
    if len(pts) < 4:
        return 0
    sides = []
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        sides.append(math.hypot(x2 - x1, y2 - y1))
    sides.sort()
    long_side = sides[-1]
    if long_side < 12 or sides[0] > long_side * 0.6:
        # Square-ish: a block of UI, not a line of type. Whatever the
        # recogniser meant by it, its thickness is not the size of any letters.
        return 0
    return int(round(sum(sides[:2]) / 2.0))


def quad_angle_deg(pts) -> float:
    """Угол верхней грани OCR-quad в градусах (наклон UI вниз/вверх).

    The corner detector rounds to whole pixels, so a level line comes back with
    a small slope that is not on the screen: a short name label measured 119
    pixels wide and 6 down, and 2.9 of those degrees is the rounding. Drawn from
    that angle the name leaned gently to the right, and the same happened to six
    level rows on a repository page and to every flat label on a stat screen.

    A real slope is not ambiguous at that margin. The results screen measured
    0.33 for "Wonderful Pain" and 0.43 for "SCORE", against 0.05 for the noise -
    a factor of five. Below the threshold the line is level, which is what it
    is: a card drawn level over level text is invisible, a card drawn at three
    degrees over level text is not.
    """
    try:
        pts = [(float(p[0]), float(p[1])) for p in pts]
    except Exception:
        return 0.0
    if len(pts) < 2:
        return 0.0
    # берём самое длинное почти-горизонтальное ребро
    best_ang, best_len = 0.0, -1.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 4:
            continue
        ang = math.degrees(math.atan2(dy, dx))
        # нормализуем к [-90, 90]
        if ang > 90:
            ang -= 180
        elif ang < -90:
            ang += 180
        if abs(ang) > 40:
            continue
        if length > best_len:
            best_len = length
            best_ang = ang
            # tan(5.7 deg) - the most a level line's rounding produces
            if abs(dy) < 0.10 * abs(dx):
                best_ang = 0.0
    if best_len < 0:
        x1, y1 = pts[0]
        x2, y2 = pts[1]
        dx, dy = x2 - x1, y2 - y1
        if math.hypot(dx, dy) < 1e-6 or abs(dy) < 0.10 * abs(dx):
            return 0.0
        best_ang = math.degrees(math.atan2(dy, dx))
    if abs(best_ang) < 5.7:
        return 0.0
    return clamp(best_ang, -28.0, 28.0)


def script_cjk_score(lines: list[dict]) -> int:
    return sum(len(RE_CJK.findall(str(p.get("text", "")))) for p in lines)


def game_ui_score(lines: list[dict]) -> tuple[int, int, int]:
    """Возвращает (ui_hits, short_chips, spoken_count) для логов/детекта."""
    texts = [str(p.get("text", "")).strip() for p in lines]
    ui_re = re.compile(
        r"\b(HP|SP|ATK|DEF|STR|VIT|DEX|END|AGI|MND|INT|Exp|Pouch|Lv\.?|Col|Select|Back|Use|"
        r"Stamina|Money|Growth|Proficiency|Cardinal|Mirror|Until Next|Main Menu|"
        r"Start|Quit|Exit|About|Cancel|Confirm|Preferences|Settings|Language|"
        r"Achievements|Load|Save|Continue|New Game|More Games|Version|"
        r"Start Game|What'?s your name)\b",
        re.I,
    )
    ui_hits = sum(1 for t in texts if ui_re.search(t))
    short = sum(1 for t in texts if 1 <= len(t) <= 14)
    spoken = sum(1 for t in texts if looks_like_spoken_line(t))
    return ui_hits, short, spoken


def _reads_as_prose(text: str) -> bool:
    """Whether a line reads as a sentence rather than as a spoken line.

    A bubble is short and starts with a name. Prose is longer, and a capital in
    the middle of it means a proper noun, not a new sentence - which matters
    here because the recogniser misreads "Safe Areas" as "Safe Are as" and the
    stray "Are" then counted as a capital and the line stopped looking like what
    it is. Only a capital in the first two words is a sentence start.
    """
    words = str(text or "").split()
    if len(words) < 7:
        return False
    opened = sum(1 for w in words[:2] if w[:1].isupper())
    later = sum(1 for w in words[2:] if w[:1].isupper())
    # Seven words with no sentence start anywhere is prose. A capital further in
    # is allowed, because a proper noun is capitalised and a misread word may be
    # capitalised too, and neither of those is evidence of speech.
    return opened == 0 and later <= max(1, len(words) // 4)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / float(area_a + area_b - inter)


_FORBIDDEN_PAIRS = frozenset(
    [*(f"h{tail}" for tail in "bcdfgjklmnpqtvxz"), *(f"j{tail}" for tail in "bcdfghklmnpqrstvwxz")]
    + ["rh", "qh", "fh", "kx", "zz"]
)


def _impossible_pairs(text: str) -> int:
    low = re.sub(r"[^a-z]", "", text.lower())
    return sum(1 for i in range(len(low) - 1) if low[i : i + 2] in _FORBIDDEN_PAIRS)


def _reading_quality(text: str) -> tuple[int, int, int]:
    """Score a candidate reading; a higher tuple is a more plausible reading.

    Only structural signals are used. A doubled-letter penalty was tried and
    removed: it punished "Skills" for its "ll" and preferred a misread.
    """
    words = re.findall(r"[A-Za-z]{2,}", text)
    if not words:
        return (0, 0, 0)
    vowel_words = sum(1 for w in words if any(c in "aeiouy" for c in w.lower()))
    return (-_impossible_pairs(text), vowel_words, sum(len(w) for w in words))


_SHORT_WORDS = {
    "bin", "src", "nix", "git", "vim", "lua", "env", "dev", "app", "web",
    "cpu", "gpu", "ram", "ssd", "usb", "tcp", "udp", "ssh", "ftp", "api",
    "faq", "new", "old", "yes", "no", "all", "any", "one", "two", "ten",
    "ok", "on", "off", "up", "go", "hi", "it", "id", "os", "ip", "db", "ui",
    "abc", "amd", "arm", "x86", "zip", "pdf", "gif", "png", "svg", "toml",
    "yml", "ini", "cfg", "exe", "dll", "sdk", "ide", "rgb", "hex", "utc",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun", "min", "max", "avg",
}


def _is_real_short_word(text: str, line: dict) -> bool:
    """Whether a very short read is a word rather than a fragment.

    Two ways to earn it. A known short word is one. So is a confident read: a
    misread fragment of a scan comes back unsure, while a real three-letter word
    in a real font comes back at ninety-nine percent, and there is no reason to
    throw away a word the engine was certain about. The repeated-character test
    and the single-character test still reject, because `lll` and `a` are not
    words however confidently they were read.
    """
    t = str(text or "").strip()
    if not t or len(t) <= 1:
        return False
    low = t.casefold()
    if low in _SHORT_WORDS:
        return True
    if len(set(low)) == 1:
        return False
    if not re.search(r"[A-Za-zА-Яа-я]", t):
        return False
    return float(line.get("conf", 0.0) or 0.0) >= 90.0


def same_paragraph_read(a: dict, b: dict) -> bool:
    """Whether two reads describe the same run of lines.

    The ink-band pass reads a run of lines under a block and the line pass
    reads the same run from the top. When they agree on the first line - same
    words, same top edge - they agree on where the paragraph is, and whichever
    read reached further down has everything the other one has. Comparing the
    whole boxes cannot tell them apart, because a box for three lines contains
    a box for two of them and half the time it contains a single line as well;
    comparing the first line can, because a line is the smallest thing two
    reads can both be about.

    The first lines are compared on what they share rather than on being equal.
    Two engines read the same line and disagreed at the end of it - "...of
    light. These" against "...of light. 1" - and demanding equality missed
    exactly the pair this exists for.
    """
    la = list(a.get("line_boxes") or [])
    lb = list(b.get("line_boxes") or [])
    if not la or not lb:
        return False
    if abs(int(la[0]["box"][1]) - int(lb[0]["box"][1])) > 4:
        return False
    ka = _line_compare_key(la[0])
    kb = _line_compare_key(lb[0])
    return _shares_its_start(ka, kb)


def _line_compare_key(line: dict) -> str:
    return re.sub(r"[\s.,!?'\"—–-]+", "", str(line.get("text", ""))).casefold()


def _shares_its_start(ka: str, kb: str) -> bool:
    """Whether two reads of one line agree over most of it.

    The agreement is measured from the front because that is where the words
    are: the two engines differ at the tail of a line far more often than at
    its head, and on a codex entry they differed inside a word - "unexplored
    are as" against "unexplored areas" - which only a shared front survives.
    """
    if not ka or not kb:
        return False
    shared = 0
    for ca, cb in zip(ka, kb):
        if ca != cb:
            break
        shared += 1
    return shared >= max(12, int(min(len(ka), len(kb)) * 0.45))


def covered_by_paragraph(block: dict, para: dict) -> bool:
    """Whether a single line is already inside a paragraph read elsewhere.

    Once the longest read of a paragraph is kept, the line pass still offers the
    same paragraph one line at a time, and those lines sit on top of it. A line
    belongs to the paragraph when it starts where one of the paragraph's own
    lines starts and reads like it; a line that starts in the same band but says
    something else is its own text and stays.
    """
    para_lines = list(para.get("line_boxes") or [])
    if not para_lines:
        return False
    y1 = int(block["box"][1])
    best = min(para_lines, key=lambda lb: abs(int(lb["box"][1]) - y1))
    by1, by2 = int(best["box"][1]), int(best["box"][3])
    if not (by1 - 8 <= y1 <= by2 + 8):
        return False
    return _shares_its_start(_line_compare_key(block), _line_compare_key(best))


def dedupe_paragraph_reads(lines: list[dict]) -> list[dict]:
    """Keep one read per paragraph: the one that reached furthest.

    Found on a game codex screen: a four-line entry produced two cards for it,
    laid over each other, because the ink-band pass and the line pass both read
    the paragraph from the same first line and stopped at different lines.
    """
    if len(lines) < 2:
        return list(lines)
    ordered = sorted(
        lines,
        key=lambda p: (
            -len(p.get("line_boxes") or []),
            -len(str(p.get("text", ""))),
        ),
    )
    kept: list[dict] = []
    for p in ordered:
        if any(same_paragraph_read(p, q) for q in kept):
            continue
        if any(covered_by_paragraph(p, q) for q in kept):
            continue
        kept.append(p)
    kept.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return kept


def _boxes_touch(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """Whether two lines are close enough to be one run of text."""
    lh = max(12, a[3] - a[1])
    gap = b[1] - a[3]
    if gap > lh * 0.6:
        return False
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    return overlap > 0.55 * min(a[2] - a[0], b[2] - b[0])


def merge_paragraph_tail(lines: list[dict]) -> list[dict]:
    """Fold a paragraph's last line back into it when the read stopped short.

    Found on a game codex screen: a four-line entry was read as three lines plus
    a separate one-word line, "first.". Two cards then fought over the same
    ground - the paragraph's card was squeezed to half its height to avoid the
    one-word card, and two lines of English stayed uncovered underneath it.

    What makes them one run is that they touch, that they are aligned, and that
    the short line is too short to be a label of its own. A button under a
    paragraph has a gap and a border; a wrapped sentence does not.
    """
    if len(lines) < 2:
        return list(lines)
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    out: list[dict] = []
    used: set[int] = set()
    emitted: set[int] = set()
    for i, par in enumerate(ordered):
        if i in used:
            continue
        emitted.add(i)
        para_lines = list(par.get("line_boxes") or [])
        if len(para_lines) < 2:
            out.append(par)
            continue
        cur = dict(par)
        changed = True
        while changed:
            changed = False
            last = cur["line_boxes"][-1]["box"]
            for j in range(i + 1, len(ordered)):
                if j in used:
                    continue
                nxt = ordered[j]
                text = str(nxt.get("text", "")).strip()
                if not text or len(text.split()) > 3:
                    continue
                if not _boxes_touch(last, nxt["box"]):
                    continue
                used.add(j)
                cur["line_boxes"] = list(cur["line_boxes"]) + [
                    {
                        "text": text,
                        "box": tuple(nxt["box"]),
                        "line_height": int(nxt.get("line_height", 0) or 0),
                    }
                ]
                cur["text"] = f"{cur['text']} {text}".strip()
                bx1, by1, bx2, by2 = cur["box"]
                nx1, ny1, nx2, ny2 = nxt["box"]
                cur["box"] = (min(bx1, nx1), by1, max(bx2, nx2), max(by2, ny2))
                cur["src_h"] = int(cur["box"][3] - cur["box"][1])
                changed = True
                break
        out.append(cur)
    out.extend(
        ordered[j]
        for j in range(len(ordered))
        if j not in used and j not in emitted
    )
    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return out


UI_LINES_MAX = 120


def cap_ui_lines(lines: list[dict], limit: int = UI_LINES_MAX) -> list[dict]:
    """Cap the number of cards, dropping the least worth translating first.

    The cap used to keep the first sixty after a sort by descending text length,
    so the shortest lines were always the ones dropped: on a GitHub page that
    is `README.md`, `flake.nix`, `.github`, `install`, `bin`, `nix`, `src`,
    `Projects`, `Actions`, `Releases`, `Packages` - nineteen elements, every one
    of them a real heading or a real file name, and every one of them shorter
    than a commit subject. A page has more text than a card budget, and the
    budget has to be spent on the text a reader would want translated.

    What survives the cap is therefore chosen by usefulness: a line with words is
    kept over a fragment, a longer one over a shorter, and position breaks the
    remaining ties so the result is stable between frames. Nothing is dropped
    below the limit, and the original order is restored afterwards, so a screen
    with fewer lines than the cap is untouched.
    """
    if len(lines) <= limit:
        return list(lines)

    def rank(p: dict) -> tuple:
        text = str(p.get("text", "")).strip()
        words = len(text.split())
        x1, y1, x2, y2 = p.get("box", (0, 0, 0, 0))
        # A heading and a file name are one word, and so is most of what a
        # reader is looking for on a page of links. Length is therefore not the
        # question - the old sort by descending length dropped exactly those,
        # because a commit subject is eight words and `README.md` is one. What
        # separates a heading from a fragment of a merged line is the confidence
        # of the read, and a heading that sits alone in its own box is not a
        # fragment. Among equals the longer line wins, then the earlier one, so
        # the result is the same between frames.
        conf = float(p.get("conf", 0.0) or 0.0)
        return (
            0 if conf >= 80.0 else 1,
            -words,
            -len(text),
            y1,
            x1,
        )

    order = sorted(range(len(lines)), key=lambda i: (rank(lines[i]), i))
    keep = sorted(order[:limit])
    return [lines[i] for i in keep]


def normalize_subtitle_ocr(text: str) -> str:
    """Чинит типичный OCR субтитров: lori→Iori, склейки без пробелов.
    Переносы \\n сохраняем — иначе 2 строки субтитров схлопываются в одну."""
    def _one(line: str) -> str:
        t = re.sub(r"[^\S\n]+", " ", line).strip()
        t = re.sub(r":(?=\S)", ": ", t)
        t = re.sub(r"\b(lori|tori|lorí)\s*:", "Iori:", t, flags=re.I)
        t = re.sub(r"\bIoris\b", "Iori's", t)
        t = re.sub(r"\bloris\b", "Iori's", t, flags=re.I)
        t = re.sub(
            r"\b([a-z]{2,12})\s*:",
            lambda m: m.group(1)[:1].upper() + m.group(1)[1:] + ":",
            t,
        )
        t = re.sub(r"\b(Lori|Tori)\s*:", "Iori:", t)
        t = re.sub(r"([.!?\"'])([A-Za-z])", r"\1 \2", t)
        t = re.sub(r",([A-Za-z])", r", \1", t)
        t = re.sub(r"([A-Za-z])'\s+([A-Za-z])", r"\1'\2", t)
        # 10, oo / 10, o 00 / 10.000 → 10,000 (OCR нулей). НЕ трогаем Version 1.0
        t = re.sub(
            r"\b(\d{1,3})[,.\s]+(?:o{2,3}|0{3})(?:\s*o{1,3}|\s*0{1,3})*\b",
            r"\1,000",
            t,
            flags=re.I,
        )
        t = re.sub(r"\b(\d{1,3}),\s*o{2,}\s*0+\b", r"\1,000", t, flags=re.I)
        t = re.sub(r"\bVersion\s+(\d+)[,.](\d+)\b", r"Version \1.\2", t, flags=re.I)
        # HUD-статусы (частые OCR-кривляния / Blinded-scanlines)
        t = re.sub(r"\bwal+ting\s*to\s*swit[coh]+\b", "Waiting to Switch", t, flags=re.I)
        t = re.sub(r"\bwaiting\s*to\s*swit[coh]+\b", "Waiting to Switch", t, flags=re.I)
        # WialtintoSwitoh / Viaiting / Vialting to Switch / gto Switch
        if re.search(r"swit", t, re.I) and re.search(
            r"(wait|ait|ltin|ting\s*to|^\s*[vg]?t+o\b)", t, re.I
        ):
            t = "Waiting to Switch"
        t = re.sub(r"\bexpl+aring\b", "Exploring", t, flags=re.I)
        t = re.sub(r"\bin\s*com+bat\b", "In Combat", t, flags=re.I)
        t = re.sub(r"\bhead\s*to\s*horunk[ao]?\b", "Head to Horunka", t, flags=re.I)
        # OCR часто склеивает no+word: noviolent → no violent
        t = re.sub(r"\bno(?=(violent|distress|content|human|emotional)\b)", "no ", t, flags=re.I)
        t = re.sub(r"\bhas\s+no(?=[a-z]{4,})", "has no ", t, flags=re.I)
        # obtained35o x Co / Obtained 350 x Col.
        t = re.sub(
            r"\bobtained\s*(\d{2,4})[o0l]?\s*x\s*co[li.]?\b",
            r"Obtained \1 x Col.",
            t,
            flags=re.I,
        )
        t = re.sub(
            r"\bobtained\s*(\d+)\s+[o0]\s+x\s+co[li.]?\b",
            r"Obtained \1 x Col.",
            t,
            flags=re.I,
        )
        t = strip_watermark_tail(t)
        return re.sub(r" {2,}", " ", t).strip(" ,.-")

    parts = str(text or "").split("\n")
    return "\n".join(_one(p) for p in parts)


COMMIT_PREFIXES = {
    "feat", "feature", "fix", "fixes", "fixed", "bugfix", "hotfix", "docs", "doc",
    "style", "refactor", "refact", "perf", "chore", "build", "ci", "test", "tests",
    "revert", "merge", "bump", "deps", "dep", "release", "docs:", "add", "update",
    "remove", "cleanup", "improve", "implement", "initial", "wip", "tmp",
}


def looks_like_speaker_prefix(word: str) -> bool:
    """Whether a word before a colon names a character rather than an action.

    Only the shape is judged here - capitalisation, length, letters - because a
    game speaker and a commit prefix have the same shape. The word list below is
    what separates them.
    """
    raw = str(word or "").strip()
    w = raw.casefold()
    if not w or w in COMMIT_PREFIXES:
        return False
    # A name is short: one short word, or two small ones;
    # "Overworld Exploration" is two long ones, and a heading with a colon in it
    # is not a character speaking - which is what sent a codex title through the
    # subtitle path and left the paragraph under it untranslated.
    if len(raw.split()) >= 2 and any(len(piece) > 9 for piece in raw.split()):
        return False
    if len(raw) > 24:
        return False
    # A single lowercase letter is an OCR artefact, not a name.
    if len(w) < 2 or w.isdigit():
        return False
    if w in {"http", "https", "www", "note", "warning", "error", "info", "todo"}:
        return False
    return True


def looks_like_spoken_line(text: str) -> bool:
    """A line of the form `Name:...` in gameplay footage or a cutscene.

    The name has to look like a name. A commit subject has the identical shape
    and used to pass: on a GitHub file listing every row is `feat:` or `docs:`
    followed by text, so a column of them was read as one character speaking and
    merged into a single card over half the screen.
    """
    t = normalize_subtitle_ocr(text)
    if len(t) < 8:
        return False
    m = re.match(r"^([A-Za-z][A-Za-z0-9 .'\-]{0,28}):\s+\S.{3,}", t)
    if not m:
        return False
    # The whole name, not its last word. "Game Master" is a name and
    # "Overworld Exploration" is a heading; both end in a word that looks like a
    # name on its own, and the difference is in the phrase - which is why the
    # length rule above has to see the phrase. Checking only the last word is
    # what let a codex title through as a character speaking, and the subtitle
    # path then read one line of it and left the paragraph untranslated.
    return looks_like_speaker_prefix(m.group(1))


_EN_WORD_SET: set[str] | None = None


def _english_word_set() -> set[str]:
    """Системный словарь — общий word-break, без хардкода отдельных фраз."""
    global _EN_WORD_SET
    if _EN_WORD_SET is not None:
        return _EN_WORD_SET
    words: set[str] = set()
    for path in (
        "/usr/share/dict/words",
        "/usr/share/dict/american-english",
        "/usr/share/dict/british-english",
        "/usr/share/dict/cracklib-small",
    ):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    w = line.strip().lower()
                    if 2 <= len(w) <= 16 and w.isalpha():
                        words.add(w)
        except OSError:
            continue
    # fallback без /usr/share/dict — частые EN + игровой UI (иначе Selectthe не рвётся)
    words.update(
        w
        for w in ["i", "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "is", "are", "be", "we", "you", "he", "she", "it", "they", "my", "your", "what", "who", "how", "do", "did", "does", "can", "will", "just", "stay", "town", "safe", "from", "out", "there", "long", "as", "admire", "anyone", "brave", "enough", "after", "conduct", "matters", "truly", "one", "ones", "their", "appearance", "regardless", "go", "strong", "possible", "best", "interest", "future", "unclear", "stronger", "better", "were", "was", "trapped", "nightmare", "that", "demanded", "price", "could", "never", "hope", "afford", "patients", "climbing", "life", "others", "quietly", "slipping", "toward", "final", "moments", "emotional", "themes", "financial", "stress", "light", "human", "violent", "distressing", "content", "explore", "start", "cancel", "about", "language", "preferences", "settings", "games", "game", "achievements", "continue", "load", "save", "quit", "exit", "death", "last", "thing", "willing", "accept", "medical", "bill", "file", "edit", "view", "help", "no", "yes", "ok", "back", "map", "all", "buy", "use", "next", "skip", "confirm", "select", "partner", "accompany", "like", "would", "incomplete", "quest", "already", "finished", "begin", "have", "has", "had", "been", "being", "with", "without", "into", "onto", "upon", "over", "under", "again", "still", "only", "also", "more", "most", "many", "much", "some", "any", "every", "each", "other", "another", "such", "than", "then", "when", "where", "which", "while", "because", "before", "between", "both", "during", "few", "first", "great", "high", "however", "if", "into", "its", "itself", "least", "less", "may", "might", "must", "now", "often", "once", "only", "own", "same", "should", "since", "so", "some", "than", "that", "their", "them", "then", "these", "this", "those", "through", "too", "under", "until", "very", "well", "were", "what", "when", "where", "which", "while", "who", "whom", "whose", "why", "will", "with", "within", "without", "would", "yet", "about", "above", "across", "after", "again", "against", "along", "among", "around", "because", "before", "behind", "below", "beneath", "beside", "between", "beyond", "both", "but", "by", "down", "during", "except", "for", "from", "in", "inside", "into", "near", "of", "off", "on", "onto", "out", "outside", "over", "past", "since", "through", "throughout", "to", "toward", "under", "until", "up", "upon", "with", "within", "without", "deal", "deals", "damage", "medium", "surrounding", "enemies", "enemy", "assistance", "character", "higher", "create", "creates", "zone", "which", "gradually", "restored", "restore", "set", "period", "time", "support", "skill", "skills", "combination", "twin", "embrace", "healing", "circle", "sword", "slanted", "edge", "rage", "spike", "vertical", "arc", "shortsword", "short", "sword", "waiting", "switch", "guard", "action", "shift", "standard", "heavy", "attacks", "light", "main", "menu", "change", "settings", "options", "quest", "objective", "search", "rumored", "monster", "obtained", "received", "exploring", "blinded", "head", "proceed", "combat", "confirm", "cancel", "back", "next", "skip", "start", "begin", "select", "partner", "accompany", "incomplete", "finished", "already", "have", "has", "quest", "game", "load", "save", "continue", "options", "preferences", "language", "display", "window", "return", "quit", "exit", "about", "achievements", "history", "hp", "sp", "atk", "def", "str", "vit", "dex", "end", "agi", "mnd", "int", "level", "lv", "player", "party", "item", "items", "inventory", "equipment", "weapon", "armor", "attack", "defend", "magic", "mana", "stamina", "health", "gradually", "restored", "period", "surrounding", "assistance", "character", "damage", "enemies", "incomplete", "quest", "already", "finished", "accompany", "partner", "select", "would", "like"]
        if len(w) >= 1 and len(w) <= 16 and w.isalpha()
    )
    # однобуквенные артикли/местоимения — нужны для fora→for a
    words.update({"a", "i"})
    _EN_WORD_SET = words
    tlog(f"en-dict words={len(words)}")
    return words


def _word_break_token(token: str) -> str:
    """DP word-break для склеенного OCR-токена (Iadmire, strongaspossible…)."""
    if len(token) < 4 or not token.isalpha():
        return token
    words = _english_word_set()
    low = token.lower()
    if low in words:
        return token
    n = len(low)
    prev = [-1] * (n + 1)
    prev[0] = 0
    for i in range(n):
        if prev[i] < 0:
            continue
        for j in range(i + 1, min(n, i + 16) + 1):
            if low[i:j] in words:
                prev[j] = i
    parts: list[str] = []
    if prev[n] >= 0:
        i = n
        while i > 0:
            j = prev[i]
            parts.append(token[j:i])
            i = j
        parts.reverse()
    else:
        # greedy: Selectthe / incompletequestoroneyouhave
        i = 0
        while i < n:
            best = -1
            for j in range(min(n, i + 16), i + 1, -1):
                frag = low[i:j]
                # "a"/"i" только если это остаток (иначе Ioris→I or is)
                min_len = 1 if (j == n and frag in {"a", "i"}) else 2
                if frag in words and len(frag) >= min_len:
                    best = j
                    break
            if best < 0:
                return token
            parts.append(token[i:best])
            i = best
        if len(parts) < 2:
            return token
    # Ioris→I or is — рвём только явный микро-мусор, "for a" оставляем
    if any(len(p) == 1 and p.lower() not in {"a", "i"} for p in parts):
        return token
    if len(parts) >= 3 and sum(1 for p in parts if len(p) <= 2) >= 3:
        return token
    if len(parts) >= 3 and max(len(p) for p in parts) <= 3:
        return token
    if len(words) < 20000 and len(low) <= 7:
        # Without the system dictionary the list is a few hundred common words,
        # and any short word it lacks splits into short words it has: "Areas"
        # into "Are as", "Area" into "Are a". A glued pair of real words is
        # longer than that, or made of longer pieces.
        if any(len(p) <= 2 for p in parts) and not (
            len(low) >= 6 and all(len(p) >= 3 or p.lower() in {"a", "i"} for p in parts)
        ):
            return token
    return " ".join(parts)


def source_rows(par: dict) -> list[dict]:
    """The rows a paragraph was read as, top to bottom.

    A stitched paragraph carries one box for several lines of text. Which lines
    they were is not decoration: the card is laid out against the same number of
    rows the original had, so a two-line description stays two lines instead of
    being condensed into one. `line_boxes` is the dialogue path, where each row
    gets its own card; `src_rows` is the panel path, where the paragraph keeps
    one card but still remembers its rows. Sorted by y, because the recogniser
    hands lines back in reading order only when it feels like it.
    """
    rows = [r for r in (par.get("line_boxes") or par.get("src_rows") or []) if r.get("box")]
    return sorted(rows, key=lambda r: int(r["box"][1]))


def row_pitch(rows: list[dict]) -> int:
    """The distance the original put between its lines, in pixels.

    The game's own leading, not a font's: two description lines in a sheet sat
    55 pixels apart, and the leading the translation gets has to be that same
    55 or the block sits in the top of the box it was given. Median, so one odd
    row - a box that caught a descender, a line half a pixel lower - does not
    become the leading of the paragraph.
    """
    if len(rows) < 2:
        return 0
    gaps = sorted(
        int(b["box"][1]) - int(a["box"][1])
        for a, b in zip(rows, rows[1:])
        if int(b["box"][1]) - int(a["box"][1]) > 2
    )
    if not gaps:
        return 0
    mid = len(gaps) // 2
    if len(gaps) % 2:
        return int(gaps[mid])
    # An even number of gaps has no middle row of its own, and picking one of
    # the two ends makes the paragraph's leading the largest gap in it - the
    # gap under a heading, or the empty row between two paragraphs. The middle
    # of the two is the leading the rows actually agree on.
    return int(round((gaps[mid - 1] + gaps[mid]) / 2.0))


def pack_translation_to_rows(rows: list[dict], translated: str) -> list[str]:
    """Split a translation across the rows of its source.

    Proportional to the length of each source row, not filled greedily to the
    first row's width: a Russian sentence is shorter than the English line it
    came from, so filling row one to the brim leaves the rest of the paragraph
    bare in English. Every row gets at least one word, so a row is never left
    holding nothing while the next holds three.
    """
    n = len(rows)
    flat = re.sub(r"\s+", " ", (translated or "").strip())
    if n <= 1:
        return [flat]
    words = flat.split()
    if not words:
        return [""] * n
    if len(words) < n:
        return words + [""] * (n - len(words))
    weights = [max(1, len(re.sub(r"\s+", "", str(r.get("text", ""))))) for r in rows]
    total = float(sum(weights))
    out: list[str] = []
    idx = 0
    for i, weight in enumerate(weights):
        if i == n - 1:
            out.append(" ".join(words[idx:]))
            break
        left_rows = n - i
        room = len(words) - idx - (left_rows - 1)
        take = max(1, min(room, round(len(words) * (weight / total))))
        out.append(" ".join(words[idx: idx + take]))
        idx += take
    return out


_FILE_EXTENSIONS = (
    "md", "markdown", "txt", "text", "yml", "yaml", "toml", "json", "jsonc",
    "lock", "nix", "conf", "cfg", "ini", "env", "log", "csv", "tsv", "xml",
    "html", "css", "js", "ts", "tsx", "jsx", "py", "rs", "go", "c", "h", "cc",
    "cpp", "hpp", "sh", "bash", "zsh", "fish", "rb", "java", "kt", "swift",
    "sql", "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "pdf", "zip",
    "tar", "gz", "xz", "7z", "exe", "dll", "so", "dylib", "deb", "rpm", "apk",
    "pyc", "o", "a", "bin", "dat", "db", "sqlite", "patch", "diff", "desktop",
    "service", "socket", "timer", "mount", "rules", "list", "sources", "target",
    "iso", "img", "qcow2", "efi", "efi.img", "spec", "sum", "mod", "work",
)


def is_subtitle_junk_line(
    text: str,
    box: tuple[int, int, int, int] | None = None,
    rw: int = 0,
    rh: int = 0,
) -> bool:
    """Ватермарки / пустые плиты / мусор OCR — не строки диалога.

    Пример бага: RSWIRRAS на весь низ (1920×87) → вторая плашка «в безопасности» в пустоте.
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return True
    if re.search(r"(shirra|shtra|chirra|oshtra|rswirr|srras|swirr|shirr)", t, re.I):
        return True
    letters = re.sub(r"[^A-Za-z]", "", t)
    if 3 <= len(letters) <= 18:
        vowels = len(re.findall(r"[aeiouyAEIOUY]", letters))
        no_space = " " not in t
        # капс-каша ватермарки
        if no_space and letters.isupper() and vowels <= max(1, len(letters) // 5):
            return True
        if no_space and len(letters) >= 5 and vowels / max(1, len(letters)) < 0.18:
            return True
    if box is not None and rw > 0 and rh > 0:
        x1, y1, x2, y2 = box
        bw, bh = max(0, x2 - x1), max(0, y2 - y1)
        # почти fullscreen-ширина у низа + мало текста = плита/ватермарка
        if bw >= int(rw * 0.72) and y2 >= int(rh * 0.94) and len(letters) < 28:
            if not looks_like_spoken_line(t):
                return True
        if bw >= int(rw * 0.85) and len(letters) <= 14 and not looks_like_spoken_line(t):
            return True
        # огромная высота «строки» (>50) при коротком тексте
        if bh >= 55 and len(letters) < 20 and not looks_like_spoken_line(t):
            return True
    return False


def strip_watermark_tail(text: str) -> str:
    """SHIRRAKO / SHTRA.S и хвосты OCR в конце реплики."""
    t = text
    t = re.sub(
        r"[\s,.\-—]*\b(S?H?IRR?A+[A-Z0-9.]*|SHTRA\.?\s*S?|OSHTRA\.?S?|SHTRA)\s*$",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\b(SMIRR?A+S?|SHIRR?A+[SKO]*|OSHTRA\.?S?|SHTRA\.?\s*S?)\b", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip(" ,.-")


def dialogue_looks_incomplete(text: str) -> bool:
    # уже есть 2+ строки — смотрим только хвост последней
    if "\n" in text:
        text = text.rsplit("\n", 1)[-1]
    t = re.sub(r"\s+", " ", text.strip())
    if len(t) < 12:
        return True
    if re.search(r"[.!?…」』»\"]\s*$", t):
        return False
    # JP: обрыв на частице / середине
    if RE_CJK.search(t):
        if re.search(r"[はがをにでとものねよ…]$", t):
            return True
        return len(t) < 18 and not re.search(r"[。！？]$", t)
    if re.search(
        r"\b(the|a|an|to|of|and|or|inside|will|be|for|with|your|my|our|from|into|"
        r"people'?s?|does|is|are|was|were|has|have|can|could|would|should|but|it|"
        r"price|one|that|demanded)\s*$",
        t,
        re.I,
    ):
        return True
    if re.search(
        r",\s*(but|and|or|so|yet)\b.*\b(does|is|are|was|it)\s*$",
        t,
        re.I,
    ):
        return True
    if re.search(r"\bbut\s+it\s+does\s*$", t, re.I):
        return True
    # обрезано посередине слова (нет пробела у хвоста) — не любое предложение без точки
    if re.search(r"[A-Za-z]{8,}$", t) and not re.search(r"[.!?:,;]\s*$", t) and len(t) >= 48:
        return True
    # длинная VN-строка без точки сама по себе НЕ обрубок:
    # Rapid часто теряет финальную точку ("accept"). Режем только явный хвост.
    if len(t) >= 36 and not re.search(r"[.!?…]\s*$", t):
        if re.search(r"\b(a|an|the|to|of|and|or|but|that|one|price|does|is|are|was|were|has|have|can|could|would|should)\s*$", t, re.I):
            return True
    if len(t) >= 48 and t.rstrip().endswith((",", "does", "and", "but", "—", "-")):
        return True
    return False


def is_hud_spam(text: str) -> bool:
    t = re.sub(r"\s+", " ", text.strip())
    up = t.upper()
    if up in {"WARNING", "BARNING", "DARNING", "WARNI", "ARNING", "WARNING!", "SYSTEM"}:
        return True
    if re.fullmatch(r"W?A?RNINGS?", up):
        return True
    if "WARNING" in up and len(up) <= 18:
        return True
    if re.search(r"PROGRAM\s*CONTROL|SYSTEM\s*ANNOUNCE|CONTROL:\s*0", up):
        return True
    return False


def soft_descan_panel(img: Image.Image) -> Image.Image:
    """Лёгкое сглаживание горизонтальных scanline без median (он убивает буквы)."""
    try:
        from PIL import ImageEnhance
    except ImportError:
        return img
    # усреднить по вертикали: сжать высоту → растянуть обратно
    h2 = max(1, img.height // 2)
    small = img.resize((img.width, h2), Image.Resampling.BILINEAR)
    out = small.resize(img.size, Image.Resampling.BILINEAR)
    g = ImageOps.autocontrast(ImageOps.grayscale(out), cutoff=1)
    g = ImageEnhance.Contrast(g).enhance(1.35)
    return g.convert("RGB")


def ocr_coverage_stats(lines: list[dict], w: int, h: int) -> dict:
    """Насколько OCR закрыл область — для логов и добора."""
    if not lines or h <= 0:
        return {
            "n": 0,
            "top": h,
            "span": 0,
            "big_gaps": 0,
            "sparse": True,
            "coverage": 0.0,
        }
    ordered = sorted(lines, key=lambda p: p["box"][1])
    top = int(ordered[0]["box"][1])
    bot = int(ordered[-1]["box"][3])
    span = max(0, bot - top)
    ink = 0
    big_gaps = 0
    for a, b in zip(ordered, ordered[1:]):
        gap = int(b["box"][1] - a["box"][3])
        if gap > 36:
            big_gaps += 1
        ink += max(8, int(a["box"][3] - a["box"][1]))
    ink += max(8, int(ordered[-1]["box"][3] - ordered[-1]["box"][1]))
    coverage = ink / float(h)
    # выделенный абзац сайта: мало строк + дыры/пустой верх = sparse
    sparse = bool(
        (len(lines) <= 6 and h >= 180 and (big_gaps >= 1 or top >= 36))
        or (len(lines) <= 3 and h >= 220)
        or coverage < 0.12
    )
    return {
        "n": len(lines),
        "top": top,
        "span": span,
        "big_gaps": big_gaps,
        "sparse": sparse,
        "coverage": coverage,
    }


def log_ocr_coverage(tag: str, lines: list[dict], w: int, h: int) -> None:
    st = ocr_coverage_stats(lines, w, h)
    tlog(
        f"{tag}-coverage n={st['n']} top={st['top']} span={st['span']} "
        f"gaps={st['big_gaps']} cov={st['coverage']:.2f} sparse={int(st['sparse'])} "
        f"geom={w}x{h}"
    )


def log_eng_ui_lines(tag: str, lines: list[dict]) -> None:
    """Полный дамп UI-строк + группы одинакового текста на разном Y."""
    tlog(f"{tag} n={len(lines)}")
    by_key: dict[str, list[int]] = {}
    for p in lines:
        t = str(p.get("text", "")).strip()
        y1 = int(p["box"][1])
        y2 = int(p["box"][3])
        eng = p.get("engine", "?")
        tdetail(f"{tag}-line y={y1}-{y2} eng={eng} '{t[:56]}'")
        key = re.sub(r"\s+", " ", t).casefold()
        by_key.setdefault(key, []).append(y1)
    for key, ys in by_key.items():
        if len(ys) >= 2:
            tlog(f"{tag}-dup n={len(ys)} ys={ys} '{key[:48]}'")


def _crop_box(
    region_img,
    box: tuple[int, int, int, int],
    pad: int = 3,
):
    x1, y1, x2, y2 = box
    w, h = region_img.size
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(w, max(x1 + 4, x2 + pad)), min(h, max(y1 + 4, y2 + pad))
    return region_img.crop((x1, y1, x2, y2))


JP_REFINE_CONF = 78.0


JP_REFINE_MIN_CHARS = 1


SCRIPT_EN = "en"


SCRIPT_JA = "ja"


SCRIPT_MIXED = "mixed"


SCRIPT_UNKNOWN = "unknown"


SCRIPT_TARGET = "target"


def block_script(text: str) -> str:
    """Per-block script: en / ja / mixed / target / unknown."""
    s = str(text or "").strip()
    if not s:
        return SCRIPT_UNKNOWN
    kana = len(RE_KANA.findall(s))
    han = len(RE_HAN.findall(s))
    latin = len(RE_LAT.findall(s))
    cyr = len(RE_CYR.findall(s))
    if kana >= 1:
        return SCRIPT_MIXED if latin >= 3 else SCRIPT_JA
    if han >= 1 and latin >= 3:
        return SCRIPT_MIXED
    if han >= 2:
        return SCRIPT_JA
    if cyr >= 2 and latin == 0:
        return SCRIPT_TARGET
    if latin >= 2:
        return SCRIPT_EN
    if cyr >= 1:
        return SCRIPT_TARGET
    return SCRIPT_UNKNOWN


def normalize_for_compare(text: str) -> str:
    """Whitespace/punctuation-insensitive form used by the tracker."""
    s = str(text or "").strip().casefold()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[\s\u3000]+", "", s)
    return s


def block_lang(text: str) -> str:
    """The source language of one block, decided from the block itself.

    Never from the frame. A frame is not a language: a game menu is EN, a
    dialogue is JP, and a help page is EN with a JP note in the corner, all at
    once. Deciding once per screen is what made the English path throw the
    Japanese away - the decision was made before the Japanese was looked at.

    Han alone is deliberately "auto" and not "ja". Kanji-only labels are real
    Japanese UI, and Chinese-only labels are real Chinese UI, and the two are
    indistinguishable from the characters; the backend reads both, so guessing
    would be worse than saying so.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    kana = len(RE_KANA.findall(s))
    han = len(RE_HAN.findall(s))
    hangul = len(RE_HANGUL.findall(s))
    latin = len(RE_LAT.findall(s))
    cyr = len(RE_CYR.findall(s))
    if kana >= 1:
        return "ja"
    if hangul >= 1:
        return "ko"
    if han >= 1:
        return "auto"
    if cyr >= 1 and latin < 2:
        from . import active

        return active.target_lang()
    if latin >= 2:
        return "en"
    if cyr >= 1:
        from . import active

        return active.target_lang()
    return "auto"


def is_japanese_block(text: str) -> bool:
    """Whether a block is Japanese-shaped, including a single kanji.

    block_script needs two Han characters before it will call a block Japanese,
    which is reasonable for deciding what to send to a translator. It is wrong
    for deciding what is worth a second OCR opinion: a one-kanji label -
    無, 強, 気, 選 - is the read a recogniser is least sure of, and it was the
    one shape the refinement pass was never asked about. Those blocks were not
    refused, they were never read.

    Han without kana cannot be told apart from Chinese by the characters, and
    does not need to be: re-reading such a crop with jpn+eng and keeping the
    new reading only when it is also Han-shaped costs one crop of work and
    cannot rewrite Japanese into something else.
    """
    s = str(text or "").strip()
    if not s:
        return False
    if RE_KANA.search(s):
        return True
    return bool(RE_HAN.search(s)) and len(RE_LAT.findall(s)) < 3
