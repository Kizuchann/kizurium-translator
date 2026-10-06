"""Реплика не поглощает строки, стоящие в других частях экрана.

На снимке ws13 описание внизу кадра уезжало к шапке — рисовалось там, где
текста нет. Причина оказалась не в вёрстке карточки, а в том, как
`extend_dialogue_with_bottom_pass` искал продолжение реплики: полоса чтения
шла от нижнего края блока до низа кадра, а верхней границы расстояния не было
вообще. Подпись «CV: Horse» без точки считалась недосказанной репликой, и в её
хвост записались строки из нижней панели — в 600 пикселей ниже.

Тесты ниже проверяют не этот снимок и не эти слова, а правило: продолжение
строки живёт в пределах интерлиньяжа, а всё, что дальше, — другой элемент
экрана. ws1-ws25 — это примеры, а не список, на который заточен движок.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from kizurium_translator.live import (
    extend_dialogue_with_bottom_pass,
    merge_subtitle_cluster,
    rapid_ocr_lines,
)

# Подпись в верхней части кадра.
TOP = (97, 319, 295, 370)
TOP_LH = 51
REACH = int(TOP_LH * 2.6)
# Строка в 550px ниже — другой элемент экрана, а не перенос.
FAR_Y = 925
# Строка сразу под блоком — перенос.
NEAR_Y = 384

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _font(size: int = 44):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _screen(lines: list[tuple[int, str]]) -> Image.Image:
    """Кадр с настоящим текстом: OCR должен видеть строки, а не полосы."""
    img = Image.new("RGB", (1920, 1080), (30, 10, 40))
    draw = ImageDraw.Draw(img)
    for y, text in lines:
        draw.text((120, y), text, fill=(250, 250, 250), font=_font())
    return img


def _block(text: str, box: tuple[int, int, int, int], lh: int = TOP_LH) -> dict:
    return {
        "text": text,
        "box": box,
        "line_height": lh,
        "conf": 90.0,
        "angle": 0.0,
        "kind": "dialogue",
        "line_boxes": [],
    }
@pytest.mark.needs_host


def test_ocr_really_sees_the_far_line():
    """Сторож: если кадр перестал читаться, остальные тесты meaningless.

    Без этой проверки тесты ниже проходили бы на старом коде — на картинке с полосами вместо букв,
    где OCR не видит ничего, нижний проход и не делал того, что чинили.
    """
    img = _screen([(FAR_Y, "When the combo is less than 100")])
    found = [
        r for r in rapid_ocr_lines(img)
        if r["box"][1] > FAR_Y - 20 and r["box"][3] > FAR_Y
    ]
    assert found, "тестовый кадр не читается — тесты ниже ничего не проверяют"


def test_a_line_far_below_the_block_is_not_a_continuation():
    """Строка в 550px ниже не влияет на блок.

    Это и был баг: подпись без точки считалась недосказанной репликой, полоса
    чтения шла до низа кадра, и строки нижней панели уезжали в её хвост через
    пол-экрана. Проверяется не состав хвоста, а то, что блок остался на своём
    месте: на старом коде он раздувался с (97,319,295,370) до (57,319,335,457),
    на новом не трогается вовсе.
    """
    img = _screen([(FAR_Y, "When the combo is less than 100")])
    out = extend_dialogue_with_bottom_pass(img, [_block("CV: Horse", TOP)])
    assert len(out) == 1
    for b in out:
        for lb in b.get("line_boxes") or []:
            assert lb["box"][1] <= TOP[3] + REACH, (
                "строка из дальней части экрана попала в хвост реплики"
            )
        x1, y1, x2, y2 = b["box"]
        assert y2 <= TOP[3], "блок вырос вниз из-за строки из другой части экрана"
        assert (x1, y1) >= (TOP[0] - 40, TOP[1]), "блок расползся в сторону"


def test_a_line_just_below_is_still_reachable():
    """Граница расстояния не отсекает перенос, который лежит рядом."""
    img = _screen([(NEAR_Y, "the combo will not")])
    blocks = [_block("When the combo is less than 100", (97, 319, 700, 370))]
    # Проверяем, что строка вообще попадает в зону рассмотрения: расстояние
    # между блоком и строкой заведомо меньше границы.
    assert NEAR_Y - TOP[3] <= REACH, "тест не проверяет ничего: строка вне зоны"
    out = extend_dialogue_with_bottom_pass(img, blocks)
    assert out, "нижний проход вернул пустоту"


def test_an_empty_band_does_not_inflate_the_block():
    """Пустая полоса под блоком не раздувает его вдвое.

    Кегль берётся из высоты бокса, поэтому раздутый бокс уменьшал шрифт:
    «Резюме: Лошадь» выходила мельче соседнего «HP:250» при одинаковой
    высоте строки.
    """
    out = extend_dialogue_with_bottom_pass(
        Image.new("RGB", (1920, 1080), (30, 10, 40)), [_block("CV: Horse", TOP)]
    )
    assert len(out) == 1
    _, y1, _, y2 = out[0]["box"]
    assert (y2 - y1) <= (TOP[3] - TOP[1]) * 1.6, "бокс раздут без строк, которых нет"


def test_box_never_drops_its_own_lines():
    """Бокс кластера — объединение боксов его строк, без ужимания по высоте.

    Раньше высота зажималась до `lh * nlines * 1.55 + 16`, и для кластера из
    четырёх строк через пол-экрана это 326px вместо 712. Бокс переставал
    содержать собственные строки, и карточка рисовалась там, где текста нет.
    """
    rows = [
        {"box": (97, 319, 295, 370), "text": "CV: Horse", "line_height": 51,
         "angle": 0.0, "conf": 95.0},
        {"box": (228, 923, 1543, 974), "text": "When the combo is less than 100",
         "line_height": 51, "angle": 0.0, "conf": 99.0},
        {"box": (1671, 954, 1807, 1004), "text": "SELECT", "line_height": 49,
         "angle": 0.0, "conf": 99.0},
        {"box": (228, 986, 805, 1031), "text": "the combo will not be interrupted",
         "line_height": 45, "angle": 0.0, "conf": 99.0},
    ]
    x1, y1, x2, y2 = merge_subtitle_cluster(rows, 1920, 1080)["box"]
    for r in rows:
        rx1, ry1, rx2, ry2 = r["box"]
        assert x1 <= rx1 and y1 <= ry1, "бокс кластера не покрывает начало строки"
        assert x2 >= rx2 and y2 >= ry2, "бокс кластера не покрывает конец строки"
