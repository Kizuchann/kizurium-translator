"""Пакетный перевод кадра: у каждой строки свой язык источника.

Тела перенесены из `live/session.py` дословно. Цикл кадра только отдаёт сюда
порцию и забирает строки.
"""
from __future__ import annotations

import re
import time

from .. import translate as translate_mod
from ..core.text import RE_LAT, block_lang, tlog
from ..ocr.engine import normalize_japanese_text
from ..translation.service import (
    glossary_translation,
    is_translation_error,
    parse_marked_translation,
    same_line,
)
from ..typography.metrics import RE_JPN


def _translate_tail(text: str) -> str:
    """Translate the remainder of a phrase whose head came from the glossary."""
    from ..live.runtime import translator
    if not text:
        return ""
    if glossary_translation(text):
        return glossary_translation(text) or ""
    try:
        source = "ja" if RE_JPN.search(text) else "en"
        got = translator().translate(text, source=source, allow_slow=True)
    except Exception:  # noqa: BLE001
        return ""
    return got.strip() if got and not is_translation_error(text, got) else ""


def _translate_line_job(key: str, is_jpn: bool, allow_slow: bool) -> str:
    """Pool entry: positional args only (``allow_slow`` is keyword-only upstream)."""
    return translate_single_line(key, is_jpn, allow_slow=allow_slow)


def translate_single_line(original: str, is_jpn: bool = False, *, allow_slow: bool = False) -> str:
    # is_jpn is kept for callers outside the live loop; the text decides anyway
    # (see below), so a caller that passes it wrong loses nothing.
    """One string through the shared backend. Falls back to the source text."""
    from ..live.runtime import TRANSLATION, translator
    cleaned = (
        normalize_japanese_text(original)
        if is_jpn or RE_JPN.search(original)
        else original.strip()
    )
    if TRANSLATION.disabled:
        return cleaned

    manual = glossary_translation(cleaned)
    if manual:
        return manual

    # "<known term> <number> <rest>": translate the known head ourselves and
    # send only the rest. "Chapter 3 - The Frozen Harbour" came back as "Певица
    # 3" because free translation picked "singer" for "chapter". Only attempted
    # when the head is a term the glossary knows, and only accepted whole.
    if m:= re.fullmatch(r"([A-Za-z][A-Za-z ]{0,24}?)\s+(\d.*)", cleaned):
        head = glossary_translation(m.group(1).strip())
        tail = _translate_tail(m.group(2).strip()) if head else ""
        if head and tail:
            return f"{head} {tail}"

    source = "ja" if (is_jpn or RE_JPN.search(cleaned)) else ("en" if RE_LAT.search(cleaned) else "auto")
    got = translator().translate(cleaned, source=source, allow_slow=allow_slow)
    if got and not is_translation_error(cleaned, got) and not same_line(cleaned, got):
        return got
    return cleaned


def translate_many(
    items: list[str | dict],
    cache: dict[str, str],
) -> list[str]:
    """Translate a batch of elements, each carrying its own source language.

    ``items`` are ``{"text":..., "source": "en"|"ja"|"ko"|"ru"|"auto"}``. A bare
    string is accepted and has its language read off the text itself.

    There used to be an ``is_jpn`` argument, one boolean for the whole batch. It
    was the exact bug this layer is built to avoid: a frame is EN, JP, EN, JP
    and mixed at once, so a batch-level answer was either wrong for half the
    blocks or a guess dressed as a fact. A caller that has the OCR result in
    hand now sends that block's own ``lang``; a caller with only text gets the
    language derived from the text, which is per block by construction.

    Batching survives as a transport optimisation, grouped by source language so
    one HTTP call still carries one language.

    A response is rejected when the backend failed, not when it contains
    Japanese: a Japanese result is a legitimate translation.
    """
    from ..live.runtime import TRANSLATION, translator
    t0 = time.monotonic()
    texts: list[str] = []
    sources: list[str] = []
    for it in items:
        if isinstance(it, dict):
            text = str(it.get("text", ""))
            # The block's own language, when the caller knows it. block_lang is
            # the fallback rather than a default of "en": a default is how a
            # Japanese block ends up being sent as English.
            src = str(it.get("source") or it.get("lang") or "").strip() or block_lang(text)
        else:
            text = str(it)
            src = block_lang(text)
        texts.append(text)
        sources.append(src)

    if TRANSLATION.disabled:
        for text in texts:
            cache.setdefault(text, text)
        return [cache.get(t, t) for t in texts]

    tr = translator()
    missing: list[tuple[int, str, str]] = []
    glossary_n = 0
    disk_n = 0
    for idx, (text, src) in enumerate(zip(texts, sources)):
        key = normalize_japanese_text(text) if src == "ja" else text.strip()
        if text in cache:
            continue
        if key in cache:
            cache[text] = cache[key]
            continue
        manual = glossary_translation(key)
        if manual:
            cache[text] = manual
            tr.cache_store(key, manual, src)
            glossary_n += 1
            continue
        stored = tr.cache_lookup(key, src)
        if stored and (
            is_translation_error(text, stored)
            or translate_mod.translation_dropped_tail(text, stored)
        ):
            tr.cache_forget(key, src)
            stored = ""
        if stored:
            cache[text] = stored
            disk_n += 1
            continue
        # enumerate, not texts.index(text): two blocks on one frame can carry
        # the same text, and.index returns the first of them, so the second
        # block's result was stored against the first block's position.
        missing.append((idx, key, src))

    if missing:
        # One entry per (source, text): the same text twice in the same language
        # is one request, and the same text in two languages is two.
        unique: dict[str, str] = {}
        for _idx, key, src in missing:
            unique.setdefault(f"{src}\x1f{key}", key)
        tlog(f"translate-many-unique n={len(unique)} items={list(unique.values())[:5]}")
        pending: list[tuple[str, str]] = []
        for combo, key in unique.items():
            src = combo.split("\x1f", 1)[0]
            pending.append((key, src))

        # cap per cycle: 60 strings x HTTP is 20s+ on its own
        hard_cap = 28
        deferred = pending[hard_cap:]
        pending = pending[:hard_cap]
        if deferred:
            tlog(f"translate-cap {len(deferred)} deferred")

        still: list[tuple[str, str]] = []
        from .scheduler import SCHEDULER, pack_local_batch

        frame_rev = SCHEDULER.capture()
        # offline_only never opens gtx batch — fall through to per-line
        # (glossary/cache/local MT only).
        if not getattr(tr, "offline_only", False):
            for chunk in pack_local_batch(pending):
                groups: dict[str, list[str]] = {}
                for key, src in chunk:
                    groups.setdefault(src, []).append(key)
                for src, keys in groups.items():
                    if len(keys) < 2:
                        still.extend((k, src) for k in keys)
                        continue
                    payload = "\n".join(
                        f"@@KZT{idx:03d}@@ {normalize_japanese_text(k) if src == 'ja' else k}"
                        for idx, k in enumerate(keys)
                    )
                    blob = tr.via_gtx(payload, source=src)
                    parsed = {} if (not blob or is_translation_error(payload, blob)) else parse_marked_translation(blob)
                    tlog(f"gtx-response parsed={len(parsed)} keys={list(parsed.keys())} sample={str(list(parsed.values())[0])[:50] if parsed else 'empty'}")
                    for idx, key in enumerate(keys):
                        got = parsed.get(idx, "")
                        if got and not same_line(key, got) and not is_translation_error(key, got):
                            tr.cache_store(key, got.strip(), src)
                        else:
                            still.append((key, src))
            tlog(f"gtx-batch left={len(still)}")
        else:
            still = list(pending)
            tlog("offline_only: skip gtx-batch")

        if still:
            # When gtx is cooling after 429, single-line must use MyMemory —
            # otherwise the batch path leaves every miss untranslated.
            use_slow = bool(tr.allow_slow) or tr.gtx_cooling()
            futures = {
                (key, src): TRANSLATION.pool.submit(
                    _translate_line_job, key, src == "ja", use_slow
                )
                for key, src in still
            }
            for (key, src), fut in futures.items():
                try:
                    res = fut.result(timeout=8.0 if use_slow else 4.0)
                except Exception:  # noqa: BLE001
                    res = ""
                if res and not same_line(key, res) and not SCHEDULER.stale(frame_rev):
                    tr.cache_store(key, res, src)

        for key, src in deferred:
            tr.cache_get(text=key)  # keep the key warm for the next cycle

        tr.cache_flush(force=False)

        # fill the per-text cache from the shared cache
        for text, src in zip(texts, sources):
            if text in cache:
                continue
            key = normalize_japanese_text(text) if src == "ja" else text.strip()
            got = tr.cache_lookup(key, src)
            if got and not is_translation_error(text, got):
                cache[text] = got

    tlog(
        f"translate n={len(texts)} miss={len(missing)} "
        f"glossary={glossary_n} disk={disk_n} {int((time.monotonic()-t0)*1000)}ms"
    )
    # how the batch split between answers we already had and answers a
    # backend had to produce. This is the only place that knows which is which -
    # the caller passes a dict and cannot tell a fresh answer from a cached one,
    # so "we retranslated the whole screen" is invisible without this.
    from ..live.stats import BATCH_STATS

    BATCH_STATS.note(len(texts), len(missing))
    out: list[str] = []
    for text in texts:
        got = cache.get(text, text)
        out.append(translate_mod.carry_edge_punctuation(text, got))
    return out

