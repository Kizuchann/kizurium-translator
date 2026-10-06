"""Иконка приложения и .desktop-файл.

Зачем это здесь, а не нарисовано руками.

`assets/translator.png` - скриншот окна, 946x504. Иконка должна быть квадратной,
и в меню приложений файл такого размера растягивается и выглядит сломанным.
Поэтому иконка собирается кодом: она перерисовывается, воспроизводится и не
расходится с остальным оформлением.

    uv run python scripts/make_icon.py

Пишет:
    assets/icons/kizurium-translator.png   256x256
    assets/kizurium-translator.desktop

Что нарисовано: подложка цвета окна, сверху два «поля языка» и стрелка между
ними, снизу карточка с переводом. Ни логотипа, ни сторонних изображений, и ни
одного глифа шрифта - текст на иконке в 16px нечитаем, а кириллица в этом шрифте
рисуется квадратом-заглушкой.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONS = ASSETS / "icons"

# Цвета взяты из темы карточек оверлея, не выдуманы.
BG = (12, 14, 20)
CARD = (36, 40, 48)
PILL = (48, 53, 63)
TEXT = (232, 234, 238)
DIM = (108, 114, 128)

SIZE = 256
R = 24  # скругление подложки


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=R, fill=BG)

    # Ряд полей языка, как в верхней части окна: два пилюля и стрелка между ними.
    d.rounded_rectangle((28, 38, 106, 74), radius=18, fill=PILL)
    d.rounded_rectangle((150, 38, 228, 74), radius=18, fill=PILL)

    mid = 56
    d.line((112, mid, 144, mid), fill=TEXT, width=7)
    d.line((144, mid, 132, mid - 11), fill=TEXT, width=7)
    d.line((144, mid, 132, mid + 11), fill=TEXT, width=7)

    # Подсказка внутри пилюлей - точки, а не буквы.
    for cx in (52, 68, 84):
        d.ellipse((cx - 3, mid - 3, cx + 3, mid + 3), fill=TEXT)
    for cx in (174, 190, 206):
        d.ellipse((cx - 3, mid - 3, cx + 3, mid + 3), fill=DIM)

    # Карточка перевода: три строки разной длины, как настоящий текст.
    d.rounded_rectangle((28, 92, 228, 216), radius=18, fill=CARD)
    for y, x1, x2, ink in (
        (114, 48, 176, TEXT),
        (140, 48, 208, TEXT),
        (166, 48, 136, DIM),
    ):
        d.rounded_rectangle((x1, y, x2, y + 12), radius=6, fill=ink)

    return img


DESKTOP = """\
[Desktop Entry]
Type=Application
Version=1.0
Name=Kizurium Translator
GenericName=Screen Translator
Comment=Перевод текста на экране через OCR и оверлей Wayland
Comment[en]=Translate on-screen text with OCR and a Wayland overlay
Exec=kizurium-translator --toggle
TryExec=kizurium-translator
Icon=kizurium-translator
Terminal=false
Categories=Utility;Translation;GTK;
Keywords=translate;ocr;wayland;overlay;
StartupNotify=false
X-AppId=ru.kizurium.translator
"""


def write_desktop() -> pathlib.Path:
    # Проверка синтаксиса: desktop-файл, который desktop-file-validate не
    # принимает, просто не появляется в меню, и это видно только на месте.
    ET.fromstring(f"<x>{DESKTOP}</x>")
    path = ASSETS / "kizurium-translator.desktop"
    path.write_text(DESKTOP, encoding="utf-8")
    return path


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    icon = ICONS / "kizurium-translator.png"
    icon_img = draw_icon()
    icon_img.save(icon)
    print(f"{icon.relative_to(ROOT)}  {icon_img.size}")
    print(f"{write_desktop().relative_to(ROOT)}")


if __name__ == "__main__":
    main()
