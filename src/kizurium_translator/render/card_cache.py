"""bounded LRU cache of rasterized translation cards.

Cache key:

```text
translation, font id, font size, weight, italic,
letter spacing, outline, shadow, available width/height, theme
```

Value: a Cairo ``ImageSurface`` (or any opaque painted buffer).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class CardCacheKey:
    translation: str
    font_id: str
    font_size: int
    weight: int
    italic: bool
    letter_spacing: float
    outline: float
    shadow: float
    width: int
    height: int
    theme: str = "default"

    @classmethod
    def from_block(
        cls,
        block: dict,
        *,
        width: int,
        height: int,
        theme: str = "default",
    ) -> CardCacheKey:
        return cls(
            translation=str(block.get("text") or ""),
            font_id=str(block.get("font") or block.get("family") or ""),
            font_size=int(block.get("font_px") or block.get("src_h") or 16),
            weight=int(block.get("weight") or 0),
            italic=bool(block.get("italic")),
            letter_spacing=float(block.get("tracking", 0.0) or 0.0),
            outline=float(block.get("stroke_w") or 0.0),
            shadow=float(block.get("shadow") or 0.0),
            width=max(1, int(width)),
            height=max(1, int(height)),
            theme=str(theme or "default"),
        )


class CardRenderCache:
    """Thread-safe LRU of painted card surfaces."""

    def __init__(self, max_items: int = 64) -> None:
        self._max = max(1, int(max_items))
        self._lock = threading.Lock()
        self._items: OrderedDict[CardCacheKey, object] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, key: CardCacheKey) -> object | None:
        with self._lock:
            if key not in self._items:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return self._items[key]

    def put(self, key: CardCacheKey, surface: object) -> None:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
            self._items[key] = surface
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.hits = 0
            self.misses = 0


# Process-wide cache used by the overlay painter.
CARD_CACHE = CardRenderCache(max_items=96)
