"""Сэмплирование цвета обязано возвращать цвет, а не молча откатываться на дефолт.

`sample_ocr_cover_colors` читает пиксели бокса и решает, какой цвет — подложка,
а какой — глифы. Любая ошибка внутри переводится в `None`, а `None` на стороне
рисования означает плашку по умолчанию: чёрный фон и белый текст поверх
фиолетового экрана. Выглядит как «переводчик сломался», хотя сломан был ровно
один вызов, и заметить его по симптомам нельзя — только тестом.

Так и вышло: `Image.getdata()` в Pillow 12.3 был помечен deprecated, и его
заменили на `list(crop)`. `Image` не итерируется, и вызов падал
`TypeError: 'Image' object is not iterable`. 1195 тестов оставались зелёными,
а на экране у всех карточек был чёрный фон.

Правильный API — `get_flattened_data()`: те же пиксели в том же порядке, без
предупреждения. Тесты ниже зовут функцию напрямую, поэтому ловят и поломку
чтения пикселей, и молчаливый откат на дефолт.
"""

from __future__ import annotations

from PIL import Image

from kizurium_translator.live import ink_is_light, sample_ocr_cover_colors


def _box_image(bg: tuple[int, int, int], fg: tuple[int, int, int]) -> Image.Image:
    """Плашка с текстом: подложка bg, глифы fg — как настоящий OCR-бокс."""
    im = Image.new("RGB", (200, 60), bg)
    for y in range(10, 50):
        for x in range(10, 190):
            if (x // 6 + y // 6) % 2 == 0:
                im.putpixel((x, y), fg)
    return im


def test_dark_panel_returns_its_own_colours():
    """Тёмная подложка с яркими глифами — вернуть оба цвета, не None."""
    im = _box_image((94, 0, 82), (255, 25, 129))
    got = sample_ocr_cover_colors(im, (0, 0, 200, 60))
    assert got is not None, "пиксели не прочитались — плашка уйдёт в чёрный дефолт"
    bg, fg = got
    assert tuple(round(v * 255) for v in bg[:3]) == (94, 0, 82)
    assert tuple(round(v * 255) for v in fg[:3]) == (255, 25, 129)


def test_bright_button_returns_its_own_colours():
    """Яркая кнопка — тоже, а не `None` только из-за светлой подложки."""
    im = _box_image((137, 255, 12), (0, 209, 71))
    got = sample_ocr_cover_colors(im, (0, 0, 200, 60))
    assert got is not None
    bg, fg = got
    assert tuple(round(v * 255) for v in bg[:3]) == (137, 255, 12)
    assert tuple(round(v * 255) for v in fg[:3]) == (0, 209, 71)


def test_two_toned_line_is_not_read_as_one_colour():
    """Глиф и подложка различаются — значит функция различает их, а не одну роль."""
    im = _box_image((20, 24, 33), (200, 210, 220))
    got = sample_ocr_cover_colors(im, (0, 0, 200, 60))
    assert got is not None
    bg, fg = got
    assert bg[:3] != fg[:3]


def test_grayscale_crop_reads_as_numbers_and_keeps_ink_decision():
    """Режим `L` отдаёт числа, а не кортежи, и `ink_is_light` на этом живёт.

    `ink_is_light` берёт верхний квартиль яркости, чтобы решить, светлый ли
    текст. Кортежи вместо чисел или исключение ломают этот путь так же
    незаметно, как и цветовой.
    """
    im = Image.new("L", (48, 16), 20)
    # Светлой должна быть больше четверти: `ink_is_light` смотрит верхний
    # квартиль, и при 21% глифов он попадает в тёмную подложку.
    for x in range(12, 40):
        for y in range(2, 14):
            im.putpixel((x, y), 240)
    crop = im.convert("L").resize((48, 16))
    px = list(crop.get_flattened_data())
    assert px and all(isinstance(v, int) for v in px)
    assert sorted(px)[int(len(px) * 0.75)] >= 240
    assert ink_is_light(im, (0, 0, 48, 16)) is True
