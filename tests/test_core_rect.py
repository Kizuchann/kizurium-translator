"""Геометрия как объект, а не как набор свободных функций.

Раздел 9.1 плана: `Rect` — типизированный объект без GTK зависимостей. До него
три слоя - tracking, layout и change detection - решали «пересекаются ли боксы»
каждый по-своему, и соглашение о границах жило в каждой копии отдельно.

Тесты ниже фиксируют не «метод существует», а соглашения, на которых стоит
сопоставление блоков. Смена любого из них тихо двигает границу «тот же самый
блок», и заметить это можно только на экране, где перевод начнёт дублироваться
или, наоборот, пропадать.

Прямоугольники полуоткрытые: `[x1, x2) x [y1, y2)`. Бокс, начинающийся ровно там,
где другой кончается, не имеет общей площади и не содержится в другом. При этом
`overlaps` считает касание ребром пересечением - расхождение с `iou` намеренное,
и на нём держатся две разные проверки; в модуле это описано.
"""

from __future__ import annotations

import pytest

from kizurium_translator.core import Rect, box_iou, box_size_ratio, boxes_overlap


def test_box_round_trips_through_both_forms() -> None:
    r = Rect.from_box((10, 20, 110, 70))
    assert r.box == (10, 20, 110, 70)
    assert (r.width, r.height) == (100, 50)
    assert Rect.from_xywh(10, 20, 100, 50) == r


def test_area_never_collapses_to_zero() -> None:
    """Нулевая площадь превращает любое отношение в деление на ноль.

    Пустой бокс бывает в середине протяжки и у однопиксельной линейки. Площадь
    floored на 1 - именно так вели себя прежние `_box_iou` и `_size_ratio`.
    При этом IoU двух вырожденных боксов остаётся 0.0, а не 1.0: пересечения у
    них нет вовсе, и прежний код возвращал ровно это.
    """
    empty = Rect(5, 5, 5, 5)
    assert empty.is_empty()
    assert empty.area == 1
    assert empty.size_ratio(Rect(5, 5, 5, 5)) == 1.0
    assert empty.iou(Rect(5, 5, 5, 5)) == 0.0


def test_edge_contact_is_an_overlap_but_has_no_area() -> None:
    """Касание ребром: пересечение есть, общей площади нет.

    Это расхождение между `overlaps` и `iou` намеренное и досталось от прежнего
    кода: группировка изменившихся элементов опирается на первое, сопоставление
    блоков - на второе. Свести их к одному ответу значит сдвинуть одну из границ
    молча.
    """
    a = Rect(0, 0, 100, 20)
    b = Rect(0, 20, 100, 40)
    assert a.overlaps(b) is True
    assert a.intersection(b) is None
    assert a.iou(b) == 0.0
    assert a.distance_to(b) == 0.0
    assert not a.contains(b)


def test_containment_excludes_a_box_starting_at_the_far_edge() -> None:
    outer = Rect(0, 0, 100, 100)
    assert outer.contains(Rect(10, 10, 90, 90))
    assert outer.contains(Rect(0, 0, 100, 100))
    assert not outer.contains(Rect(50, 50, 150, 150))


def test_size_ratio_compares_shapes_not_mixed_sides() -> None:
    """Вдвое шире и вдвое ниже - это не то же самое размер, но площадь равна.

    Прежняя версия брала числитель как ширину одного и высоту другого: 200*100
    против знаменателя 100*100 давало 2.0, то есть «сходство больше единицы».
    Проверка, которая спрашивает «похожи ли размеры», отвечала бы «да, даже
    очень» на прямоугольниках вдвое разной формы.
    """
    a = Rect(0, 0, 200, 50)
    b = Rect(0, 0, 100, 100)
    assert a.area == b.area
    assert a.size_ratio(b) == pytest.approx(1.0)
    # Та же пара по старой формуле: ширина первого умноженная на высоту второго.
    old = (a.width * b.height) / max(a.area, b.area)
    assert old > 1.0, "старый дефект должен быть воспроизводим, иначе он не выявлен"


def test_center_offset_is_scale_free() -> None:
    """Одинаковое расстояние между центрами - одинаковая величина.

    Сырое расстояние в пикселях назвало бы пару крупных боксов соседями, а пару
    мелких - чужими, хотя разница между ними ровно одна и та же.
    """
    big = Rect(0, 0, 400, 400)
    big_off = Rect(400, 0, 800, 400)
    small = Rect(0, 0, 40, 40)
    small_off = Rect(40, 0, 80, 40)

    assert big.center_offset(big_off) == pytest.approx(small.center_offset(small_off))


def test_overlaps_grows_by_the_pad() -> None:
    """Отступ превращает «рядом» в «касается».

    Группировка изменившихся элементов требует щедрого отступа, а решение «тот
    ли это блок» - тесного. Одна и та же операция, различается только число.
    """
    a = Rect(0, 0, 100, 20)
    b = Rect(0, 30, 100, 50)
    assert a.overlaps(b) is False
    assert a.overlaps(b, pad=10) is True
    assert boxes_overlap(a.box, b.box, pad=10) is True


def test_iou_of_a_contained_box_is_its_own_share() -> None:
    """Вложенный бокс: пересечение равно меньшему, объединение - сумме за вычетом."""
    outer = Rect(0, 0, 200, 200)
    inner = Rect(0, 0, 100, 100)
    assert outer.iou(inner) == pytest.approx(10000 / (40000 + 10000 - 10000))


def test_expanded_keeps_the_rectangle_a_rectangle() -> None:
    r = Rect(10, 10, 20, 20)
    assert r.expanded(5) == Rect(5, 5, 25, 25)
    assert r.expanded(0) == r


def test_module_level_helpers_agree_with_the_type() -> None:
    """Обёртки для сырых кортежей обязаны совпадать с самим типом.

    Иначе один и тот же вопрос про два бокса получает два разных ответа в
    зависимости от того, как вызывающий держит геометрию.
    """
    a = Rect(0, 0, 100, 60)
    b = Rect(40, 20, 140, 90)
    assert box_iou(a.box, b.box) == a.iou(b)
    assert box_size_ratio(a.box, b.box) == a.size_ratio(b)
    assert boxes_overlap(a.box, b.box) == a.overlaps(b)


def test_str_is_readable() -> None:
    assert str(Rect(4, 9, 24, 19)) == "4,9 20x10"
