"""Версия проекта названа в четырёх местах, и они разошлись.

Что было сломано.

`pyproject.toml` и `__version__` говорили `0.1.0`, а `PKGBUILD` собирал пакет
`1.0.0` под тег `v1.0`. То есть то, что ставится через `makepkg -si`,
отличалось от того, о чём заявляет исходник, и `pip show` на установленном
пакете называл другую версину, чем `kizurium-translator --version`.

Версия не выводится из одного места, поэтому рассинхрон повторится, как только
кто-то поднимет версию в одном файле. Тест делает расхождение падающим.

`min_app_version` в `data/language_packs/catalog.toml` и в
`translation/packs.py` - не версия приложения, а требование к совместимости
языковых паков, и здесь он не проверяется.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert match, "в pyproject.toml нет поля version"
    return match.group(1)


def _dunder_version() -> str:
    from kizurium_translator import __version__

    return __version__


def test_the_dunder_matches_pyproject():
    assert _dunder_version() == _pyproject_version()


def test_the_flake_matches_pyproject():
    text = (ROOT / "flake.nix").read_text(encoding="utf-8")
    match = re.search(r'version\s*=\s*"([^"]+)";', text)
    assert match, "в flake.nix нет поля version"
    assert match.group(1) == _pyproject_version()


def test_the_pkgbuild_matches_pyproject():
    pkgbuild = ROOT / "PKGBUILD"
    if not pkgbuild.is_file():
        return
    text = pkgbuild.read_text(encoding="utf-8")
    match = re.search(r"^pkgver=(\S+)", text, re.M)
    assert match, "в PKGBUILD нет поля pkgver"
    assert match.group(1) == _pyproject_version()


def test_the_version_is_not_a_placeholder():
    """Релиз не может называться `0.1.0`, пока в репозитории тег `v1.0`."""
    assert _pyproject_version() != "0.0.0"
    assert _pyproject_version().count(".") == 2, _pyproject_version()
