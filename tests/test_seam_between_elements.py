"""Соседние кнопки списка не склеиваются в один абзац.

На экране выбора языка (ws13) «日本語», «簡体中文» и「繁體中文」 стояли тремя
отдельными кнопками в левой колонке — у каждой своя светлая подложка, между
ними чёрный фон экрана. `group_japanese_blocks` свёл их в один блок, и на
выходе получилась одна белая карточка с тремя переводами разом, причём втрое
уже оригинала.

По одним боксам перенос абзаца и список неразличимы: левый край один, шаг по
вертикали меньше высоты строки. Разница видна в зазоре — у переноса он залит
той же панелью, между кнопками там фон экрана.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from kizurium_translator.live import group_japanese_blocks

# Три кнопки левой колонки: 75px высоты, 20px чёрного зазора между ними.
BUTTONS = [
    ((322, 379, 498, 454), "日本語"),
    ((295, 475, 522, 549), "簡体中文"),
    ((294, 567, 522, 642), "繁體中文"),
]


def _line(text: str, box: tuple[int, int, int, int], lh: int = 75) -> dict:
    return {
        "text": text,
        "box": box,
        "line_height": lh,
        "conf": 95.0,
        "angle": 0.0,
        "kind": None,
        "line_boxes": [],
    }


def _screen(buttons: list[tuple[tuple[int, int, int, int], str]]) -> Image.Image:
    """Панель на всю колонку либо отдельная подложка под каждой кнопкой."""
    img = Image.new("RGB", (1920, 1080), (7, 6, 8))  # фон экрана почти чёрный
    draw = ImageDraw.Draw(img)
    for box, text in buttons:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 - 6, y1 - 4, x2 + 6, y2 + 4), fill=(90, 90, 90))
        draw.text((x1 + 4, y1 + 8), text, fill=(20, 20, 20))
    return img


def _panel_screen(lines: list[tuple[tuple[int, int, int, int], str]]) -> Image.Image:
    """Одна панель на все строки — зазор залит ею же, как у переноса абзаца."""
    img = Image.new("RGB", (1920, 1080), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    for box, text in lines:
        x1, y1, x2, y2 = box
        draw.text((x1 + 4, y1 + 8), text, fill=(20, 20, 20))
    return img


# Первый тест ловит регрессию по поведению: на коде без проверки шва эти три
# кнопки снова слипаются в один блок. Второй и третий требуют двухаргументной
# сигнатуры `group_japanese_blocks(lines, region_img)` — на старом коде они
# падают с TypeError, что само по себе верно: разделение по шву тогда ещё не
# существовало.


def test_buttons_separated_by_a_seam_stay_separate():
    img = _screen(BUTTONS)
    out = group_japanese_blocks([_line(t, b) for b, t in BUTTONS], img)
    assert len(out) == len(BUTTONS), (
        "кнопки, разделённые зазором чужого фона, слиплись в один блок"
    )
    assert [str(o["text"]).strip() for o in out] == [t for _, t in BUTTONS]


def test_lines_on_one_panel_still_join():
    """Проверка на то, что правило не отключает настоящий перенос абзаца."""
    wrapped = [
        ((300, 300, 1500, 360), "ここには日本語の文章があります"),
        ((300, 365, 1200, 425), "そして二行目も同じ文章の中です"),
    ]
    img = _panel_screen(wrapped)
    out = group_japanese_blocks([_line(t, b) for b, t in wrapped], img)
    assert len(out) == 1, "перенос абзаца на одной панели перестал склеиваться"


def test_seam_needs_pixels():
    """Без картинки решение не принимается: иначе отказ данных ломает текст."""
    out = group_japanese_blocks([_line(t, b) for b, t in BUTTONS], None)
    assert out, "без картинки функция должна вернуть строки как есть"
