""": bounded LRU card render cache."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.render.card_cache import (  # noqa: E402
    CARD_CACHE,
    CardCacheKey,
    CardRenderCache,
)


def test_phase50_key_fields():
    key = CardCacheKey(
        translation="Привет",
        font_id="Noto Sans",
        font_size=16,
        weight=700,
        italic=False,
        letter_spacing=0.0,
        outline=1.4,
        shadow=0.0,
        width=120,
        height=24,
        theme="dark",
)
    assert key.translation == "Привет"
    assert key.width == 120


def test_lru_evicts_oldest():
    cache = CardRenderCache(max_items=2)
    k1 = CardCacheKey("a", "f", 12, 0, False, 0.0, 0.0, 0.0, 10, 10)
    k2 = CardCacheKey("b", "f", 12, 0, False, 0.0, 0.0, 0.0, 10, 10)
    k3 = CardCacheKey("c", "f", 12, 0, False, 0.0, 0.0, 0.0, 10, 10)
    cache.put(k1, "s1")
    cache.put(k2, "s2")
    assert cache.get(k1) == "s1"
    cache.put(k3, "s3")
    assert cache.get(k2) is None
    assert cache.get(k3) == "s3"
    assert cache.get(k1) == "s1"


def test_from_block_and_global_cache():
    CARD_CACHE.clear()
    block = {"text": "HUD", "font": "Sans", "src_h": 14, "tracking": 0.1}
    key = CardCacheKey.from_block(block, width=40, height=14)
    CARD_CACHE.put(key, "surface")
    assert CARD_CACHE.get(key) == "surface"
    assert CARD_CACHE.hits >= 1
