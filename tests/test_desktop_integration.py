"""Desktop-интеграция: файл меню есть, иконка квадратная, и то и другое ставится.

Что было сломано.

Ни `.desktop`-файла, ни иконки не было вообще: команда ставилась в `/usr/bin`,
запускалась из терминала и не появлялась в меню приложений. `APP_ID =
"ru.kizurium.translator"` был объявлен и нигде не использовался.

Единственное изображение в проекте, `assets/translator.png`, - скриншот окна
946x504. Иконкой он быть не может: панель растягивает такой файл, и квадратная
иконка рядом с прямоугольной выглядит поломанной.

Ниже проверяется то, что реально ломает запуск из меню: валидный файл,
обязательные поля, квадратная иконка и то, что оба файла кладутся в пакет.
"""

from __future__ import annotations

import configparser
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "assets" / "kizurium-translator.desktop"
ICON = ROOT / "assets" / "icons" / "kizurium-translator.png"


def _desktop() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(DESKTOP, encoding="utf-8")
    return parser


class TestTheDesktopFile:
    def test_it_is_there(self):
        assert DESKTOP.is_file(), f"нет {DESKTOP}"

    def test_the_required_keys_are_present(self):
        entry = _desktop()["Desktop Entry"]
        for key in ("Type", "Name", "Exec", "Icon", "Terminal", "Categories"):
            assert key in entry, f"нет ключа {key}"

    def test_it_starts_the_overlay_not_a_terminal(self):
        entry = _desktop()["Desktop Entry"]
        assert entry["Type"] == "Application"
        # Terminal=false: приложение рисует оверлей слоем, окно терминала ему
        # только мешает.
        assert entry["Terminal"] == "false"
        assert entry["Exec"] == "kizurium-translator --toggle"

    def test_the_command_it_names_is_the_command_we_ship(self):
        entry = _desktop()["Desktop Entry"]
        binary = entry["Exec"].split()[0]
        assert (ROOT / "pyproject.toml").read_text(encoding="utf-8").count(
            f'"{binary}"'
        ) >= 1, f"{binary} не объявлен в [project.scripts]"

    def test_the_icon_name_matches_the_installed_icon(self):
        entry = _desktop()["Desktop Entry"]
        assert entry["Icon"] == ICON.stem, entry["Icon"]

    def test_the_app_id_is_the_one_the_project_declares(self):
        sys.path.insert(0, str(ROOT / "src"))
        from kizurium_translator import APP_ID

        assert _desktop()["Desktop Entry"]["X-AppId"] == APP_ID

    def test_it_has_no_leftover_placeholders(self):
        text = DESKTOP.read_text(encoding="utf-8")
        assert not re.search(r"\bTODO\b|\bFIXME\b|\bXXX\b", text)
        assert text.endswith("\n"), "нет перевода строки в конце файла"


class TestTheIcon:
    def test_it_is_there_and_square(self):
        from PIL import Image

        assert ICON.is_file(), f"нет {ICON}"
        with Image.open(ICON) as img:
            w, h = img.size
        assert w == h, f"иконка {w}x{h} - панель растянет её в прямоугольник"

    def test_it_is_big_enough_for_hicolor_256(self):
        from PIL import Image

        with Image.open(ICON) as img:
            assert img.size == (256, 256), img.size

    def test_the_window_screenshot_is_not_being_used_as_one(self):
        """Скриншот 946x504 остаётся скриншотом и иконкой не становится."""
        from PIL import Image

        shot = ROOT / "assets" / "translator.png"
        if not shot.is_file():
            pytest.skip("скриншота окна нет")
        with Image.open(shot) as img:
            assert img.size != ICON and shot.name != ICON.name


class TestBothFilesArePackaged:
    @pytest.mark.parametrize(
        ("packaging", "path"),
        [
            ("PKGBUILD", "assets/kizurium-translator.desktop"),
            # The icon is installed as $pkgname.png, so the literal file name
            # does not appear - the directory does.
            ("PKGBUILD", "assets/icons/$pkgname.png"),
            ("flake.nix", "assets/kizurium-translator.desktop"),
            ("flake.nix", "assets/icons/kizurium-translator.png"),
        ],
    )
    def test_the_packaging_installs_it(self, packaging: str, path: str):
        text = (ROOT / packaging).read_text(encoding="utf-8")
        assert path in text, f"{packaging} не ставит {path}"

    def test_the_install_paths_are_the_ones_the_desktop_standard_expects(self):
        build = (ROOT / "PKGBUILD").read_text(encoding="utf-8")
        assert "usr/share/applications/$pkgname.desktop" in build
        assert "usr/share/icons/hicolor/256x256/apps/$pkgname.png" in build
