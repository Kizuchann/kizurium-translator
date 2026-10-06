"""OCR: reading what is written on the screen, and saying what it says.

До разреза всё чтение текста лежало в `live.py` вместе с отрисовкой, переводом
и циклом опроса, и не было видно, где кончается пиксель и начинается перевод.

Здесь ровно две вещи: получить изображение экрана и вернуть строки с боксами
(`ocr_image`, `region_text`, `rapid_ocr_lines`, `meiki_ocr_lines`,
`subtitle_ocr_lines` и панельные варианты), и разобрать уже распознанное
(`ocr_lines`, `is_garbage_ocr`, `is_garbage_japanese`, `english_ocr_quality`,
`skip_source`, `normalize_japanese_text`, `annotate_lines`, `japanese_blocks`).
Плюс отбор строк, по которым видно, что строка - это реплика, а не мусор
(`live_dialogue_cards`, `looks_like_ocr_mojibake_of_russian`, `drop_bad_ocr_boxes`).

Модуль ничего не знает про перевод и про cairo. На выходе - список словарей
с ключами `text`, `box`, `line_height`, `conf` и, где движок их проставил,
`engine`.

Тела перенесены из `live.py` дословно. Первый проход этой работы был написан по
памяти, и такой перенос тихо сдвигает пороги и границы, не меняя ни одного
теста.
"""
from __future__ import annotations

import re
import threading
from statistics import median

import pytesseract
from PIL import Image

from ..core.scripts import (  # noqa: F401
    dominant_script,
    is_in_target_script,
    pattern_of,
    script_of,
    target_language,
)
from ..core.text import (  # noqa: F401
    RE_CJK,
    RE_CYR,
    RE_LAT,
    _digit_spam,
    _real_english_words,
    block_lang,
    block_script,
    clean_ocr_text,
    is_hud_spam,
    is_japanese_block,
    is_subtitle_junk_line,
    looks_like_spoken_line,
    normalize_subtitle_ocr,
    ocr_coverage_stats,
    subtitle_preprocess,
    tdetail,
    tlog,
)
from ..layout.grouping import (  # noqa: F401
    gap_hits_gutter,
    is_desktop_chrome,
    is_known_gameplay_hud_phrase,
    is_mostly_russian,
    is_static_ui_overlay,
    looks_like_section_header,
    page_column_gutters,
    split_cross_column_merges,
    stitch_same_baseline,
)
from ..threads import budget
from ..typography.metrics import (  # noqa: F401
    RE_JPN,
    is_ink_band_garbage,
    is_latin_label,
    looks_like_ui_prompt,
    merge_subtitle_cluster,
    ui_text_fingerprint,
    unglue_english,
)

RAPID_OCR = None


RAPID_INIT_FAILED = False


MEIKI_OCR = None


MEIKI_INIT_FAILED = False


OCR_THREADS = 2
"""ONNX threads for the detector.

Derived rather than guessed - see `threads.budget()`. Two is what the budget
resolves to on any machine, and it is deliberate: a wider ONNX pool lowered idle
CPU, not latency, because the runtime keeps its intra-op threads spinning after
the call returns. The number is read from the budget so that the reason lives in
one place instead of here.
"""

OCR_THREADS = budget().ocr


_OCR_LOCK = threading.Lock()


DET_SIDE_LEN = 640


def ocr_lines(data: dict) -> list[dict]:
    grouped: dict[tuple[int, int], list[dict]] = {}
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = 0
        if conf < 20:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        if width < 4 or height < 4:
            continue
        block = int(data.get("block_num", [0] * n)[i])
        par = int(data.get("par_num", [0] * n)[i])
        line = int(data.get("line_num", [0] * n)[i])
        word = int(data.get("word_num", [i] * n)[i])
        key = (block, par, line)
        grouped.setdefault(key, []).append(
            {
                "text": text,
                "conf": conf,
                "line": line,
                "word": word,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "height": height,
            }
        )

    lines_out: list[dict] = []
    for words in grouped.values():
        words.sort(key=lambda item: (item["word"], item["left"]))
        chunks: list[list[dict]] = []
        current: list[dict] = []
        prev_right: int | None = None
        for word in words:
            gap = 0 if prev_right is None else word["left"] - prev_right
            height = max(1, word["height"])
            # Субтитры часто с большим letter-spacing — почти не режем строку.
            # Кнопки меню: два коротких Title Case и большой зазор.
            title_nav = False
            if current:
                prev = current[-1]["text"]
                nxt = word["text"]
                title_nav = (
                    len(prev) >= 2
                    and len(nxt) >= 2
                    and len(prev) <= 14
                    and len(nxt) <= 14
                    and prev[0].isupper()
                    and nxt[0].isupper()
                    and prev.replace("-", "").isalpha()
                    and nxt.replace("-", "").isalpha()
                    and not any(w["text"][:1].islower() for w in current)
                )
            # меню ~ большие дырки; обычная речь/субтитры — держим одной строкой
            gap_limit = max(22, height * (1.45 if title_nav else 2.4))
            if current and gap > gap_limit:
                chunks.append(current)
                current = []
            current.append(word)
            prev_right = word["right"]
        if current:
            chunks.append(current)

        for chunk in chunks:
            text = " ".join(word["text"] for word in chunk).strip()
            if not text:
                continue
            left = min(word["left"] for word in chunk)
            top = min(word["top"] for word in chunk)
            right = max(word["right"] for word in chunk)
            bottom = max(word["bottom"] for word in chunk)
            line_height = int(median(word["height"] for word in chunk))
            avg_conf = sum(float(word.get("conf", 0)) for word in chunk) / max(1, len(chunk))
            lines_out.append(
                {
                    "text": text,
                    "box": (left, top, right, bottom),
                    "line_height": line_height,
                    "conf": avg_conf,
                }
            )
    lines_out.sort(key=lambda item: (item["box"][1], item["box"][0]))
    return stitch_same_baseline(lines_out)


def live_dialogue_cards(blocks: list[dict] | None) -> list[dict]:
    """Только живой VN-баббл. Меню Preferences/Achievements сюда НЕ входят."""
    if is_static_ui_overlay(blocks):
        return []
    out: list[dict] = []
    for b in blocks or []:
        kind = str(b.get("kind", "") or "")
        if kind not in ("dialogue", "dialogue-line", "body"):
            continue
        src = str(b.get("source") or b.get("text") or "").strip()
        if not src:
            continue
        compact = re.sub(r"\s+", "", src)
        # совсем короткие UI-лейблы, ошибочно помеченные dialogue
        if len(compact) < 12 and not looks_like_spoken_line(src):
            continue
        if (
            looks_like_spoken_line(src)
            or len(src) >= 22
            or bool(re.search(r"[.!?…]", src))
        ):
            out.append(b)
    return out


def ui_line_fingerprints(key: str) -> set[str]:
    return {ui_text_fingerprint(p) for p in (key or "").split("|") if p.strip()}


def looks_like_ocr_mojibake_of_russian(text: str) -> bool:
    """Латиница-каша от OCR кириллицы (Настройки→Hact… / «BO obwe Kakoi»). Не переводить."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t or is_mostly_russian(t) or RE_CJK.search(t):
        return False
    # Short menu chips RapidOCR often returns as Latin soup instead of Cyrillic:
    # "сайт"→"sayt", "Словарь"→"Cnoapb", "Корректор"→"Kopp". Painting over them
    # on an already-Russian page is what wrecked the browser workspace.
    if re.fullmatch(
        r"(sayt|sait|cnoapb|slovar|kopp|korrekt|nirbl|voyti|voyti|"
        r"perevod|kontекст|kontеkst|izbran|russkiy|russk)",
        t,
        re.I,
    ):
        return True
    if re.search(
        r"\b(Ho\s*Boe|Kp\s*KOHTe|KOHTe|Cyuy|naron|Cnoapb|nds?\s*Premium|ca\s*T\s*nm)\b",
        t,
        re.I,
    ):
        return True
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 4:
        return False
    # Short soup without spaces: "KOHTeCT", "Cyuy"
    if " " not in t and len(letters) <= 16 and _real_english_words(t) == 0:
        if re.search(r"[A-Z]{2,}[a-z]|[a-z]{2,}[A-Z]|[bcdfghjklmnpqrstvwxz]{4,}", t):
            return True
    if len(letters) < 6:
        return False
    lat = sum(1 for c in letters if RE_LAT.match(c))
    if lat / len(letters) < 0.82:
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", t)
    if len(words) < 2:
        return False
    if _real_english_words(t) >= 3:
        return False
    short = sum(1 for w in words if len(re.sub(r"[^A-Za-z]", "", w)) <= 2)
    # много обрывков + мало нормальных EN-слов
    real_en = [
        w
        for w in words
        if len(w) >= 4
        and re.search(r"[aeiouy]", w, re.I)
        and not re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", w, re.I)
    ]
    if short >= max(2, int(len(words) * 0.4)) and len(real_en) <= 1 and t.count(" ") >= 1:
        return True
    if len(letters) >= 8 and short >= max(3, int(len(words) * 0.4)) and len(real_en) <= 1 and t.count(" ") >= 2:
        return True
    if re.search(
        r"\b(Hact|Hactpon|Ckpmh|Becb|Kakoi|obwe|woen|Bbll|nepe|Vuy|KWe|KMe|"
        r"bli|3kpa|Bbli|Xakt|Pactures|Becb\s*3kpa)\b",
        t,
        re.I,
    ):
        return True
    return False


def frame_already_in_target(lines: list[dict]) -> bool:
    """The screen is already in the target language; overlays would only hurt.

    RapidOCR reads Russian UI as Latin soup. Enough of that soup, and not
    enough real English, means the page is Russian and eng-ui must stay off.
    """
    if not lines:
        return False
    moji = 0
    real_en = 0
    for p in lines:
        t = str(p.get("text", ""))
        if looks_like_ocr_mojibake_of_russian(t):
            moji += 1
        elif (
            len(re.findall(r"[A-Za-z]", t)) >= 4
            and _real_english_words(t) == 0
            and not RE_CJK.search(t)
            and not is_mostly_russian(t)
            and len(t) <= 28
        ):
            moji += 1
        if _real_english_words(t) >= 2:
            real_en += 1
    # A bilingual page still has English chips ("site", "portal"); the soup
    # of mangled Cyrillic is what says the chrome is already Russian.
    return moji >= 5 and moji >= max(3, real_en)


def skip_source(text: str, target_lang: str | None = None) -> bool:
    """Не трогаем текст, который уже на языке назначения.

    Правая колонка двуязычного сайта, названия кнопок в русской локализации,
    имена персонажей на кириллице - всё это не нужно переводить: бэкенд,
    получив `ru→ru`, калечит текст.

    `target_lang` не задаётся - берётся язык назначения из состояния. Именно
    поэтому это параметр, а не константа: при `target=en` русский текст должен
    уйти в перевод, а русский перестаёт быть «своим».
    """
    lang = target_lang if target_lang is not None else target_language()
    if RE_CJK.search(text):
        return False
    if looks_like_ocr_mojibake_of_russian(text):
        return True
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 2:
        return True
    target = script_of(lang)
    if target is None:
        # Язык назначения неизвестен: решать нечем, а худший вариант - тихо
        # выбросить строку. Лучше лишний запрос бэкенду.
        return True
    if dominant_script(text) == target:
        return True
    # Непонятный набор букв - не переводим: это значки, а не язык.
    return dominant_script(text) is None


def is_garbage_ocr(text: str) -> bool:
    """Отсекает кашу OCR / кривой «перевод» / галлюцинации по текстуре."""
    t = text.strip()
    if len(t) < 2:
        return True
    if is_desktop_chrome(t):
        return True
    # японский текст — только is_garbage_japanese, не EN-эвристики
    if len(RE_JPN.findall(t)) >= 2:
        return False
    if is_ink_band_garbage(t):
        return True
    if is_known_gameplay_hud_phrase(t):
        return False
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 2:
        return True
    # A row of icons comes back as a handful of one- and two-letter scraps
    # ("L na o to"). A real label has a word in it. Counts and stat names
    # are longer than two letters or they are digits, and those are kept.
    scraps = re.findall(r"[A-Za-zА-Яа-яЁё]+", t)
    if (
        len(scraps) >= 4
        and sum(1 for w in scraps if len(w) <= 2) >= len(scraps) - 1
        and sum(len(w) for w in scraps) <= 18
    ):
        return True
    digits = sum(1 for c in t if c.isdigit())
    compact = re.sub(r"\s+", "", t)
    # Digit spam is an unbroken blob with no words, not "a word and a number".
    # The old ratio test threw away "Bonds 14820", "HP 120/120" and
    # "Lv. 12 to Lv. 13", which is most of what a game HUD shows.
    if _digit_spam(t, digits, compact):
        return True
    # Сплошной КАПС без строчных — OCR-шум по RU-оверлею.
    # НЕ трогаем нормальный перевод с акцентом: «СМЕРТЬ БЫЛА… ты готов».
    cyr_upper = sum(1 for c in letters if RE_CYR.match(c) and c.isupper())
    cyr_lower = sum(1 for c in letters if RE_CYR.match(c) and c.islower())
    if (
        cyr_lower == 0
        and cyr_upper >= 6
        and cyr_upper / len(letters) >= 0.70
    ):
        return True
    vowels = set("aeiouyAEIOUYаеёиоуыэюяАЕЁИОУЫЭЮЯ")
    # Switch/Strength и т.п. — мало гласных, но валидные EN-слова
    _vowel_ok = {
        "switch", "strength", "stretch", "script", "splash", "sprint", "struck",
        "through", "thought", "thrust", "rhythm", "stress", "street", "strict",
        "strong", "string", "screen", "scroll", "scheme", "school", "speech",
        "sphere", "sylph", "nymph", "crypt", "glyph", "psych", "mythic",
    }
    for w in re.findall(r"[A-Za-zА-Яа-яЁё]{6,}", t):
        if w.lower() in _vowel_ok:
            continue
        if sum(c in vowels for c in w) / len(w) < 0.14:
            return True
    weird = 0
    for w in re.findall(r"[А-Яа-яЁё]{4,}", t):
        flips = sum(1 for a, b in zip(w, w[1:]) if a.isupper() != b.isupper())
        if flips >= 2:
            weird += 1
    if weird >= 2:
        return True
    # Латиница без нормальных слов (шум RapidOCR по траве).
    #
    # Ветка открывается только если текст написан преимущественно латиницей.
    # Раньше порог был `len(lat) >= 4`, где `lat` - это `RE_LAT.findall`, то
    # есть отдельные буквы, а не слова. Одно нормальное слово открывало ветку:
    # «Низкая температура тела... вводим Hexamethasone 20» даёт 13 букв, и
    # дальше `len(good) == 1` объявляло перевод мусором. На экране было
    # `shown=0 retry` бесконечно, при живом переводе в логе.
    #
    # Порог по буквам здесь и остаётся: 20 символов латиницы без единого
    # нормального слова - это мусор независимо от письменности остального.
    # Меняется только проверка, что перед нами вообще латинский текст.
    lat = RE_LAT.findall(t)
    jp = RE_JPN.findall(t)
    if len(lat) >= 4 and len(jp) == 0 and dominant_script(t) != "cyrl":
        words = re.findall(r"[A-Za-z]{3,}", t)
        good = [w for w in words if sum(c in "aeiouAEIOU" for c in w) >= 1]
        short_tokens = re.findall(r"[A-Za-z]{2,}", t)
        # Two-letter tokens are a sentence, not noise: "Lv. 12 to Lv. 13" is
        # three of them and was being deleted as Latin-without-words.
        if len(good) == 0 and len(short_tokens) < 2:
            return True
        # «Mw (r ~ 6-8 nMkcenen)» и т.п.
        if len(good) == 1 and len(t) > 18 and sum(c.isdigit() or c in "()~.,-" for c in t) >= 4:
            return True
    return False


def english_ocr_quality(lines: list[dict]) -> float:
    score = 0.0
    for p in lines:
        t = str(p.get("text", ""))
        if is_garbage_ocr(t) or is_desktop_chrome(t):
            continue
        words = re.findall(r"[A-Za-z]{3,}", t)
        good = [w for w in words if sum(c in "aeiouAEIOU" for c in w) >= 1]
        if len(good) >= 2 or (len(good) >= 1 and len(t) >= 14):
            score += 12 + len(good) * 4 + float(p.get("conf", 0)) * 0.04
        elif len(good) == 1 and float(p.get("conf", 0)) >= 78:
            score += 5
    return score


def ocr_with(img: Image.Image, langs: str, psm: str | None = None) -> list[dict]:
    # Adaptive PSMgeometry picks 8/7/6/11. Callers may still force
    # a mode for specialised passes (subtitles try several variants).
    if psm is None:
        from .psm import psm_for_image

        psm = psm_for_image(img)
    data = pytesseract.image_to_data(
        img,
        lang=langs,
        output_type=pytesseract.Output.DICT,
        config=f"--oem 1 --psm {psm} -c preserve_interword_spaces=1",
    )
    return ocr_lines(data)


def normalize_japanese_text(text: str) -> str:
    # Tesseract расставляет пробелы между почти каждым японским символом.
    text = text.replace("―", "ー").replace("–", "ー").replace("~", "～")
    text = re.sub(r"(?<=[ぁ-んァ-ンー々一-龯])\s+(?=[ぁ-んァ-ンー々一-龯])", "", text)
    text = re.sub(r"\s+([、。？！,.!?])", r"\1", text)
    text = re.sub(r"([（「『])\s+", r"\1", text)
    text = re.sub(r"\s+([）」』])", r"\1", text)
    text = re.sub(r"ー{3,}", " ", text)
    # Частые ошибки Tesseract именно на этом экране Sekaі.
    fixes = (
        ("グーム", "ゲーム"),
        ("グム", "ゲーム"),
        ("ータ", "データ"),
        ("WトF", "Wi-Fi"),
        ("ＷトＦ", "Wi-Fi"),
        ("ワイF", "Wi-Fi"),
        ("スクランフル", "スクランブル"),
        ("サイドストーリード", "サイドストーリー"),
        ("メンバの", "メンバーの"),
        ("前蝠", "前編"),
        ("前蝙", "前編"),
        ("それそれ", "それぞれ"),
        ("スタンフミッショ", "スタンプミッション"),
        ("スタンブミッショ", "スタンプミッション"),
        ("コラボ限定メンバー羚択", "コラボ限定メンバー登場"),
        ("ストーリ", "ストーリー"),
        ("・トランク", "ランク"),
        ("途亦こと", "遊ぶこと"),
        ("遊呂こと", "遊ぶこと"),
        ("遊ぶぶこと", "遊ぶこと"),
        ("削用方法", "利用方法"),
        ("則用方法", "利用方法"),
        ("保譜者", "保護者"),
        ("保謀者", "保護者"),
        ("保識者", "保護者"),
        ("相請", "相談"),
        ("一 部", "一部"),
        ("ー部", "一部"),
        ("できますかが", "できますが"),
        ("ことちできます", "こともできます"),
        ("①⑧ 末満", "18歳未満"),
        ("①⑧ 歳末満", "18歳未満"),
        ("①⑧ 歳未満", "18歳未満"),
        ("の方ヘ", "の方へ"),
        ("お笠さん", "お母さん"),
        ("あ雷さん", "お母さん"),
        ("註向", "許可"),
        ("詣可", "許可"),
        ("か必きです", "が必要です"),
        ("烈める", "始める"),
        ("ちらう", "もらう"),
        ("買っよう", "買うよう"),
        ("点源", "点滅"),
        ("志君 mex", "画面の点滅"),
        ("道切", "適切"),
        ("遊んでく:P,", "遊んでください。"),
        ("テデデータ", "データ"),
        ("トランク", "ランク"),
        ("ストーリ", "ストーリー"),
        ("デデデータ", "データ"),
        ("デデータ", "データ"),
        ("テデデデータ", "データ"),
        ("テデデデデータ", "データ"),
        ("タウンロード", "ダウンロード"),
        ("ダウンロド", "ダウンロード"),
        ("Wト環境", "Wi-Fi環境"),
        ("Wイ環境", "Wi-Fi環境"),
        ("W卜環境", "Wi-Fi環境"),
        ("よろしいですか?", "よろしいですか？"),
        ("またその話?", "またその話？"),
    )
    for old, new in fixes:
        text = text.replace(old, new)
    # Иконка лампочки + TIPS → TIPS
    text = re.sub(r"^[QO0ДД]?TIPS\b", "TIPS", text, flags=re.I)
    text = re.sub(r"\bQTIPS\b", "TIPS", text, flags=re.I)
    text = re.sub(r"\bOTIPS\b", "TIPS", text, flags=re.I)
    # Meiki иногда глотает открывающую「
    if "」" in text and "「" not in text:
        text = re.sub(r"^([^」\n]{1,24})」", r"「\1」", text)
    # Meiki часто жрёт точку: 3778MB → 377.8MB
    text = re.sub(r"ストーリーー+", "ストーリー", text)
    text = re.sub(r"(?<![.\d])(\d{3})(\d)MB", r"\1.\2MB", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _rapid_rows(out) -> list:
    """Normalise rapidocr>=3 output into the legacy ``[[box, text, conf],...]``.

    The v3 API returns a result object with parallel ``boxes``/``txts``/``scores``
    attributes (or a dict) instead of a tuple of rows, so the rest of the engine
    can keep consuming one shape.
    """
    if out is None:
        return []
    boxes = getattr(out, "boxes", None)
    txts = getattr(out, "txts", None)
    scores = getattr(out, "scores", None)
    if boxes is None and isinstance(out, dict):
        boxes = out.get("boxes")
        txts = out.get("txts")
        scores = out.get("scores")
    if boxes is None or txts is None:
        return []
    rows: list = []
    for i, (box, text) in enumerate(zip(boxes, txts)):
        if box is None or text is None:
            continue
        score = 1.0
        if scores is not None and i < len(scores) and scores[i] is not None:
            try:
                score = float(scores[i])
            except (TypeError, ValueError):
                score = 1.0
        # quad corners (x, y) -> flat [x1, y1, x2, y2,...]
        pts = [[float(p[0]), float(p[1])] for p in box]
        rows.append([pts, str(text), score])
    return rows


CORE_REREAD_BELOW = 80.0


def stitch_rows_by_baseline(lines: list[dict]) -> list[dict]:
    """Склеивает фрагменты одной горизонтальной строки, даже если OCR вернул их не по X.
    Латиницу (TAP!/GACHA) с японским в одну строку НЕ склеивает.
    Колонки сайта через пустой gutter не склеивает."""
    if len(lines) < 2:
        return lines

    rw = max((int(p["box"][2]) for p in lines), default=0) + 8
    gutters = page_column_gutters(lines, rw)

    def script_tag(text: str) -> str:
        if is_latin_label(text):
            return "lat"
        if len(RE_JPN.findall(text)) >= 1:
            return "jp"
        return "other"

    rows: list[list[dict]] = []
    for item in sorted(lines, key=lambda p: (p["box"][1] + p["box"][3]) / 2):
        cy = (item["box"][1] + item["box"][3]) / 2
        tag = script_tag(str(item.get("text", "")))
        placed = False
        for row in rows:
            row_cy = median((p["box"][1] + p["box"][3]) / 2 for p in row)
            row_h = median(p["line_height"] for p in row)
            row_tag = script_tag(row[0].get("text", ""))
            # разный скрипт — разные ряды (иначе TAP! прилипает к tip)
            if tag != row_tag and {tag, row_tag} == {"lat", "jp"}:
                continue
            # Measured down the screen, not across the line. Measuring across it
            # was tried and is wrong for the reason worth writing down: two
            # *different* lines of a slanted row, offset along the slope so that
            # one starts where the other would have ended, have centres that are
            # collinear along the slant and so come out a few pixels apart
            # measured across it, while their bands are a whole line height
            # apart. They are two lines and the test calls them one. Vertical
            # separation cannot tell that case apart either, but it errs towards
            # leaving a line in two cards rather than merging two into one, and
            # a split line is the recoverable mistake.
            if abs(cy - row_cy) <= max(8, min(14, row_h * 0.55)) :
                row.append(item)
                placed = True
                break
        if not placed:
            rows.append([item])

    merged: list[dict] = []
    for row in rows:
        row.sort(key=lambda p: p["box"][0])
        segments: list[list[dict]] = []
        cur = [row[0]]
        for item in row[1:]:
            prev = cur[-1]
            gap = item["box"][0] - prev["box"][2]
            lh = max(8, median(p["line_height"] for p in cur + [item]))
            prev_tag = script_tag(prev.get("text", ""))
            item_tag = script_tag(item.get("text", ""))
            if prev_tag != item_tag and {prev_tag, item_tag} == {"lat", "jp"}:
                segments.append(cur)
                cur = [item]
                continue
            jpish = prev_tag == "jp" or item_tag == "jp"
            max_gap = max(30, lh * 2.4) if jpish else max(20, lh * 1.2)
            span = item["box"][2] - prev["box"][0]
            if gutters and gap_hits_gutter(prev["box"][2], item["box"][0], gutters) or (
                not jpish
                and span >= max(280, int(rw * 0.45))
                and gap > max(14, lh * 0.85)
            ):
                segments.append(cur)
                cur = [item]
            elif gap <= max_gap:
                cur.append(item)
            else:
                segments.append(cur)
                cur = [item]
        segments.append(cur)
        for segment in segments:
            x1 = min(p["box"][0] for p in segment)
            y1 = min(p["box"][1] for p in segment)
            x2 = max(p["box"][2] for p in segment)
            y2 = max(p["box"][3] for p in segment)
            text = normalize_japanese_text(" ".join(p["text"] for p in segment))
            engines = {p.get("engine") for p in segment if p.get("engine")}
            # The row is one line, so it has one slope. Carried over from the
            # fragments it was built from: a merged row drops it otherwise, and
            # the card for slanted text is then drawn level over slanted text.
            angs = [
                float(p.get("angle", 0) or 0)
                for p in segment
                if float(p.get("angle", 0) or 0) != 0.0
            ]
            merged.append(
                {
                    "text": text,
                    "box": (x1, y1, x2, y2),
                    "line_height": int(median(p["line_height"] for p in segment)),
                    "conf": sum(float(p.get("conf", 0)) for p in segment) / len(segment),
                    "engine": ",".join(sorted(engines)) if engines else "tesseract",
                    "kind": segment[0].get("kind"),
                    "wrap": segment[0].get("wrap"),
                    "oneline": segment[0].get("oneline"),
                    "angle": (sum(angs) / len(angs)) if angs else 0.0,
                    # The row is one line and so one thickness. The largest of
                    # the fragments' is the line's: a word is not thinner than
                    # the row it is in, and taking the smallest would let one
                    # short fragment set the size of all the others.
                    "band": max(int(p.get("band") or 0) for p in segment),
                }
            )
    merged.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return split_cross_column_merges(merged, rw)


def subtitle_ocr_lines(region_img: Image.Image) -> list[dict]:
    """Спец-проход для игровых/видео субтитров: одна-две белые строки на шумном фоне."""
    if region_img.width < 220:
        return []
    subtitle_area = region_img.width / max(1, region_img.height) >= 2.2
    aspect = region_img.width / max(1, region_img.height)
    # Was height<=1000 (FHD-ish). Allow full landscape frames at any resolution.
    game_frame = (
        region_img.width >= 500
        and aspect >= 1.25
        and region_img.height <= max(1000, int(region_img.width * 0.65))
    )
    if not subtitle_area and not game_frame:
        return []

    # Главный pass: psm 11 хорошо разделяет две строки субтитра и не
    # склеивает их с мусором из верхней части кадра.
    canonical = subtitle_preprocess(region_img, 205)
    canonical = canonical.resize(
        (canonical.width * 2, canonical.height * 2), Image.Resampling.LANCZOS
    )
    try:
        canonical_lines = ocr_with(canonical, "eng", "11")
    except Exception:
        canonical_lines = []
    canonical_lines = [
        p
        for p in canonical_lines
        if len(RE_LAT.findall(p["text"])) >= 8
        and float(p.get("conf", 0)) >= 25
        and p["line_height"] <= max(80, int(region_img.height * 0.28))
        and (
            subtitle_area
            or p["box"][1] >= int(region_img.height * 0.35 * 2)
        )
    ]
    if canonical_lines:
        canonical_lines.sort(key=lambda p: (p["box"][1], p["box"][0]))
        canonical_lines = canonical_lines[:2]
        text = " ".join(
            re.sub(r"\bGotit\b", "Got it", p["text"], flags=re.I)
            for p in canonical_lines
        )
        text = re.sub(
            r"\bThings,\s+there's\b", "Thing is, there's", text, flags=re.I
        )
        x1 = min(int(p["box"][0] / 2) for p in canonical_lines)
        y1 = min(int(p["box"][1] / 2) for p in canonical_lines)
        x2 = max(int(p["box"][2] / 2) for p in canonical_lines)
        y2 = max(int(p["box"][3] / 2) for p in canonical_lines)
        return [
            {
                "text": text,
                "box": (x1, y1, x2, y2),
                "line_height": max(8, max(int(p["line_height"] / 2) for p in canonical_lines)),
                "conf": sum(float(p["conf"]) for p in canonical_lines) / len(canonical_lines),
            }
        ]

    candidates: list[dict] = []
    # Часто пользователь выделяет с запасом сверху; нижняя часть даёт меньше мусора с фона.
    crops = [(0, region_img), (int(region_img.height * 0.35), region_img.crop((0, int(region_img.height * 0.35), region_img.width, region_img.height)))]
    for yoff, crop in crops:
        if crop.height < 24:
            continue
        for threshold in (205, 195, 185, 165, 145):
            proc = subtitle_preprocess(crop, threshold)
            scale = 2
            work = proc.resize((proc.width * scale, proc.height * scale), Image.Resampling.LANCZOS)
            for psm in ("6", "11"):
                try:
                    data = pytesseract.image_to_data(
                        work,
                        lang="eng",
                        output_type=pytesseract.Output.DICT,
                        config=f"--oem 1 --psm {psm} -c preserve_interword_spaces=1",
                    )
                except Exception:
                    continue
                words_all: list[dict] = []
                for i, raw in enumerate(data["text"]):
                    text = raw.strip()
                    if not text:
                        continue
                    try:
                        conf = float(data["conf"][i])
                    except (ValueError, TypeError):
                        conf = 0
                    if conf < 25:
                        continue
                    if not RE_LAT.search(text):
                        continue
                    left = int(data["left"][i] / scale)
                    top = int(data["top"][i] / scale) + yoff
                    width = int(data["width"][i] / scale)
                    height = int(data["height"][i] / scale)
                    if width < 3 or height < 6:
                        continue
                    # Огромные компоненты — фон/персонаж, а не буквы субтитра.
                    if height > max(40, int(region_img.height * 0.22)):
                        continue
                    if width > max(180, int(region_img.width * 0.32)):
                        continue
                    words_all.append(
                        {
                            "text": text,
                            "conf": conf,
                            "left": left,
                            "top": top,
                            "right": left + width,
                            "bottom": top + height,
                            "height": height,
                            "cy": top + height / 2.0,
                        }
                    )
                rows: list[list[dict]] = []
                for word in sorted(words_all, key=lambda w: w["cy"]):
                    placed = False
                    for row in rows:
                        row_cy = median(w["cy"] for w in row)
                        row_h = median(w["height"] for w in row)
                        if abs(word["cy"] - row_cy) <= max(9.0, row_h * 0.55):
                            row.append(word)
                            placed = True
                            break
                    if not placed:
                        rows.append([word])
                for words in rows:
                    words.sort(key=lambda w: w["left"])
                    text = clean_ocr_text(" ".join(w["text"] for w in words))
                    letters = len(RE_LAT.findall(text))
                    if letters < 8:
                        continue
                    x1 = min(w["left"] for w in words)
                    y1 = min(w["top"] for w in words)
                    x2 = max(w["right"] for w in words)
                    y2 = max(w["bottom"] for w in words)
                    line_height = int(median(w["height"] for w in words))
                    avg_conf = sum(float(w["conf"]) for w in words) / max(1, len(words))
                    width_score = min(1.0, (x2 - x1) / max(1, region_img.width * 0.45))
                    # Высокая confidence важнее ширины: иначе мусор сверху
                    # иногда побеждает настоящую вторую строку субтитров.
                    score = letters * 2 + avg_conf * 0.9 + width_score * 20
                    candidates.append(
                        {
                            "text": text,
                            "box": (x1, y1, x2, y2),
                            "line_height": line_height,
                            "conf": avg_conf,
                            "_score": score,
                            "_variant": f"{yoff}:{threshold}:{psm}",
                        }
                    )

    if not candidates:
        return []
    # Не смешиваем варианты бинаризации: иначе строка от одного порога
    # соединяется с мусором от другого. Выбираем лучшую пару строк одного pass.
    passes: dict[str, list[dict]] = {}
    for candidate in candidates:
        passes.setdefault(str(candidate["_variant"]), []).append(candidate)
    best_pass: list[dict] = []
    best_pass_score = -1.0
    for pass_name, pass_candidates in passes.items():
        pass_candidates.sort(key=lambda p: float(p.get("_score", 0)), reverse=True)
        selected_pass: list[dict] = []
        for candidate in pass_candidates:
            cy = (candidate["box"][1] + candidate["box"][3]) / 2
            if any(
                abs(cy - (old["box"][1] + old["box"][3]) / 2)
                <= max(10, candidate["line_height"], old["line_height"]) * 0.75
                for old in selected_pass
            ):
                continue
            selected_pass.append(candidate)
            if len(selected_pass) == 2:
                break
        pass_score = sum(float(p.get("_score", 0)) for p in selected_pass)
        # На обычных белых игровых субтитрах этот pass даёт наиболее чистый
        # результат; не даём шумному psm 6 его перебить.
        if pass_name == "0:205:6":
            pass_score += 1000
        if pass_score > best_pass_score:
            best_pass = selected_pass
            best_pass_score = pass_score

    selected = best_pass
    # Берём максимум две независимые строки одной реплики.
    for candidate in candidates:
        cy = (candidate["box"][1] + candidate["box"][3]) / 2
        if any(
            abs(cy - (old["box"][1] + old["box"][3]) / 2)
            <= max(10, candidate["line_height"], old["line_height"]) * 0.75
            for old in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) == 2:
            break
    selected.sort(key=lambda p: (p["box"][1], p["box"][0]))
    if not selected:
        return []
    # Две строки переводятся одним запросом: сохраняем контекст и грамматику.
    text = " ".join(
        re.sub(r"\bGotit\b", "Got it", p["text"], flags=re.I) for p in selected
    )
    text = re.sub(r"\bThings,\s+there's\b", "Thing is, there's", text, flags=re.I)
    x1 = min(p["box"][0] for p in selected)
    y1 = min(p["box"][1] for p in selected)
    x2 = max(p["box"][2] for p in selected)
    y2 = max(p["box"][3] for p in selected)
    return [
        {
            "text": text,
            "box": (x1, y1, x2, y2),
            "line_height": max(p["line_height"] for p in selected),
            "conf": sum(float(p["conf"]) for p in selected) / len(selected),
        }
    ]


def looks_like_dialogue_choice(text: str) -> bool:
    """Пункты выбора в катсцене (Shake your head / Tell the truth), не Name:-реплика."""
    t = unglue_english(normalize_subtitle_ocr(text or "")).strip()
    if len(t) < 5 or len(t) > 80:
        return False
    if looks_like_spoken_line(t):
        return False
    if is_hud_spam(t) or is_desktop_chrome(t) or is_garbage_ocr(t):
        return False
    if is_overlay_echo_ocr(t):
        return False
    if is_subtitle_junk_line(t):
        return False
    # [X] Confirm / Confirm
    if re.match(r"^(\[[^\]]{1,6}\]\s*)?(Confirm|Cancel|Back|Next|Skip|Close|Accept|Decline)\b", t, re.I):
        return True
    if re.match(r"^\[[A-Z0-9x]\]\s+\S", t, re.I):
        return True
    lat = len(RE_LAT.findall(t))
    if lat < 8:
        return False
    words = [w for w in re.findall(r"[A-Za-z']+", t)]
    if not (2 <= len(words) <= 14):
        return False
    # действие/ответ: с заглавной, без длинного повествования
    if not t[0].isupper():
        return False
    # не продолжение субтитра внизу (длинное с lowercase mid)
    if t.endswith(("...", "…")) and len(t) > 50:
        return False
    # A choice is a short reply. A sentence of nine or more words is the
    # body of a panel ("Some items, like Helmets, can not be equipped…"),
    # and treating it as a button sizes the whole paragraph as one huge label.
    if len(words) > 8:
        return False
    return True


def extract_dialogue_choice_lines(
    lines: list[dict],
    dialogue_blocks: list[dict] | None = None,
    rw: int = 0,
    rh: int = 0,
) -> list[dict]:
    """Choice/Confirm рядом с Name:-репликой — OCR их видит, collect_subtitle выкидывал."""
    dialogue_blocks = dialogue_blocks or []
    used: set[str] = set()
    dlg_ys: list[int] = []
    for b in dialogue_blocks:
        raw = str(b.get("text", ""))
        used.add(re.sub(r"\s+", "", raw).casefold())
        for ln in raw.split("\n"):
            used.add(re.sub(r"\s+", "", ln).casefold())
        dlg_ys.append(int(b["box"][1]))
        for lb in b.get("line_boxes") or []:
            dlg_ys.append(int(lb["box"][1]))
            used.add(re.sub(r"\s+", "", str(lb.get("text", ""))).casefold())
    min_dlg_y = min(dlg_ys) if dlg_ys else 10**9
    out: list[dict] = []
    for p in lines:
        t = str(p.get("text", "")).strip()
        if not looks_like_dialogue_choice(t):
            continue
        compact = re.sub(r"\s+", "", t).casefold()
        if not compact or any(compact == u or (len(compact) > 10 and compact in u) for u in used):
            continue
        y1 = int(p["box"][1])
        is_btn = bool(re.search(r"\b(Confirm|Cancel|Back|Next|Skip)\b", t, re.I))
        # choice обычно ВЫШЕ нижней реплики; Confirm может быть справа внизу
        if dialogue_blocks and not is_btn and y1 >= min_dlg_y - 6:
            continue
        copy = dict(p)
        copy["text"] = unglue_english(normalize_subtitle_ocr(t))
        copy["kind"] = "ui"
        copy["wrap"] = len(copy["text"]) > 26
        copy["pin_box"] = True
        out.append(copy)
        used.add(compact)
    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    if out:
        tlog(
            f"choices n={len(out)} "
            + " || ".join(str(p.get("text", ""))[:36] for p in out[:6])
        )
    return out[:8]


def stitch_subtitle_fragments(lines: list[dict]) -> list[dict]:
    """Склеивает левую+правую половину одной реплики на одной baseline (letterbox).
    Веб-колонки через пустой gutter / большой зазор НЕ склеивает."""
    if len(lines) < 2:
        return lines
    rw = max((int(p["box"][2]) for p in lines), default=0) + 8
    gutters = page_column_gutters(lines, rw)
    ordered = sorted(lines, key=lambda p: ((p["box"][1] + p["box"][3]) / 2, p["box"][0]))
    out: list[dict] = []
    used: set[int] = set()
    for i, a in enumerate(ordered):
        if i in used:
            continue
        ax1, ay1, ax2, ay2 = a["box"]
        acy = (ay1 + ay2) / 2
        ah = max(8, ay2 - ay1)
        cluster = [a]
        used.add(i)
        for j, b in enumerate(ordered):
            if j in used:
                continue
            bx1, by1, bx2, by2 = b["box"]
            bcy = (by1 + by2) / 2
            if abs(bcy - acy) > max(10, ah * 0.7):
                continue
            # горизонтальный сосед (в т.ч. большой gap после точки)
            gap = bx1 - ax2
            if gap < -ah * 0.5:
                continue
            if gap > max(220, ah * 12):
                continue
            ta = str(a.get("text", ""))
            tb = str(b.get("text", ""))
            # сайдбар History + попап «Are you sure…» на одной высоте — НЕ клеить
            if looks_like_ui_prompt(ta) or looks_like_ui_prompt(tb):
                continue
            # не клеим второй Name:
            if looks_like_spoken_line(tb) and looks_like_spoken_line(ta):
                continue
            # Zoom In | Zoom Out | Location — отдельные кнопки на одной baseline
            if looks_like_ui_prompt(ta) and looks_like_ui_prompt(tb):
                continue
            # короткий лейбл + длинная фраза (разные колонки)
            if (len(ta) <= 22 and len(tb) >= 28) or (len(tb) <= 22 and len(ta) >= 28):
                if abs(bx1 - ax1) > 80:
                    continue
            if not (looks_like_spoken_line(ta) or looks_like_subtitle_continue(tb) or looks_like_spoken_line(tb)):
                if len(RE_LAT.findall(tb)) < 6:
                    continue
            # колонки сайта: желоб / два длинных абзаца на одной высоте
            if gutters and gap_hits_gutter(ax2, bx1, gutters):
                continue
            if gap > max(28, ah * 1.35) and len(ta) >= 18 and len(tb) >= 18:
                continue
            if (
                gap > max(22, ah)
                and len(ta) >= 16
                and len(tb) >= 16
                and ta[:1].isupper()
                and tb[:1].isupper()
                and not looks_like_spoken_line(ta)
            ):
                continue
            # большой горизонтальный зазор = разные колонки меню
            if gap > max(80, ah * 4):
                continue
            cluster.append(b)
            used.add(j)
            ax2 = max(ax2, bx2)
        if len(cluster) == 1:
            out.append(a)
        else:
            merged = merge_subtitle_cluster(cluster)
            tlog(f"sub-stitch n={len(cluster)} chars={len(merged['text'])}")
            tdetail(f"sub-stitch preview={merged['text'][:70]!r}")
            out.append(merged)
    out.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return split_cross_column_merges(out, rw)


def looks_like_subtitle_continue(text: str) -> bool:
    """Продолжение реплики без имени (вторая/третья строка субтитра)."""
    t = unglue_english(text.strip())
    if len(t) < 4 or looks_like_spoken_line(t):
        return False
    if is_subtitle_junk_line(t):
        return False
    if is_hud_spam(t) or is_desktop_chrome(t):
        return False
    if is_garbage_ocr(t):
        return False
    lat = len(RE_LAT.findall(t))
    if lat < 4:
        return False
    # glued OCR без пробелов (warmththeirheart…)
    if lat >= 18 and t.count(" ") <= 2:
        return True
    # не UI-кнопки
    if lat <= 12 and t.istitle() and " " not in t and not t.endswith((".", "!", "?", ",")):
        return False
    return True


def is_overlay_echo_ocr(text: str) -> bool:
    """OCR читает наши RU-плашки / кашу от кириллицы — не считать сменой реплики.

    Пример из лога: soft-recheck 'Bai 3 e Ma H: Bep Ho...' → вечный clear+мигание.
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return False
    if looks_like_ocr_mojibake_of_russian(t):
        return True
    # «Apro: pek Ae Bcero…» — Name: от эха RU, тело каша. НЕ spoken.
    m_name = re.match(r"^([^:]{1,28}):\s*(.+)$", t)
    if m_name:
        body = m_name.group(2).strip()
        btoks = body.split()
        if len(btoks) >= 3:
            short = sum(
                1 for w in btoks if len(re.sub(r"[^A-Za-zА-Яа-яЁё]", "", w)) <= 2
            )
            if short / len(btoks) >= 0.35:
                return True
        bcomp = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", body)
        if len(bcomp) >= 10:
            bv = len(re.findall(r"[aeiouyAEIOUYаеёиоуыэюяАЕЁИОУЫЭЮЯ]", bcomp))
            # каша без нормальных EN-слов
            real = re.findall(r"[A-Za-z]{4,}", body)
            real_ok = [
                w
                for w in real
                if sum(c in "aeiouAEIOU" for c in w) >= 1
                and not re.search(r"[qwxz]{2}|[bcdfghjklmnpqrstvwxz]{5,}", w, re.I)
            ]
            if bv / len(bcomp) < 0.38 and len(real_ok) <= 1:
                return True
    # нормальный EN (This will lose… / Are you sure…) — НЕ echo
    if looks_like_spoken_line(t) or looks_like_ui_prompt(t):
        return False
    if re.search(
        r"\b(this|will|lose|unsaved|progress|sure|return|menu|want|you|are|"
        r"yes|no|window|fullscreen|display|preferences|history|achievements|"
        r"obtained|search|waiting|guard|attack|monster|hints|tutorial)\b",
        t,
        re.I,
    ):
        return False
    cyr = len(RE_CYR.findall(t))
    lat = len(RE_LAT.findall(t))
    cjk = len(RE_CJK.findall(t))
    if cyr >= 3 and cyr >= lat and cjk < 2:
        return True
    toks = t.split()
    if len(toks) >= 4:
        short = sum(1 for w in toks if len(re.sub(r"[^A-Za-zА-Яа-яЁё]", "", w)) <= 2)
        if short / len(toks) >= 0.45 and (lat + cyr) >= 6 and cjk < 2:
            if _real_english_words(t) < 3:
                return True
    # кириллица, прочитанная RapidOCR как Latin («Tbl WMee Wb B BWAy»)
    compact = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", t)
    if len(compact) >= 12 and cjk < 2:
        vows = len(re.findall(r"[aeiouyAEIOUYаеёиоуыэюяАЕЁИОУЫЭЮЯ]", compact))
        # было 0.34 — ломало «Thiswill loseunsavedprogress» (vowel≈0.33)
        if vows / len(compact) < 0.28:
            return True
    # тело после Name: — отдельно (Caal: Tbl WMee…)
    body = re.sub(r"^[^:]{1,28}:\s*", "", t)
    if body != t and len(body) >= 8:
        bcomp = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", body)
        if len(bcomp) >= 8:
            bv = len(re.findall(r"[aeiouyAEIOUYаеёиоуыэюяАЕЁИОУЫЭЮЯ]", bcomp))
            if bv / len(bcomp) < 0.28:
                return True
    letters = re.sub(r"[^A-Za-z]", "", t)
    if 8 <= len(letters) <= 64 and cjk < 2:
        vowels = len(re.findall(r"[aeiouyAEIOUY]", letters))
        if t.count(" ") >= 3 and vowels / max(1, len(letters)) < 0.26:
            if _real_english_words(t) < 2:
                return True
    return False


def collect_subtitle_blocks(lines: list[dict], rw: int = 0, rh: int = 0) -> list[dict]:
    """
    Берёт Name:... и все продолжения ниже (до 4 строк), склеивает в один блок.
    Продолжения без имени на первой строке относятся к тому же блоку, что и
    реплика с именем.
    """
    if not lines:
        return []
    ordered = sorted(lines, key=lambda p: (p["box"][1], p["box"][0]))
    if rw <= 0:
        rw = max(int(p["box"][2]) for p in ordered) + 80
    if rh <= 0:
        rh = max(int(p["box"][3]) for p in ordered) + 40
    anchors = [p for p in ordered if looks_like_spoken_line(str(p.get("text", "")))]
    if not anchors:
        return []
    used: set[int] = set()
    out: list[dict] = []
    for anc in anchors:
        if id(anc) in used:
            continue
        cluster = [anc]
        used.add(id(anc))
        ax1, ay1, ax2, ay2 = anc["box"]
        lh = max(14, int(anc.get("line_height", 20)))
        bottom = ay2
        acx = (ax1 + ax2) / 2
        for p in ordered:
            if id(p) in used:
                continue
            t = str(p.get("text", "")).strip()
            if looks_like_spoken_line(t):
                break  # следующий спикер
            if is_subtitle_junk_line(t, p.get("box"), rw, rh):
                continue
            if not looks_like_subtitle_continue(t):
                continue
            x1, y1, x2, y2 = p["box"]
            if y1 < ay1 - lh * 0.4:
                continue
            gap = y1 - bottom
            if gap > lh * 3.8:
                continue
            if gap < -lh * 0.5:
                continue
            pcx = (x1 + x2) / 2
            overlap = max(0, min(ax2, x2) - max(ax1, x1))
            wide = max(1, min(ax2 - ax1, x2 - x1))
            centered = abs(pcx - acx) <= max(ax2 - ax1, x2 - x1) * 0.7
            if overlap / wide < 0.08 and not centered:
                continue
            cluster.append(p)
            used.add(id(p))
            bottom = max(bottom, y2)
            if len(cluster) >= 5:
                break
        out.append(merge_subtitle_cluster(cluster, rw, rh))
        tlog(
            f"sub-block lines={len(cluster)} chars={len(out[-1]['text'])}"
        )
        tdetail(f"sub-block preview={out[-1]['text'][:70]!r}")
    return out


def looks_like_gameplay_hud_line(text: str) -> bool:
    """Любой читаемый UI/тост — без whitelist слов конкретной игры."""
    t = unglue_english(normalize_subtitle_ocr(text or "")).strip()
    if len(t) < 3:
        return False
    if is_hud_spam(t) or is_desktop_chrome(t):
        return False
    if looks_like_spoken_line(t):
        return True
    if is_known_gameplay_hud_phrase(t):
        return True
    if is_garbage_ocr(t):
        return False
    letters = len(RE_LAT.findall(t)) + len(RE_CYR.findall(t)) + len(RE_CJK.findall(t))
    if letters < 4 and len(RE_CJK.findall(t)) < 2:
        return False
    # чипы статов / одиночные аббревиатуры
    if re.fullmatch(r"[A-Z]{1,3}", t) or re.fullmatch(r"\d+(/\d+)?", t):
        return False
    if re.fullmatch(r"Lv\.?\s*\d+[O0]?", t, re.I):
        return False
    return True


def tess_fill_sparse_ui(region_img: Image.Image, lines: list[dict]) -> list[dict]:
    """Tesseract eng добирает строки, которые RapidOCR пропустил на узком кропе."""
    w, h = region_img.size
    stats = ocr_coverage_stats(lines, w, h)
    # на широком кадре Tesseract = секунды впустую
    if not stats["sparse"] or w >= 900 or h >= 900:
        return lines
    try:
        scale = 2 if max(w, h) < 900 else 1
        work = region_img
        if scale > 1:
            work = region_img.resize(
                (w * scale, h * scale),
                Image.Resampling.LANCZOS,
            )
        raw = ocr_with(work, "eng", psm="6")
    except Exception as exc:
        tlog(f"tess-fill-error {type(exc).__name__}: {exc}")
        return lines
    extras: list[dict] = []
    have = [
        ((p["box"][1] + p["box"][3]) * 0.5, str(p.get("text", "")).casefold())
        for p in lines
    ]
    for p in raw:
        t = unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip()))
        if not t or is_garbage_ocr(t) or is_hud_spam(t) or is_desktop_chrome(t):
            continue
        if len(RE_LAT.findall(t)) < 6 and not looks_like_section_header(t):
            continue
        x1, y1, x2, y2 = p["box"]
        copy = dict(p)
        copy["text"] = t
        copy["box"] = (
            int(x1 / scale),
            int(y1 / scale),
            int(x2 / scale),
            int(y2 / scale),
        )
        copy["line_height"] = max(8, int(p.get("line_height", 14) / scale))
        copy["engine"] = "tess-fill"
        cy = (copy["box"][1] + copy["box"][3]) * 0.5
        key = t.casefold()
        if any(abs(cy - hy) < 14 or (key and key == hk) for hy, hk in have):
            continue
        # не дублировать уже известные куски
        if any(
            key in str(q.get("text", "")).casefold()
            or str(q.get("text", "")).casefold() in key
            for q in lines
            if len(key) >= 18
        ):
            continue
        extras.append(copy)
        have.append((cy, key))
    if extras:
        tlog(
            f"tess-fill +{len(extras)} sparse top={stats['top']} gaps={stats['big_gaps']} "
            + " || ".join(str(e.get("text", ""))[:36] for e in extras[:5])
        )
        merged = list(lines) + extras
        merged.sort(key=lambda p: (p["box"][1], p["box"][0]))
        return merged
    tlog(
        f"tess-fill-miss n={stats['n']} top={stats['top']} "
        f"gaps={stats['big_gaps']} cov={stats['coverage']:.2f}"
    )
    return lines


def annotate_lines(lines, engine: str = "rapid") -> list[dict]:
    """Fill in the OCR fields on results that came from anywhere.

    Idempotent, and never overwrites an engine that already set itself, so a
    refined block keeps saying it was Meiki that read it.
    """
    out: list[dict] = []
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        text = str(ln.get("text", ""))
        ln["engine"] = str(ln.get("engine") or engine)
        ln["conf"] = float(ln.get("conf", 0.0) or 0.0)
        ln["lang"] = str(ln.get("lang") or block_lang(text))
        ln["script"] = str(ln.get("script") or block_script(text))
        out.append(ln)
    return out


def japanese_blocks(lines) -> list[dict]:
    """The Japanese of a multilingual result, by block.

    This is the point of reading one frame with one multilingual engine: the
    Japanese is already in hand, separated from the English, and dropping it
    costs nothing but the decision to drop it.
    """
    return [
        ln
        for ln in (lines or [])
        if isinstance(ln, dict) and is_japanese_block(str(ln.get("text", "")))
    ]
