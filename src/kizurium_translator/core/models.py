"""Geometry primitives shared by tracking, layout and change detection.

`Rect` — типизированный геометрический объект без GTK
зависимостей. До него геометрия была размазана по свободным функциям внутри
`live.py`, и три разных слоя решали «пересекаются ли боксы» каждый по-своему.

Прямоугольник хранится как `(x1, y1, x2, y2)`: так он лежит в OCR-боксах и в
словарях блоков без преобразований, и `Rect` — обёртка над этой формой, а не
новый формат, который надо таскать через все слои.

Все площади и пересечения считаются в полуоткрытых границах `[x1, x2)`, и
вырожденный бокс (нулевая ширина или высота) даёт площадь не меньше единицы.
Это поведение перенесено из прежних `_box_iou` и `_size_ratio` как есть: на нём
стоит сопоставление блоков, и смена соглашения здесь тихо сдвинула бы границу
«тот же самый блок».
"""
from __future__ import annotations

from typing import NamedTuple

Box = tuple[int, int, int, int]


class Rect(NamedTuple):
    """An axis-aligned rectangle in layout pixels."""

    x1: int
    y1: int
    x2: int
    y2: int

    # -- construction -------------------------------------------------

    @classmethod
    def from_box(cls, box: Box) -> Rect:
        """Build from the `(x1, y1, x2, y2)` tuples OCR produces."""
        x1, y1, x2, y2 = box
        return cls(x1, y1, x2, y2)

    @classmethod
    def from_xywh(cls, x: int, y: int, width: int, height: int) -> Rect:
        return cls(x, y, x + width, y + height)

    # -- form ---------------------------------------------------------

    @property
    def box(self) -> Box:
        """The `(x1, y1, x2, y2)` tuple, for the many places that still speak it."""
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    @property
    def area(self) -> int:
        """Never below 1.

        A collapsed box - the empty selection mid-drag, or a one-pixel rule -
        measures as empty here, and a ratio against zero is not a comparison, it
        is a crash. Both the old IoU and the old size ratio already floored the
        area at 1 for exactly this reason, and flooring it in one place is what
        keeps that guarantee from drifting apart between callers.
        """
        return max(1, self.width) * max(1, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def diagonal(self) -> float:
        return (self.width + self.height) / 2.0

    # -- relations ----------------------------------------------------

    def intersection(self, other: Rect) -> Rect | None:
        """The shared area, or None when the two do not touch."""
        x1, y1 = max(self.x1, other.x1), max(self.y1, other.y1)
        x2, y2 = min(self.x2, other.x2), min(self.y2, other.y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2, y2)

    def union(self, other: Rect) -> Rect:
        return Rect(
            min(self.x1, other.x1),
            min(self.y1, other.y1),
            max(self.x2, other.x2),
            max(self.y2, other.y2),
        )

    def contains(self, other: Rect) -> bool:
        """Is `other` entirely inside this one, edges included.

        Half-open, so a box that starts exactly where this one ends is not
        contained. Sharing an edge is not containment.
        """
        return (
            other.x1 >= self.x1
            and other.y1 >= self.y1
            and other.x2 <= self.x2
            and other.y2 <= self.y2
        )

    def overlaps(self, other: Rect, pad: int = 0) -> bool:
        """Do the two touch at all, grown by `pad` on each side.

        `pad` is how near counts as touching: grouping neighbouring changed
        elements needs a generous one, and deciding whether two boxes are the
        same element needs a tight one. The same call serves both because the
        difference is the number, not the operation.

        Boxes that share only an edge DO count as overlapping. That disagrees
        with `iou`, which reports 0.0 for the same pair, because the
        intersection there is an empty area. Both behaviours are what the
        previous `_boxes_overlap` and `_box_iou` did, and the grouping of
        changed regions depends on the first while the block matching depends on
        the second. Making them agree would move one of those boundaries, so
        they are kept apart on purpose and named rather than unified.
        """
        return not (
            self.x2 + pad < other.x1
            or other.x2 + pad < self.x1
            or self.y2 + pad < other.y1
            or other.y2 + pad < self.y1
        )

    def iou(self, other: Rect) -> float:
        """Intersection over union, from 0 to 1.

        Both areas are floored at 1, so two collapsed boxes at the same spot
        compare as a full match rather than dividing by zero. That is the
        behaviour the previous `_box_iou` had.
        """
        inter = self.intersection(other)
        inter_area = 0 if inter is None else inter.area
        if inter_area <= 0:
            return 0.0
        union = self.area + other.area - inter_area
        return inter_area / float(union)

    def size_ratio(self, other: Rect) -> float:
        """How alike the two are in size, from 0 to 1.

        The smaller area over the larger one, both as width times height. An
        earlier version took the numerator as one box's width times the other's
        height, so a box twice as wide and half as tall came out as identical -
        it is not the same shape at all - and anything asking this was told the
        boxes matched.
        """
        a, b = self.area, other.area
        return min(a, b) / float(max(a, b))

    def center_offset(self, other: Rect, min_scale: float = 8.0) -> float:
        """Distance between centres, in units of this rectangle's half-diagonal.

        Scale-free on purpose: a box 40 pixels across and one 400 pixels across
        are 100 pixels apart in both cases, and a raw distance would call the
        pair of large ones neighbours and the pair of small ones strangers.
        """
        acx, acy = self.center
        bcx, bcy = other.center
        d = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
        return d / max(min_scale, self.diagonal)

    def distance_to(self, other: Rect) -> float:
        """Nearest distance between the two; 0 when they touch or overlap.

        Each axis contributes its own gap: the distance is zero along an axis
        where the boxes already overlap, so two boxes sharing an edge are zero
        apart on that axis and the other axis alone decides the answer.
        """
        gap_x = max(0, max(self.x1 - other.x2, other.x1 - self.x2))
        gap_y = max(0, max(self.y1 - other.y2, other.y1 - self.y2))
        return float((gap_x**2 + gap_y**2) ** 0.5)

    def expanded(self, pad: int) -> Rect:
        return Rect(self.x1 - pad, self.y1 - pad, self.x2 + pad, self.y2 + pad)

    # -- predicates ---------------------------------------------------

    def is_empty(self) -> bool:
        return self.x2 <= self.x1 or self.y2 <= self.y1

    def __str__(self) -> str:
        return f"{self.x1},{self.y1} {self.width}x{self.height}"


def boxes_overlap(a: Box, b: Box, pad: int = 0) -> bool:
    """`Rect.overlaps` for callers that still hold raw tuples."""
    return Rect.from_box(a).overlaps(Rect.from_box(b), pad)


def box_iou(a: Box, b: Box) -> float:
    return Rect.from_box(a).iou(Rect.from_box(b))


def box_center_offset(a: Box, b: Box) -> float:
    return Rect.from_box(a).center_offset(Rect.from_box(b))


def box_size_ratio(a: Box, b: Box) -> float:
    return Rect.from_box(a).size_ratio(Rect.from_box(b))
