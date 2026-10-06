"""Снимок настроек, который сессия применяет один раз.

`Config` неизменяем. Живой процесс читает не файл и не россыпь модульных
имён (`TARGET_LANG`, порядок OCR, цвет карточки), а этот снимок: `configure`
кладёт сюда значения из конфига, и нижние слои спрашивают их отсюда.

Один объект на процесс. У переводчика одна живая сессия и один pid, поэтому
снимок не делится между сессиями. Замена идёт подменой полей этого объекта,
а не копией в каждом слое: копия и есть рассинхрон, когда настройка
применилась и не подействовала.
"""

from __future__ import annotations

from typing import Any


class Active:
    """Применённые настройки кадра. Не конфиг: конфиг читается, это то, что действует."""

    def __init__(self) -> None:
        self.target_lang = "ru"
        self.ocr_engines: tuple[str,...] = ("rapid", "meiki", "tesseract")
        self.card_bg = (0.05, 0.07, 0.12, 0.55)
        self.card_fg = (1.0, 1.0, 1.0, 1.0)
        self.card_radius = 7.0
        self.card_outline = True


_ACTIVE = Active()


def current() -> Active:
    return _ACTIVE


def target_lang() -> str:
    return _ACTIVE.target_lang


def set_target_lang(lang: str) -> None:
    _ACTIVE.target_lang = (lang or "ru").strip().lower() or "ru"


def ocr_engines() -> tuple[str, ...]:
    return _ACTIVE.ocr_engines


def card_bg() -> tuple[float, float, float, float]:
    return _ACTIVE.card_bg  # type: ignore[return-value]


def card_fg() -> tuple[float, float, float, float]:
    return _ACTIVE.card_fg  # type: ignore[return-value]


def card_radius() -> float:
    return _ACTIVE.card_radius


def apply_frame(config: Any) -> None:
    """Движки и карточка. Язык назначения пишет только `set_target_language`."""
    engines = tuple(str(v) for v in config.ocr_engines)
    if engines:
        _ACTIVE.ocr_engines = engines
    _ACTIVE.card_bg = tuple(config.card_bg)
    _ACTIVE.card_fg = tuple(config.card_fg)
    _ACTIVE.card_radius = float(config.card_radius)
    _ACTIVE.card_outline = bool(config.card_outline)
