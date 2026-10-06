"""Перевод: всё, что превращает прочитанный текст в перевод.

Распознанная строка - это текст, который надо перевести; здесь он уходит в
бэкенд и возвращается строкой, которую можно нарисовать. Между этими двумя точками живёт всё, что делает перевод
переводом, а не вызовом сети:

- кэш, двухуровневый (в кадре и на диске), и он же источник повторов;
- глоссарий - короткие лейблы, имена и числа, которые нельзя отдать движку
  на откуп, потому что «PASS» это вердикт, а не «пройдите»;
- защита от собственного эха: ответ, совпавший с исходником, переводом не
  является, и такой ответ отбрасывается, а не рисуется;
- защита пунктуации: запятая и вопросительный знак принадлежат исходной
  строке, и перевод их не имеет права унести.

Модуль ничего не знает про cairo, GTK и OCR: на входе текст и его язык, на
выходе - строка. Всё, что нужно понять про экран, сделано до него.

Тело перенесено из `live.py` дословно. Первый проход этой работы был написан
по памяти, функции выглядели теми же, а `glossary_translation` молча перестал
узнавать «Bonds 14820»: потерялся один `re.fullmatch` в середине тела, и
число уезжало в движок, который читал его как облигации.
"""
from __future__ import annotations

import math
import os
import re
import time

from PIL import Image

from .. import translate as translate_mod
from ..core.text import (  # noqa: F401
    JP_REFINE_CONF,
    JP_REFINE_MIN_CHARS,
    OCR_ERROR,
    RE_HAN,
    RE_KANA,
    RE_LAT,
    SPEAKER_NAMES,
    UI_SHORT_LABELS,
    _attached_to_kanji,
    _crop_box,
    block_lang,
    engine_enabled,
    is_japanese_block,
    normalize_for_compare,
    quad_angle_deg,
    tdetail,
    tlog,
)
from ..layout.grouping import (  # noqa: F401
    is_desktop_chrome,
)
from ..ocr import engine  # noqa: F401
from ..ocr.engine import (  # noqa: F401
    MEIKI_INIT_FAILED,
    MEIKI_OCR,
    annotate_lines,
    is_garbage_ocr,
    japanese_blocks,
    normalize_japanese_text,
    stitch_rows_by_baseline,
)
from ..typography.metrics import (  # noqa: F401
    RE_JPN,
    is_latin_label,
)
from . import glossary

_GAME_GLOSSARY_APPLIED = False


TRANSLATION_DISABLED = False


def require_ocr() -> None:
    if OCR_ERROR:
        raise RuntimeError(
            f"OCR dependencies unavailable ({OCR_ERROR}). "
            "Install python-pillow and python-pytesseract."
        )
    _ensure_game_glossary_applied()


def _ensure_game_glossary_applied() -> None:
    """Fold in the opt-in game vocabulary once, the first time OCR is used.

    Done here rather than at import so the environment is read when the user
    actually runs something, and done once so the count is logged once.
    """
    global _GAME_GLOSSARY_APPLIED
    if _GAME_GLOSSARY_APPLIED:
        return
    _GAME_GLOSSARY_APPLIED = True
    added = apply_game_glossary()
    if added:
        tlog(f"glossary game-terms={added} (opt-in)")


def is_speaker_name(text: str) -> bool:
    """Короткое имя над репликой — только известные неймплейты, не фуригана."""
    t = re.sub(r"\s+", "", text.strip())
    if not t or len(t) > 6:
        return False
    if t in SPEAKER_NAMES or t in GLOSSARY:
        kana = len(re.findall(r"[\u3040-\u30ff]", t))
        kanji = len(re.findall(r"[\u4e00-\u9fff]", t))
        return kana >= 2 or (kanji >= 1 and len(t) <= 3)
    return False


def is_garbage_japanese(text: str) -> bool:
    """Фуригана/шум OCR по фону игры, не настоящий UI/диалог."""
    t = normalize_japanese_text(text)
    if not t or is_latin_label(t):
        return False
    if t in UI_SHORT_LABELS or t in GLOSSARY:
        return False
    if is_speaker_name(t):
        return False
    # заголовки caution-экранов
    if t in ("ご注意", "お願い", "未成年") or t.startswith(("ご注意", "お願い", "未成年", "TIPS")):
        return False
    compact = re.sub(r"\s+", "", t)
    jp = len(RE_JPN.findall(compact))
    kanji = len(re.findall(r"[\u4e00-\u9fff]", compact))
    kana = len(re.findall(r"[\u3040-\u30ff]", compact))
    lat = len(RE_LAT.findall(compact))
    if jp < 2:
        return True
    # MB / Wi-Fi / Lv в JP-UI — норма; режем только когда латиницы много и JP мало
    if lat >= 3 and jp < 8:
        return True
    if re.search(r"[`|>\\_^=~]", t):
        return True
    # разнесённые одиночные глифы: «浩。 ヶ ド i»
    if len(t) - len(compact) >= 2 and jp <= 10:
        return True
    prolonged = compact.count("ー") + compact.count("―") + compact.count("一")
    if prolonged >= 2 and kanji == 0 and jp <= 10:
        return True
    if prolonged >= max(3, len(compact) // 2) and kanji == 0:
        return True
    # «はいよニニーニ» и похожий катакана-спам без смысла
    if kanji == 0 and sum(compact.count(c) for c in "ニヌネノミー") >= 3 and len(compact) <= 12:
        return True
    particles = (
        "は", "が", "を", "に", "の", "です", "ます", "か", "よ", "ね", "だ", "な",
        "も", "と", "で", "から", "して", "って", "けど", "ない", "た", "て", "も",
        "へ", "ご", "お",
    )
    part_hits = sum(1 for p in particles if p in compact)
    # короткие обрывки без грамматики / UI
    if jp <= 4 and part_hits == 0 and compact not in UI_SHORT_LABELS and kanji == 0:
        return True
    if kanji == 0 and jp <= 6 and part_hits == 0:
        return True
    # Short kanji-only words are ordinary labels (第3章, 設定, 購入), not noise.
    # Background OCR garbage mixes kana into the kanji, so require that.
    if kanji >= 1 and jp <= 2 and part_hits == 0 and kana >= 1:
        return True
    # одна частица + 1–2 случайных каны (をゃき) — шум, не реплика
    if kanji == 0 and jp <= 4 and part_hits == 1 and compact not in UI_SHORT_LABELS:
        return True
    return False


def japanese_ocr_quality(lines: list[dict]) -> float:
    score = 0.0
    for p in lines:
        t = str(p.get("text", ""))
        if is_garbage_japanese(t) or is_latin_label(t):
            continue
        kanji = len(re.findall(r"[\u4e00-\u9fff]", t))
        jp = len(RE_JPN.findall(t))
        if kanji >= 1:
            score += 14 + kanji * 2 + float(p.get("conf", 0)) * 0.03
        elif is_speaker_name(t):
            score += 8
        elif jp >= 4:
            score += 6
        else:
            score += 1
    return score


def filter_plausible_lines(lines: list[dict], mode: str) -> list[dict]:
    out: list[dict] = []
    for p in lines:
        t = str(p.get("text", ""))
        if mode.startswith("jpn"):
            if is_latin_label(t) or p.get("kind") in ("chip", "latin", "menu"):
                if is_garbage_ocr(t) and t.upper().replace(" ", "") not in {"TAP!", "NEW!", "TIPS", "GACHA", "MEMBER", "STORY", "LIVE", "MYSEKAI"}:
                    continue
                out.append(p)
                continue
            if is_garbage_japanese(t):
                continue
            out.append(p)
        else:
            if is_garbage_ocr(t):
                continue
            out.append(p)
    return out


def same_line(a: str, b: str) -> bool:
    na = re.sub(r"\s+", "", a).casefold()
    nb = re.sub(r"\s+", "", b).casefold()
    return bool(na) and na == nb


def meiki_ocr_lines(region_img: Image.Image) -> list[dict]:
    """Локальный OCR для японского game UI. Tesseract остаётся только fallback."""
    if region_img.width < 120 or region_img.height < 35 or engine.MEIKI_INIT_FAILED:
        return []
    if not engine_enabled("meiki"):
        return []
    try:
        if engine.MEIKI_OCR is None:
            from meikiocr import MeikiOCR

            engine.MEIKI_OCR = MeikiOCR()
            tlog("meiki=ready")
    except Exception as exc:
        engine.MEIKI_INIT_FAILED = True
        tlog(f"meiki=unavailable {type(exc).__name__}: {exc}")
        return []

    try:
        import numpy as np
    except Exception as exc:
        tlog(f"meiki=np-unavailable {type(exc).__name__}: {exc}")
        return []

    try:
        # MeikiOCR ожидает cv2/numpy BGR-картинку.
        rgb = np.asarray(region_img.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        # 0.50 ловит реплики с длинным тире (――でさ～); 0.55+ их терял
        raw = engine.MEIKI_OCR.run_ocr(bgr, det_threshold=0.48, rec_threshold=0.22)
    except Exception as exc:
        tlog(f"meiki=error {type(exc).__name__}: {exc}")
        return []

    result: list[dict] = []
    for item in raw or []:
        text = normalize_japanese_text(str(item.get("text", "")))
        # имена вроде ミク (2 каны) и glossary — не режем; остальное минимум 2 глифа
        if len(RE_JPN.findall(text)) < 2 and text not in GLOSSARY and not is_speaker_name(text):
            continue
        box = item.get("box") or item.get("bbox") or item.get("points")
        chars = item.get("chars") or []
        if not box and chars:
            char_boxes = [ch.get("bbox") for ch in chars if ch.get("bbox")]
            if char_boxes:
                xs = [float(v) for b in char_boxes for v in (b[0], b[2])]
                ys = [float(v) for b in char_boxes for v in (b[1], b[3])]
                box = [min(xs), min(ys), max(xs), max(ys)]
        if not box:
            continue
        try:
            if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
                x1, y1, x2, y2 = box
            else:
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        except Exception:
            continue
        if chars:
            char_confs = [float(ch.get("conf", 0)) for ch in chars if ch.get("conf") is not None]
            conf = (sum(char_confs) / len(char_confs)) if char_confs else 0.8
        else:
            conf = float(item.get("score", item.get("confidence", item.get("conf", 80))) or 80)
        if 0 < conf <= 1:
            conf *= 100
        # Короткие имена (ミク) — мягче; фуригана/шум отсекаем позже.
        speaker = is_speaker_name(text) or text in GLOSSARY
        if len(text) <= 2:
            if conf < (40 if speaker else 52):
                continue
        elif len(text) <= 4:
            if conf < (48 if speaker else 58):
                continue
        elif conf < 48:
            continue
        if is_garbage_japanese(text) and not speaker:
            continue
        h = max(8, int(y2 - y1))
        angle = 0.0
        try:
            if len(box) >= 4 and not all(isinstance(v, (int, float)) for v in box[:4]) or isinstance(box, (list, tuple)) and len(box) >= 4 and hasattr(box[0], "__len__"):
                angle = quad_angle_deg(box)
        except Exception:
            angle = 0.0
        result.append(
            {
                "text": text,
                "box": (int(x1), int(y1), int(x2), int(y2)),
                "line_height": h,
                "conf": conf,
                "engine": "meiki",
                "kind": "name" if is_speaker_name(text) else None,
                "angle": float(angle),
            }
        )
    cleaned = stitch_rows_by_baseline(result)
    cleaned = [p for p in cleaned if not is_garbage_japanese(p["text"]) or is_speaker_name(p["text"])]
    cleaned = drop_furigana_lines(cleaned)
    return cleaned


def is_furigana_reading(text: str) -> bool:
    """Чистая фуригана/чтение без кандзи — не клеить в тело и не переводить."""
    t = re.sub(r"\s+", "", text.strip())
    if not t or is_speaker_name(t) or t in GLOSSARY:
        return False
    kanji = re.findall(r"[\u4e00-\u9fff]", t)
    if kanji:
        return False
    kana = re.findall(r"[\u3040-\u30ff]", t)
    jp = RE_JPN.findall(t)
    if not kana or len(kana) < max(1, int(len(jp) * 0.85)):
        return False
    # грамматика → реальная фраза (не подстроки вроде よう внутри りよう)
    if any(x in t for x in ("です", "ます", "でした", "ました", "して", "から", "ので", "けど", "ください", "ません")):
        return False
    # спам повторов: りようほうほうりようほうほう…
    if re.search(r"([\u3040-\u30ff]{3,10})\1+", t):
        return True
    # A short kana word is a normal label (ストーリー, キャンセル, ライブ), not
    # furigana. Ruby is recognised by its size and its position next to kanji,
    # which drop_furigana_lines checks; a bare length test deleted real UI text.
    return False


def drop_furigana_lines(lines: list[dict]) -> list[dict]:
    """Мелкие kana-only строки над кандзи (фуригана) — не UI и не реплики."""
    if len(lines) < 4:
        return lines
    heights = sorted(max(1, int(p.get("line_height", 1))) for p in lines)
    max_h = heights[-1]
    body = heights[max(0, int(len(heights) * 0.55)) :]
    body_med = body[len(body) // 2] if body else max_h
    thr = max(12, int(min(body_med, max_h) * 0.45))
    out = []
    dropped = 0
    for p in lines:
        t = str(p.get("text", "")).strip()
        h = int(p.get("line_height", 0))
        if is_speaker_name(t) or t in GLOSSARY:
            out.append(p)
            continue
        if is_furigana_reading(t) and h <= thr and _attached_to_kanji(p, lines):
            dropped += 1
            continue
        out.append(p)
    if dropped:
        tlog(f"furigana-drop {dropped} thr={thr} body_h={body_med}")
    return out or lines


def parse_marked_translation(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    matches = list(re.finditer(r"@@KZT(\d+)@@", text))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            found[int(match.group(1))] = body
    return found


def is_translation_error(src: str, out: str) -> bool:
    """A failed or unusable response. Japanese output is NOT an error.

    Delegates to the full check in translate.py which includes:
    - explicit error messages
    - gibberish detection
    - half-translated line detection (leaves_source_untranslated)
    - placeholder detection
    - consonant crowding
    """
    return translate_mod.is_error_response(src, out)


#: Словари грузятся из `data/glossary/<код>.toml`. Русский лежит там целиком,
#: остальные языки добавляются файлом; см. `translation/glossary.py`.
GLOSSARY: dict[str, str] = glossary.section_for(
    glossary.DEFAULT_LANG, "ui")

#: Игровые термины из своей секции того же файла: лейблы интерфейса и
#: названия игр выглядят одинаково, но приходят из разных источников.
GAME_GLOSSARY: dict[str, str] = glossary.section_for(
    glossary.DEFAULT_LANG, "game")


def apply_game_glossary(enabled: bool | None = None) -> int:
    """Fold the game vocabulary into the default glossary, if asked for.

    Returns how many terms were added, so a caller can log it rather than
    leaving the user to wonder why their screen is full of caps.
    """
    if enabled is None:
        enabled = (os.environ.get("KIZURIUM_TRANSLATOR_GAME_GLOSSARY", "") or "").strip() not in ("", "0", "no", "false")
    # The two halves of the same decision: what to call a word, and which names
    # are not ordinary vocabulary at all. One switch, because a user who wants a
    # game's terms held fixed wants its character names held fixed too.
    translate_mod.enable_title_glossary(enabled)
    if not enabled:
        return 0
    added = 0
    for term, value in GAME_GLOSSARY.items():
        # A user entry is a deliberate choice and must not be overwritten.
        if term not in GLOSSARY:
            GLOSSARY[term] = value
            added += 1
    return added


def _active_profile_pack() -> str | None:
    """Lexicon pack from the active profile, if any"""
    try:
        from ..profile import active_lexicon_pack

        return active_lexicon_pack()
    except Exception:  # noqa: BLE001
        return None


def glossary_translation(text: str, target_lang: str | None = None) -> str | None:
    """Короткие кнопки и имена. Не подменяем целые реплики и абзацы.

    `target_lang` обязателен по смыслу: словарь отвечает на том же языке, на
    который переводим. Без этого параметра русский словарь выдавался при любом
    языке назначения, и `TAP!` на японском экране рисовался как `Нажми!` -
    выглядит как «перевод сломан», хотя сломан был только словарь.

    Формы, где движок ломает число, общие для всех языков: `12/24` движок
    читает как дату, `Lv. 12` как имя. Слово внутри формы берётся по языку.
    """
    t = normalize_japanese_text(text)
    lang = (target_lang or "").strip().lower() or glossary.DEFAULT_LANG
    from . import user_glossary

    user_hit = user_glossary.exact(t)
    if user_hit:
        return user_hit
    from .. import translate as translate_mod
    from ..lexicon.store import exact as lexicon_exact

    data_hit = lexicon_exact(
        t,
        game_on=bool(translate_mod._TITLE_TRUTHY_ON),
        game=_active_profile_pack(),
    )
    if data_hit:
        return data_hit
    from ..lexicon.regex_rules import apply_regex

    regex_hit = apply_regex(
        t,
        game_on=bool(translate_mod._TITLE_TRUTHY_ON),
        game=_active_profile_pack(),
    )
    if regex_hit:
        return regex_hit
    table = glossary.entries_for(lang)
    level = glossary.level_word(lang)

    if not table:
        # Словаря для этого языка нет: остаётся только то, что не переводится.
        return _numeric_guard(t, lang, level)
    if t.casefold() == "tips":
        return table.get("TIPS")
    if m:= re.fullmatch(r"version\s*(\d+(\.\d+)?)", t, re.I):
        head = _lookup(table, "version", prefix=True)
        return f"{head} {m.group(1)}" if head else None
    hit = table.get(t)
    if hit is not None:
        return hit
    if len(t) > 42:
        return None
    tu = t.upper().replace(" ", "")
    for k, v in table.items():
        if len(k) <= 28 and k.upper().replace(" ", "") == tu:
            return v
    if m:= re.fullmatch(r"ランク\s*(\d+)", t):
        rank = table.get("Rank") or table.get("RANK")
        return f"{rank} {m.group(1)}" if rank else None

    # "<термин> <число>" сохраняет число и берёт слово из словаря, иначе
    # "Bonds 14820" может вернуться как имя, а "CP 25/25" брать разное слово
    # каждый цикл.
    if m:= re.fullmatch(r"(.+?)\s+(\d[\d.,/:]*\s*(?:[A-Za-z%]{1,3})?)", t):
        known = glossary_translation(m.group(1).strip(), target_lang=lang)
        if known:
            return f"{known} {m.group(2)}"
    m = re.fullmatch(r"(.+?)\s+(\d+)\s*[-\u2013]\s*(\d+)", t)
    if m:
        known = glossary_translation(t[: m.start(2)].strip(), target_lang=lang)
        if known:
            return f"{known} {m.group(2)}-{m.group(3)}"

    return _numeric_guard(t, lang, level)


def _lookup(table: dict[str, str], word: str, *, prefix: bool = False) -> str:
    """Ищет слово в словаре без учёта регистра, по префиксу - с пробелом."""
    for k, v in table.items():
        if (k.lower().startswith(word) if prefix else k.lower() == word):
            return v
    return ""


def _numeric_guard(t: str, lang: str, level: str) -> str | None:
    """Формы, где движок превращает число во что-то другое.

    Русскому нужны согласования, поэтому общего правила «<слово> <состояние>»
    тут нет: из него получалось «Глава Пройдено». Реальные сочетания живут в
    словаре языка, а эти формы - про числа, и они одинаковы везде.
    """
    if m:= re.fullmatch(r"(\d+)\s*/\s*(\d+)", t):
        return f"{m.group(1)}/{m.group(2)}"
    if m:= re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*%", t):
        return f"{m.group(1)}%"
    if m:= re.fullmatch(r"[Ss]tamina\s+(\d+)\s*/\s*(\d+)", t):
        return f"{t.split()[0]} {m.group(1)}/{m.group(2)}"
    if m:= re.fullmatch(r"(?:\d+)\s*[/x]\s*(\d+)", t):
        return t.strip()
    if m:= re.fullmatch(r"[Ll][Vv]\.?\s*(\d+)\s*(?:[-\u2192>]|to)\s*[Ll][Vv]\.?\s*(\d+)", t, re.I):
        return f"{level} {m.group(1)} \u2192 {level.lower()} {m.group(2)}"
    if m:= re.fullmatch(r"[Ll][Vv]\.?\s*(\d+)", t):
        return f"{level} {m.group(1)}"
    if "/" in t:
        m = re.fullmatch(r"(.+?)\s+(\d+)\s*/\s*(\d+)", t)
        if m:
            head = glossary_translation(m.group(1).strip(), target_lang=lang)
            # Слово остаётся как есть, если словаря для этого языка нет.
            # Защищаем число, а не переводим: защита дроби не должна зависеть
            # от того, нашлось ли слово в словаре, иначе при `target=en`
            # "Cleared 12/24" уходило в движок целиком и возвращалось датой.
            return f"{head or m.group(1).strip()} {m.group(2)}/{m.group(3)}"
    return None


def drop_bad_ocr_boxes(lines: list[dict], rw: int, rh: int) -> list[dict]:
    """Режет мусор-bbox (полоска на весь экран), не нормальные тосты.

    Ширина сравнивается абсолютно, а не в процентах от кропа: у тоста,
    вырезанного точно по тексту, текст занимает почти всю ширину, и
    относительный критерий отбрасывал бы нормальные строки. Поэтому
    смотрим на абсолютную ширину вместе с плотностью символов.

    Наклонная строка измеряется по своему осевому прямоугольнику, и длина её
    диагонали попадает в обе стороны сразу: строка под 17 градусами в боксе
    вдвое выше и вдвое шире, чем она есть. Плотность в расчёте на такой бокс
    вдвое ниже настоящей, и «ACCURACY 47.01%» выглядит как разрежённая полоса
    на всю ширину - её ширину срезали вдвое, и карточка перестала покрывать
    оригинал. Меряем по длине строки, а не по прямоугольнику вокруг неё.
    """
    out: list[dict] = []
    for p in lines:
        t = str(p.get("text", "")).strip()
        if not t:
            continue
        if is_desktop_chrome(t):
            continue
        x1, y1, x2, y2 = p["box"]
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        chars = max(1, len(re.sub(r"\s+", "", t)))
        skew = float(p.get("angle", 0.0) or 0.0)
        if abs(skew) >= 1.0:
            rad = math.radians(skew)
            cos_a, sin_a = abs(math.cos(rad)), abs(math.sin(rad))
            det = cos_a * cos_a - sin_a * sin_a
            if abs(det) > 1e-3:
                line_w = (bw * cos_a - bh * sin_a) / det
                if 8 <= line_w <= bw * 1.05:
                    bw = int(round(line_w))
        px_per = bw / chars
        # A wide line is not a sparse one: type is as wide as it is tall, so the
        # number of pixels a character is allowed to occupy has to follow the
        # size of the line it is on. Judged on its own, a 45px headline reads as
        # three times sparser than a 15px caption, and every rule below trims
        # it - the box came back half the width of the text and the card stopped
        # covering it. The allowance scales with the line's own height.
        line_h = bh
        if abs(skew) >= 1.0:
            rad2 = math.radians(skew)
            cos2, sin2 = abs(math.cos(rad2)), abs(math.sin(rad2))
            det2 = cos2 * cos2 - sin2 * sin2
            if abs(det2) > 1e-3:
                cand = (bh * cos2 - bw * sin2) / det2
                if 6 <= cand <= bh:
                    line_h = int(round(cand))
        px_budget = max(12, line_h * 1.45)
        # одна-две буквы / обрывок не может занимать огромный bbox
        if chars <= 2 and not is_speaker_name(t) and (bw > max(80, rw * 0.12) or bh > max(48, rh * 0.08)):
            continue
        if chars <= 4 and bw * bh > (rw * rh) * 0.12 and not is_speaker_name(t):
            continue
        # гигантская полоса с редким текстом — подрежем bbox, не убиваем перевод
        # пороги — доли кадра, не абсолютные 900/700 (FHD / HiDPI)
        from ..core.scale import px_per_char

        wide_gate = max(480, int(rw * 0.47))
        mid_gate = max(360, int(rw * 0.36))
        ppc = px_per_char(line_h)
        if bw >= wide_gate and px_per > px_budget:
            cx = (x1 + x2) / 2
            new_w = max(80, min(bw, int(chars * ppc + 48)))
            p = dict(p)
            p["box"] = (int(cx - new_w / 2), y1, int(cx + new_w / 2), y2)
            tdetail(f"shrink-wide-box '{t[:28]}' w={bw}->{new_w} ppc={px_per:.1f}")
            out.append(p)
            continue
        if bw >= mid_gate and chars <= 20 and px_per > max(40, px_budget):
            cx = (x1 + x2) / 2
            new_w = max(64, min(bw, int(chars * max(ppc, 10) + 40)))
            p = dict(p)
            p["box"] = (int(cx - new_w / 2), y1, int(cx + new_w / 2), y2)
            tdetail(f"shrink-wide-short '{t[:28]}' w={bw}->{new_w}")
            out.append(p)
            continue
        # слишком широкий bbox относительно длины — подрежем карточку, не дропаем
        if chars <= 28 and bw > max(220, int(chars * max(22, px_budget) + 48)):
            cx = (x1 + x2) / 2
            new_w = max(56, min(bw, int(chars * max(ppc, 10) + 40)))
            p = dict(p)
            p["box"] = (int(cx - new_w / 2), y1, int(cx + new_w / 2), y2)
        out.append(p)
    return out


def refine_japanese_blocks(
    region_img,
    lines: list[dict],
    now: float,
) -> tuple[list[dict], int]:
    """Re-read doubtful Japanese elements with Meiki, on their crops only."""
    if not engine_enabled("meiki"):
        return lines, 0

    targets: list[dict] = []
    for ln in lines:
        text = str(ln.get("text", ""))
        if not text:
            continue
        if not is_japanese_block(text):
            continue
        kana = len(RE_KANA.findall(text))
        conf = float(ln.get("conf", 0.0) or 0.0)
        doubtful = (
            conf < JP_REFINE_CONF
            or kana == 0
            or len(RE_HAN.findall(text)) > len(RE_KANA.findall(text)) * 2
        )
        if doubtful and len(RE_JPN.findall(text)) >= JP_REFINE_MIN_CHARS:
            targets.append(ln)

    if not targets:
        return lines, 0

    refined = 0
    for ln in targets:
        box = tuple(int(v) for v in ln.get("box", (0, 0, 0, 0)))  # type: ignore[assignment]
        crop = _crop_box(region_img, box, pad=4)  # type: ignore[arg-type]
        if crop.width < 24 or crop.height < 12:
            continue
        try:
            got = meiki_ocr_lines(crop)
        except Exception:  # noqa: BLE001
            continue
        got = [g for g in got if str(g.get("text", "")).strip()]
        if not got:
            continue
        got.sort(key=lambda g: (g["box"][1], g["box"][0]))
        text = " ".join(str(g.get("text", "")).strip() for g in got)
        if not text or normalize_for_compare(text) == normalize_for_compare(
            str(ln.get("text", ""))
        ):
            continue
        # Meiki is trained on Japanese game text: trust it for kana/kanji only.
        if RE_JPN.search(text) and is_japanese_block(text):
            ln["text"] = text
            ln["engine"] = "meiki"
            ln["conf"] = max(float(ln.get("conf", 0.0) or 0.0), 85.0)
            refined += 1
    if refined:
        tlog(f"meiki-refine {refined}/{len(targets)} jp-blocks")
    return lines, refined


def finish_mixed_frame(
    lines: list[dict],
    raw: list[dict],
    region_img,
    kind: str,
    cap: int = 80,
) -> tuple[list[dict], str]:
    """The one place a frame leaves ocr_image, so no path decides the language.

    Every English-UI path below is built to return early: read the band, score
    it, and if it looks like a menu, stop. That is the right call for cost and
    the wrong one for a screen that is English on the left and Japanese on the
    right - the Japanese was already read by the same pass, at no extra cost,
    and returning without it discarded a result that was sitting in the hand.

    So the Japanese blocks of that same pass come through here, get the crop
    refinement a doubtful block deserves, and join the frame with their own
    language attached. What gets translated is then decided per block, which is
    the only level at which it can be decided correctly.
    """
    lines = annotate_lines(lines, "rapid")
    seen = {normalize_for_compare(str(p.get("text", ""))) for p in lines}
    extra: list[dict] = []
    for ln in japanese_blocks(raw):
        text = str(ln.get("text", "")).strip()
        if not text:
            continue
        key = normalize_for_compare(text)
        if not key or key in seen:
            continue
        seen.add(key)
        copy = dict(ln)
        copy["lang"] = block_lang(text)
        extra.append(copy)

    if extra:
        if region_img is not None:
            try:
                extra, _n = refine_japanese_blocks(region_img, extra, time.monotonic())
            except Exception as exc:  # noqa: BLE001
                tlog(f"jp-refine-error {type(exc).__name__}: {exc}")
        extra = [p for p in extra if str(p.get("text", "")).strip()]
        if extra:
            extra.sort(key=lambda p: (p["box"][1], p["box"][0]))
            lines = annotate_lines(lines + extra, "rapid")
            tlog(
                f"mixed-frame +{len(extra)} jp langs="
                + ",".join(sorted({str(p.get("lang", "")) for p in lines}))
            )
    return lines[:cap], kind
