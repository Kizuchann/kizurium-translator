"""Сборка карточек из результатов распознавания.

То, что пришло из разных проходов OCR, надо свести в один
набор: склеить пересекающиеся дубли, развести то, что
разъехалось по базовой линии, и не дать старой карточке
пережить смену сцены.
"""
from __future__ import annotations

import re
import time

from PIL import Image, ImageStat
from pytesseract import pytesseract

from ..core.scripts import script_of, target_language  # noqa: F401
from ..core.text import (  # noqa: F401
    RE_KANA,
    SCRIPT_EN,
    SCRIPT_TARGET,
    SCRIPT_UNKNOWN,
    _core_read_beats,
    _crop_box,
    _english_word_set,
    _iou,
    _reading_quality,
    block_script,
    engine_enabled,
    looks_like_spoken_line,
    pack_translation_to_rows,
    quad_angle_deg,
    quad_depth,
    tdetail,
    tlog,
)
from ..layout.grouping import (  # noqa: F401
    split_leading_name,
    stitch_paragraph_lines,
)
from ..ocr import engine
from ..ocr.engine import (  # noqa: F401
    _OCR_LOCK,
    CORE_REREAD_BELOW,
    DET_SIDE_LEN,
    OCR_THREADS,
    _rapid_rows,
    is_garbage_ocr,
    is_overlay_echo_ocr,
    ocr_with,
    stitch_rows_by_baseline,
)
from ..translate import (  # noqa: F401
    RE_HAN,
    normalize_for_compare,
)
from ..typography.metrics import (  # noqa: F401
    reread_core_ink,
    ui_text_fingerprint,
)
from .change import (  # noqa: F401
    DIRTY_PAD,
)
from .runtime import (  # noqa: F401
    translator,
)
from .state import (  # noqa: F401
    DIGITS_RE,
    LATIN_TOKEN_RE,
    SHORT_TOKEN_MAX,
    TrackedBlock,
)

OCR_LANGS = "eng+rus"


TESSERACT_JAPANESE = "jpn+eng"


TESSERACT_MULTI = "jpn+eng+rus"


OCR_FALLBACKS = ("eng+rus", "jpn+eng", "chi_sim+eng", "chi_tra+eng", "kor+eng")


TESSERACT_CYRILLIC = "rus+eng"


def rapid_ocr_lines_cyrillic(region_img: Image.Image) -> list[dict]:
    """Прочитать кадр кириллическим набором tesseract.

    Почему не вторая модель RapidOCR, хотя она есть и читает кириллицу
    идеально: в процессе, где загружен GTK, ONNX-модели кириллицы молча
    возвращают пустоту. Замерено на одном и том же кропе `Reverso` - 39 шагов
    из 40 с уверенностью выше 0.9 без GTK и 0 из 40 после его загрузки, при
    одинаковом алфавите из 850 символов. Модель `ch` при этом работает, так
    что дело не в ONNX Runtime и не в числе потоков, а в конкретных моделях
    `cyrillic`/`eslav` (PP-OCRv4 и PP-OCRv5, mobile и server - все). У
    tesseract своя исполняемая часть, конфликта с GTK нет, и `rus` в системе
    уже стоит.

    Возвращает только строки, которые таблица почерков опознала как
    кириллические. Иначе tesseract на игровом кадре возвращает 56 строк
    мусора: `|`, `-`, `1`, `„°`.
    """
    if region_img.width < 30 or region_img.height < 12:
        return []
    if not engine_enabled("tesseract"):
        return []
    try:
        read = ocr_with(region_img, TESSERACT_CYRILLIC, psm="6")
    except Exception as exc:  # noqa: BLE001
        tlog(f"cyrillic-ocr=error {type(exc).__name__}: {exc}")
        return []
    return [
        p
        for p in read
        if block_script(str(p.get("text", ""))) == SCRIPT_TARGET
    ]


CYRILLIC_PROBE_COOLDOWN_S = 6.0

_CYRILLIC_PROBED_AT: float = 0.0


def reset_cyrillic_probe() -> None:
    """Забыть, что зонд уже срабатывал. Вызывается при смене сцены."""
    global _CYRILLIC_PROBED_AT
    _CYRILLIC_PROBED_AT = 0.0


def cyrillic_probe_due(now: float | None = None) -> bool:
    """Пора ли снова спросить кириллицу.

    Пауза нужна, чтобы пустой кадр в игре (смаз, тёмная сцена) не удлинял
    цикл на две секунды постоянно. Но и навсегда зонд не выключается: экран
    мог смениться, и тогда русский текст уже есть.
    """
    global _CYRILLIC_PROBED_AT
    at = time.monotonic() if now is None else now
    if _CYRILLIC_PROBED_AT and at - _CYRILLIC_PROBED_AT < CYRILLIC_PROBE_COOLDOWN_S:
        return False
    _CYRILLIC_PROBED_AT = at
    return True


def rapid_ocr_lines(region_img: Image.Image, max_side: int = 1280) -> list[dict]:
    """Локальный быстрый OCR для английского и общего UI через RapidOCR (ONNX).

    max_side: даунскейл огромных кадров. Для ink-band (уже узкий апскейл)
    передавай max_side=0 / очень большой — иначе 4672→1280 убивает 2-ю строку пузыря.
    """
    if region_img.width < 30 or region_img.height < 12 or engine.RAPID_INIT_FAILED:
        return []
    if not engine_enabled("rapid"):
        return []
    try:
        if engine.RAPID_OCR is None:
            import os as _os

            # ONNX по умолчанию занимает все ядра — 98% CPU и 80°C на ноуте
            _os.environ.setdefault("OMP_NUM_THREADS", "2")
            _os.environ.setdefault("ORT_NUM_THREADS", "2")
            _os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
            from rapidocr import RapidOCR

            # ONNX Runtime defaults to one intra-op thread per core and keeps
            # them spinning while idle: on a many-core laptop the overlay burned
            # 600% CPU doing nothing. Two threads is enough for a screen region
            # and drops the idle cost to noise.
            engine.RAPID_OCR = RapidOCR(
                params={
                    "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "Global.log_level": "error",
                    # The stock detector (limit_side_len 736, limit_type min)
                    # upscales a wide region before detection: measured 957 ms
                    # against 432 ms for a 640 px long side, with fewer lines
                    # missed rather than more.
                    "Det.limit_side_len": DET_SIDE_LEN,
                    "Det.limit_type": "max",
                }
            )
            tlog(f"rapidocr=ready threads={OCR_THREADS}")
    except Exception as exc:
        engine.RAPID_INIT_FAILED = True
        tlog(f"rapidocr=unavailable {type(exc).__name__}: {exc}")
        return []

    try:
        import numpy as np

        work = region_img
        scale = 1.0
        # даунскейл огромных кадров — меньше CPU, почти та же точность на субтитрах
        if max_side and max(work.size) > max_side:
            scale = max_side / float(max(work.size))
            work = work.resize(
                (max(1, int(work.width * scale)), max(1, int(work.height * scale))),
                Image.Resampling.BILINEAR,
            )
        rgb = np.asarray(work.convert("RGB"))
        # One recognition at a time. The engine already limits its own thread
        # count, so running two recognitions at once does not make either one
        # finish sooner - it just doubles the CPU the cycle is already over
        # budget on, which is where the worst-case 235% came from. Queueing
        # costs latency; overrunning the frame costs the whole machine.
        with _OCR_LOCK:
            raw = _rapid_rows(engine.RAPID_OCR(rgb))
    except Exception as exc:
        tlog(f"rapidocr=error {type(exc).__name__}: {exc}")
        return []

    result: list[dict] = []
    for item in raw or []:
        if not item or len(item) < 3:
            continue
        pts, text, conf_raw = item[0], item[1], item[2]
        text = str(text).strip()
        if not text:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        h = max(1, y2 - y1)
        angle = quad_angle_deg(pts)
        try:
            conf = float(conf_raw) * 100 if float(conf_raw) <= 1.0 else float(conf_raw)
        except Exception:
            conf = 80.0

        # Мелкий UI/игровые подписи часто идут 45-50, а дальше всё равно чистится
        # garbage-фильтрами. 50 слишком часто выкидывал настоящий текст.
        if conf < 44:
            continue
        # Где распознаватель догадывается, стоит посмотреть на строку ещё раз -
        # иначе догадка уходит в перевод (см. reread_core_ink).
        if conf < CORE_REREAD_BELOW:
            box_px = (
                int(x1 / scale),
                int(y1 / scale),
                int(x2 / scale),
                int(y2 / scale),
            )
            again = reread_core_ink(region_img, box_px, engine.RAPID_OCR)
            # Один бокс на входе - один на выходе: если второй проход увидел в
            # нём несколько строк, это уже другая находка, а не другое чтение
            # той же строки, и подменять ею один элемент нельзя.
            if len(again) == 1 and _core_read_beats([(text, conf)], again):
                text, conf = again[0]
        if is_garbage_ocr(text):
            continue

        result.append(
            {
                "text": text,
                "box": (
                    int(x1 / scale),
                    int(y1 / scale),
                    int(x2 / scale),
                    int(y2 / scale),
                ),
                "line_height": max(8, int(h / scale)),
                "conf": conf,
                "engine": "rapidocr",
                "angle": float(angle),
                "band": quad_depth(pts),
            }
        )
    stitched = stitch_rows_by_baseline(stitch_paragraph_lines(result))
    split: list[dict] = []
    for par in stitched:
        split.extend(split_leading_name(region_img, par))
    return merge_overlapping_duplicates(split)


def card_center(block: dict) -> tuple[float, float]:
    w = float(block.get("src_w", block.get("w", 40)))
    h = float(block.get("src_h", block.get("h", 16)))
    return float(block["x"]) + w * 0.5, float(block["y"]) + h * 0.5


def filter_echo_of_overlay_cards(lines: list[dict], blocks_now: list[dict]) -> list[dict]:
    """Не принимать OCR своих RU-плашек (vayputs / Nepe Bo…) как новый EN UI.

    Важно: НЕ дропать настоящий EN/JP (Start/Quit/…), даже если он совпал
    с source уже показанной карточки — иначе меню переводится по одному пункту.
    """
    if not lines or not blocks_now:
        return lines
    out = []
    for p in lines:
        t = str(p.get("text", "")).strip()
        if not t:
            continue
        # только каша от кириллицы / эхо плашки — не живой UI
        if is_overlay_echo_ocr(t):
            tdetail(f"drop-overlay-echo '{t[:36]}'")
            continue
        out.append(p)
    return out


def _seen_before(text: str) -> bool:
    """Whether this exact reading was already translated in a previous cycle.

    A reading that has a cached translation has been on screen before and was
    translated, so it is a better bet than a structurally equal newcomer.
    """
    try:
        tr = translator()
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(tr.cache_lookup(text.strip(), "auto"))
    except Exception:  # noqa: BLE001
        return False


def merge_overlapping_duplicates(lines: list[dict]) -> list[dict]:
    """One line, one block: collapse boxes that cover the same spot.

    The detector sometimes returns a line twice with slightly different text
    ("Chapter 3" / "Chanter 3"). Both became blocks, so the screen showed two
    cards on top of each other and the wrong reading could win.

    Order of preference: confidence, then a structural quality score, then
    whether the reading was already translated before.
    """
    if len(lines) < 2:
        return list(lines)
    ordered = sorted(
        lines,
        key=lambda p: (
            -float(p.get("conf", 0) or 0),
            tuple(-v for v in _reading_quality(str(p.get("text", "")))),
        ),
    )
    kept: list[dict] = []

    def rank(line: dict) -> tuple:
        text = str(line.get("text", "")).strip()
        return (float(line.get("conf", 0) or 0), _reading_quality(text), _seen_before(text))

    for line in ordered:
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        box = tuple(int(v) for v in line.get("box", (0, 0, 0, 0)))
        for index, other in enumerate(kept):
            if _iou(box, tuple(int(v) for v in other["box"])) < 0.55:
                continue
            if rank(line) > rank(other):
                kept[index] = line
            break
        else:
            kept.append(line)
    kept.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return kept


def pack_translation_to_line_boxes(
    line_boxes: list[dict],
    ru: str,
    en_texts: list[str] | None = None,
) -> list[str]:
    """Раскладка RU на N OCR-строк.

    Важно: нельзя жадно заполнять 1-ю по ширине — RU короче EN-ширины 1-й строки
    и весь текст уезжает вверх, 2-я линия EN остаётся голой.
    Делим по доле длины EN (или ширины боксов), на каждую строку ≥1 слово.

    Доля считается общим кодом (core.text.pack_translation_to_rows): раскладка
    панели и раскладка диалога — одна и та же задача, и две копии правила
    разъезжаются, как только одна из них подправлена.
    """
    n = len(line_boxes)
    ru = re.sub(r"\s+", " ", (ru or "").strip())
    if n <= 0:
        return [ru] if ru else [""]
    if n == 1:
        return [ru]
    if en_texts and len(en_texts) == n:
        rows = [
            {"text": e, "box": lb["box"]} for lb, e in zip(line_boxes, en_texts)
        ]
    else:
        rows = list(line_boxes)
    out = pack_translation_to_rows(rows, ru)
    tlog(
        f"sub-pack n={n} "
        + " || ".join(f"{len(s.split())}w" for s in out)
    )
    return out


def merge_incremental_cards(
    old_blocks: list[dict],
    new_blocks: list[dict],
    new_source_fps: set[str] | None = None,
) -> list[dict]:
    """Инкрементально: новые/изменённые заменить, стабильный HUD оставить, пропавшее убрать.

    acceptance contract:

    ```text
    A = stable HUD, B = dynamic dialogue
    when B changes → A: no OCR / no retranslate / no disappearance / no rebuild
                   → B: reprocess only B
    ```

    Не делает full wipe — атомарный список для state.set без clear→пусто.
    """
    if not old_blocks:
        return list(new_blocks or [])
    if not new_blocks:
        return []

    def fp_of(b: dict) -> str:
        return ui_text_fingerprint(str(b.get("source") or b.get("text") or ""))

    result: list[dict] = []
    used_old: set[int] = set()

    for nb in new_blocks:
        nfp = fp_of(nb)
        ncx, ncy = card_center(nb)
        nw = float(nb.get("src_w", 40))
        nh = float(nb.get("src_h", 16))
        reused = None
        for i, old in enumerate(old_blocks):
            if i in used_old:
                continue
            ofp = fp_of(old)
            ocx, ocy = card_center(old)
            ow = float(old.get("src_w", 40))
            oh = float(old.get("src_h", 16))
            near = abs(ocx - ncx) <= max(ow, nw) * 0.7 and abs(ocy - ncy) <= max(oh, nh, 20) * 1.1
            same_src = bool(nfp and ofp and nfp == ofp)
            same_ru = str(old.get("text", "")) == str(nb.get("text", ""))
            if near and (same_src or same_ru):
                # тот же источник/перевод — переиспользуем старую карточку (без визуального дёрганья)
                reused = dict(old)
                reused.update(
                    {
                        "x": nb.get("x", old.get("x")),
                        "y": nb.get("y", old.get("y")),
                        "src_w": nb.get("src_w", old.get("src_w")),
                        "src_h": nb.get("src_h", old.get("src_h")),
                        "source": nb.get("source", old.get("source")),
                        "text": nb.get("text", old.get("text")),
                        "kind": nb.get("kind", old.get("kind")),
                        "bg": nb.get("bg", old.get("bg")),
                        "fg": nb.get("fg", old.get("fg")),
                        "font": nb.get("font", old.get("font")),
                    }
                )
                used_old.add(i)
                break
            if near and same_src and not same_ru:
                # тот же source, новый перевод
                reused = nb
                used_old.add(i)
                break
        result.append(reused if reused is not None else nb)

    # OCR мог пропустить стабильный HUD — оставить, если source ещё в свежем ключе
    if new_source_fps is not None:
        have = {fp_of(b) for b in result if fp_of(b)}
        for i, old in enumerate(old_blocks):
            if i in used_old or not is_stable_hud_card(old):
                continue
            ofp = fp_of(old)
            if ofp and ofp in new_source_fps and ofp not in have:
                result.append(old)
                have.add(ofp)
                tdetail(f"incr-keep-hud '{str(old.get('source') or old.get('text') or '')[:36]}'")

    result.sort(key=lambda b: (b.get("y", 0), b.get("x", 0)))
    return result


def is_stable_hud_card(b: dict) -> bool:
    """labels/menus stay put when dialogue elsewhere changes."""
    kind = str(b.get("kind", "") or "")
    if kind in ("dialogue", "dialogue-line", "body"):
        return False
    src = str(b.get("source") or "")
    if looks_like_spoken_line(src):
        return False
    return True


def _reading_key(line: dict) -> tuple:
    """Rank an OCR candidate: confidence, then how plausible the reading is."""
    text = str(line.get("text", "")).strip()
    return (float(line.get("conf", 0) or 0), _reading_quality(text), _seen_before(text))


NUMERIC_LOOKING = re.compile(r"^[Il1|]+$")


def reocr_crop_rapid(region_img, box: tuple[int, int, int, int]) -> tuple[str, float]:
    """RapidOCR on a single element. Returns (text, confidence)."""
    crop = _crop_box(region_img, box)
    if crop.width < 8 or crop.height < 8:
        return "", 0.0
    try:
        from ..ocr.preprocess import prepare_for_ocr, scale_boxes_down

        work, scale = prepare_for_ocr(crop)
        rows = rapid_ocr_lines(work, max_side=0)
        rows = scale_boxes_down(rows, scale)
    except Exception:  # noqa: BLE001
        return "", 0.0
    if not rows:
        return "", 0.0
    rows.sort(key=lambda r: (r["box"][1], r["box"][0]))
    text = " ".join(str(r.get("text", "")).strip() for r in rows).strip()
    conf = min(float(r.get("conf", 0.0) or 0.0) for r in rows)
    return text, conf


def reocr_crop_tesseract(
    region_img,
    box: tuple[int, int, int, int],
    psm: int | None = 7,
    script: str = "",
) -> str:
    """Tesseract on a single element.

    ``psm=None`` picks adaptive geometryCallers that vote on short
    crops still pass 7 (line) / 8 (word) explicitly.
    """
    if pytesseract is None:
        return ""
    crop = _crop_box(region_img, box, pad=4)
    if crop.width < 8 or crop.height < 8:
        return ""
    try:
        from PIL import ImageOps

        from ..ocr.preprocess import prepare_for_ocr
        from ..ocr.psm import choose_tesseract_psm

        work, _scale = prepare_for_ocr(crop)
        gray = ImageOps.autocontrast(work.convert("L"), cutoff=1)
        mode = psm if psm is not None else int(choose_tesseract_psm(crop.width, crop.height))
        out = pytesseract.image_to_string(
            gray,
            lang=tesseract_langs_for_script(script),
            config=f"--oem 1 --psm {mode} -c preserve_interword_spaces=1",
        )
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(out.split()).strip()


def tesseract_langs_for_script(script: str) -> str:
    """Tesseract language set for a block's script.

    English is always in the set. When the script is unknown the set is the one
    that can read everything the refinement pass is likely to be handed, rather
    than a guess at the most common case.
    """
    name = (script or "").strip().lower()
    if name in ("target", "目标", "sbert"):
        # `SCRIPT_TARGET` - это «уже на языке назначения», а не почерк. Для
        # уточняющего прохода нужен настоящий почерк, и он берётся у языка
        # назначения. Без этого `target` проваливался в общий набор
        # `jpn+eng+rus`, и русский текст уточнялся не тем языком.
        name = script_of(target_language()) or ""
    if name in ("jpn", "japanese", "ja", "kana", "kanji"):
        return TESSERACT_JAPANESE
    if name in ("kor", "korean", "ko", "hangul"):
        return "kor+eng"
    if name in ("cyrl", "cyrillic", "rus", "ru"):
        return "rus+eng"
    if name in ("latn", "latin", "eng", "en"):
        return OCR_LANGS
    # Unknown: cover what this pass is actually asked about, Japanese included.
    return TESSERACT_MULTI


CONFUSABLE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("i", "1"),
        ("l", "1"),
        ("I", "1"),
        ("l", "|"),
        ("I", "|"),
        ("o", "0"),
        ("O", "0"),
        ("Q", "0"),
        ("s", "5"),
        ("S", "5"),
        ("B", "8"),
        ("b", "6"),
        ("G", "6"),
        ("g", "9"),
        ("q", "9"),
        ("z", "2"),
        ("Z", "2"),
    }
)


TEMPORAL_BACKED_BONUS = 0.9


def confusable_chars(a: str, b: str) -> bool:
    """Whether two single characters are ones the recogniser swaps."""
    if a == b:
        return True
    return (a, b) in CONFUSABLE_PAIRS or (b, a) in CONFUSABLE_PAIRS


def readings_are_confusable(a: str, b: str) -> bool:
    """Whether two readings differ only where the recogniser confuses glyphs.

    Equal length, and every position where they differ is a pair the recogniser
    swaps. "is" against "15" is confusable; "is" against "no" is not, and the
    difference matters: the first case is one element that may have been misread,
    the second is one element that genuinely changed.
    """
    left = str(a or "").strip()
    right = str(b or "").strip()
    if not left or not right or len(left) != len(right):
        return False
    if left == right:
        return True
    differing = [(x, y) for x, y in zip(left, right) if x != y]
    if not differing:
        return True
    return all(confusable_chars(x, y) for x, y in differing)


def _token_plausible(token: str) -> float:
    """Higher is better. Prefers real words, accepts pure numbers."""
    t = token.strip()
    if not t:
        return -1.0
    if DIGITS_RE.match(t):
        return 0.55
    if NUMERIC_LOOKING.match(t):
        # "I" / "l" / "|" alone: neither a word nor clearly a number
        return 0.05
    if (
        len(t) <= 4
        and " " not in t
        and any(c.isdigit() for c in t)
        and any(c.isalpha() for c in t)
    ):
        # l5, 1s, I5: OCR confusion rather than a word or a number
        return 0.05
    if LATIN_TOKEN_RE.match(t):
        return 0.95 if t.casefold() in _english_word_set() else 0.6
    if RE_KANA.search(t) or RE_HAN.search(t):
        return 0.8
    return 0.3


def resolve_short_element(
    region_img,
    box: tuple[int, int, int, int],
    current: str,
    previous: str = "",
) -> str:
    """Decide the true text of a short element from two targeted readings.

    Returns the original string when the evidence is not strong enough to
    override it, so a stable reading is never churned by a bad crop.

    ``previous`` is what this element read as on the last frame, and it is
    context rather than a candidate. It never votes: an element that really did
    change from "is" to "15" must be allowed to become "15", and a previous
    frame that outvoted fresh readings is how a toggle gets stuck on its old
    value forever. It has one use, which is to corroborate a reading some other
    engine produced - if Tesseract and the last frame agree on "is" while the
    crop read "15", that is two independent sources against one misread, and
    the misread loses. It only corroborates when the fresh reading is a
    confusion away from it, because when it is not, the element changed and
    continuity says nothing at all.
    """
    base = str(current or "").strip()
    script = block_script(base)
    rapid_text, rapid_conf = reocr_crop_rapid(region_img, box)
    tess_text = reocr_crop_tesseract(region_img, box, psm=7, script=script)
    if not tess_text:
        tess_text = reocr_crop_tesseract(region_img, box, psm=8, script=script)

    votes: dict[str, float] = {}

    def vote(text: str, weight: float) -> None:
        norm = normalize_for_compare(text)
        if norm:
            votes[norm] = votes.get(norm, 0.0) + weight

    if base:
        vote(base, 1.0)
    if rapid_text:
        vote(rapid_text, 0.5 + max(0.0, (rapid_conf - 40.0)) / 100.0)

    tess_weight = 0.6
    # Continuity as corroboration and not as a vote. The last frame agreeing
    # with Tesseract is two sources against one crop, and the crop is the one
    # that just produced "15" out of "is".
    prev_norm = normalize_for_compare(previous)
    tess_norm = normalize_for_compare(tess_text)
    if (
        previous
        and tess_text
        and prev_norm
        and prev_norm == tess_norm
        and readings_are_confusable(base, previous)
        and prev_norm != normalize_for_compare(base)
    ):
        tess_weight += TEMPORAL_BACKED_BONUS
        tlog(
            f"short-temporal base={base[:12]!r} tess={tess_text[:12]!r} "
            f"prev={previous[:12]!r}"
        )
    if tess_text:
        vote(tess_text, tess_weight)

    if not votes:
        return base
    if len(votes) == 1:
        only = next(iter(votes))
        if normalize_for_compare(base) == only:
            return base
        return only

    best_norm, best_score = "", -1.0
    for norm, weight in votes.items():
        score = weight * _token_plausible(norm)
        if score > best_score:
            best_norm, best_score = norm, score

    # Only override when the winner is clearly ahead.
    if best_score <= 0.0:
        return base
    others = [w * _token_plausible(n) for n, w in votes.items() if n != best_norm]
    if others and best_score - max(others) < 0.25:
        return base
    if normalize_for_compare(base) == best_norm:
        return base
    original = {
        normalize_for_compare(v): v
        for v in (base, rapid_text, tess_text, previous)
        if v
    }.get(best_norm, best_norm)
    tlog(
        f"short-resolve '{base[:16]}' -> '{original[:16]}' "
        f"(rapid={rapid_text[:12]!r}/{rapid_conf:.0f} tess={tess_text[:12]!r}"
        + (f" prev={previous[:12]!r}" if previous else "")
        + ")"
    )
    return original


def _box_has_ink(region_img, box: tuple[int, int, int, int]) -> bool:
    """Whether anything non-uniform is left inside the box.

    Flat fill means the region is now background, whatever it used to hold. The
    test is contrast rather than brightness, so it answers the same on a dark
    game screen and a pale page.

    Unable to tell is not the same as "nothing there": without a frame there is
    nothing to look at, and reporting that as an empty box would take down every
    card the moment a capture failed.
    """
    if region_img is None:
        return True
    try:
        # TrackedBlock.box is (x, y, w, h), not (x1, y1, x2, y2). Reading it the
        # other way cropped to a negative width, which raised, and the caller
        # treated the exception as "there is something there" - so a block whose
        # text had gone was never once reported gone.
        x, y, w, h = (int(v) for v in box)
        iw, ih = region_img.size
        x1 = max(0, min(iw - 1, x))
        y1 = max(0, min(ih - 1, y))
        x2 = max(x1 + 1, min(iw, x + max(1, w)))
        y2 = max(y1 + 1, min(ih, y + max(1, h)))
        crop = region_img.convert("L").crop((x1, y1, x2, y2))
        if crop.width < 2 or crop.height < 2:
            return False
        stat = ImageStat.Stat(crop)
        return float(stat.stddev[0]) > 6.0
    except Exception:  # noqa: BLE001
        # Cannot tell, so assume it is there: keeping a card one pass longer is
        # the cheaper mistake.
        return True


def _read_box(region_img, box: tuple[int, int, int, int]):
    """Read one element. The crop is wider than the box on purpose: when a
    label grows ("Two" -> "Eight") a tight crop cuts the new text off."""
    crop = _crop_box(region_img, box, pad=DIRTY_PAD + 2)
    if crop.width < 8 or crop.height < 8:
        return []
    return rapid_ocr_lines(crop, max_side=0)


def _reread_element(region_img, blk: TrackedBlock) -> str:
    """Text of a single element, with the short-token disambiguation."""
    x1, y1, x2, y2 = blk.box
    # Grow sideways only: a label that got longer must still be readable, but a
    # taller crop would pull in the neighbouring lines.
    grow_x = max(12, int((x2 - x1) * 0.7))
    grow_y = max(3, int((y2 - y1) * 0.15))
    wide = (x1 - grow_x, y1 - grow_y, x2 + grow_x, y2 + grow_y)

    rows = _read_box(region_img, wide)
    # keep only the lines that belong to this element
    top, bottom = y1 - grow_y, y2 + grow_y
    inside = [
        r
        for r in rows
        if r["box"][3] > top and r["box"][1] < bottom
    ] or rows
    inside.sort(key=lambda r: (r["box"][1], r["box"][0]))
    text = " ".join(str(r.get("text", "")).strip() for r in inside).strip()

    if text and len(text) <= SHORT_TOKEN_MAX and block_script(text) in (
        SCRIPT_EN,
        SCRIPT_UNKNOWN,
    ):
        fixed = resolve_short_element(region_img, wide, text, previous=blk.text or "")
        if fixed:
            text = fixed
    if not text:
        text = reocr_crop_tesseract(
            region_img, wide, psm=7, script=block_script(blk.text or "")
        )

    # A reading that is a prefix of the previous one is very likely truncated
    # by the crop, so it must not overwrite a known-good reading.
    if text and blk.text and text != blk.text and blk.text.startswith(text) and len(text) >= 2:
        if not DIGITS_RE.match(text):
            tdetail(f"reread-truncated {text!r} <- {blk.text!r}")
            return ""
    return text


def _merge_cards_by_position(kept: list[dict], fresh: list[dict]) -> list[dict]:
    """Replace the cards that sit where a fresh card appeared, keep the rest."""
    out: list[dict] = []
    for card in kept:
        bx = (int(card.get("x", 0)), int(card.get("y", 0)))
        half_w = max(8, int(card.get("src_w", card.get("w", 20))) // 2)
        half_h = max(6, int(card.get("src_h", card.get("h", 12))) // 2)
        cx, cy = bx[0] + half_w, bx[1] + half_h
        covered = False
        for f in fresh:
            fx = int(f.get("x", 0)) + max(8, int(f.get("src_w", f.get("w", 20))) // 2)
            fy = int(f.get("y", 0)) + max(6, int(f.get("src_h", f.get("h", 12))) // 2)
            tol_x = max(half_w, int(f.get("src_w", f.get("w", 20))) // 2) + 4
            tol_y = max(half_h, int(f.get("src_h", f.get("h", 12))) // 2) + 4
            if abs(cx - fx) <= tol_x and abs(cy - fy) <= tol_y:
                covered = True
                break
        if not covered:
            out.append(card)
    out.extend(fresh)
    return out
