"""Точные замены пользователя. Не пополняются машинным переводом."""

from __future__ import annotations

_USER: dict[str, str] = {}


def replace(mapping: dict[str, str] | None) -> None:
    """Подменяет пользовательский слой целиком. Пустое значение — не запись."""
    global _USER
    _USER = {str(k): str(v) for k, v in (mapping or {}).items() if str(v)}


def exact(term: str) -> str | None:
    return _USER.get(term) or None


def snapshot() -> dict[str, str]:
    return dict(_USER)
