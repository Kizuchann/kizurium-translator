"""Глоссарий: словарь по языку назначения грузится из данных.

Зачем данные, а не словарь в коде.

Словарь - это данные, а не логика: их 271 запись, они пополняются, и человек
их читает и правит, не открывая Python. В коде они были потому, что проект
умел переводить только на русский и словарь был один.

Добавление языка теперь: файл `data/glossary/<код>.toml` плюс одна строка в
`GLOSSARY_FILES`. Ни одной новой ветки в коде.

Язык по умолчанию - русский, потому что проект на нём вырос, и это осознанный
компромисс: `NO_GLOSSARY` значит «словаря нет», и при `target=ru` словарь
должен быть. Русский словарь лежит в данных рядом с пустыми заглушками
остальных языков, и это видно из репозитория.

Плохой файл не роняет перевод. Отсутствие словаря - это «словаря нет»:
перевод уходит в бэкенд и работает хуже, но работает. Исключение на
испорченный TOML означало бы, что опечатка в словаре останавливает
переводчик, а это худший вид поломки.
"""

from __future__ import annotations

import functools
import tomllib
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "glossary"

#: Язык, словарь которого достаётся, когда язык назначения не назван.
DEFAULT_LANG = "ru"

#: Известные словари. Файл без строки здесь - молчаливо неработающий язык,
#: поэтому список явный: тест проверяет, что он совпадает с содержимым
#: каталога.
GLOSSARY_FILES: tuple[str,...] = ("ru", "en")

#: Слово «уровень» для форм, где движок ломает число.
#:
#: Форма защиты общая, слово - нет. `Lv. 12` без этого превращается в имя, а
#: `Ур. 12` на английском экране выглядит как поломка. Добавление языка -
#: одна строка здесь.
LEVEL_WORD: dict[str, str] = {
    "ru": "Ур.",
    "uk": "Рів.",
    "en": "Lv.",
    "de": "Lv.",
    "fr": "Niv.",
    "es": "Nv.",
    "it": "Lv.",
    "pt": "Nv.",
    "pl": "Poz.",
    "tr": "Sv.",
    "ja": "レベル",
    "zh": "等级",
    "ko": "레벨",
}


def known(lang: str) -> bool:
    return (lang or "").strip().lower() in GLOSSARY_FILES


@functools.lru_cache(maxsize=32)
def _sections(code: str) -> dict[str, dict[str, str]]:
    """Секции словаря: `ui` и `game`.

    Секции держат словари порознь не для красоты: игровые термины и лейблы
    интерфейса выглядят одинаково, но приходят из разных источников, и в
    одном словаре их не отличить.
    """
    path = DATA_DIR / f"{code.lower()}.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, section in data.items():
        if isinstance(section, dict):
            out[name] = {str(k): str(v) for k, v in section.items()}
    return out


def section_for(lang: str, section: str) -> dict[str, str]:
    code = (lang or "").strip().lower() or DEFAULT_LANG
    return _sections(code).get(section, {})


@functools.lru_cache(maxsize=32)
def load_file(code: str) -> dict[str, str]:
    """Словарь языка из данных. Пустой словарь вместо исключения.

    Отсутствующий файл - это «словаря нет», и перевод уйдёт в бэкенд. Битого
    файла быть не должно, но если будет - последствие не должно быть хуже
    отсутствия словаря.
    """
    out: dict[str, str] = {}
    for section in _sections(code).values():
        out.update(section)
    return out


def entries_for(lang: str) -> dict[str, str]:
    """Словарь для языка назначения. Чужой словарь не выдаётся никогда.

    Отдавать русский словарь при `target=en` - значит рисовать на экране
    русский текст поверх японского интерфейса: выглядит как «перевод сломан»,
    хотя сломан только словарь.
    """
    code = (lang or "").strip().lower()
    if not code:
        code = DEFAULT_LANG
    return load_file(code)


def level_word(lang: str) -> str:
    code = (lang or "").strip().lower()
    if code in LEVEL_WORD:
        return LEVEL_WORD[code]
    # Язык не в таблице: латиница пишется так же почти везде, и «Lv.» не
    # выдаёт себя за перевод лучше, чем пустая строка.
    script = _script_of(code)
    if script == "cyrl":
        return "Ур."
    return "Lv."
