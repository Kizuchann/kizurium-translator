"""Сетевые бэкенды перевода.

Адрес gtx живёт здесь, а не в цикле кадра. Это неофициальный endpoint
`translate.googleapis.com/translate_a/single?client=gtx`, не Google Cloud
Translation API: он ограничивает частоту и может ответить 429.

MyMemory и deep-translator не входят в основной путь. Их подключает
`allow_slow` или авто-fallback, когда gtx уже в cooldown после 429: иначе
кадр остаётся с голым глоссарием, пока Google молчит.
"""

from __future__ import annotations

from typing import Protocol


class TranslationBackend(Protocol):
    backend_id: str

    def supports(self, source: str, target: str) -> bool: ...

    def translate(self, texts: list[str], source: str, target: str) -> list[str]: ...


def gtx_request(http: object, text: str, source: str, target: str, timeout: float = 3.5):
    """Один GET. Вызывающий сам разбирает 429 и тело ответа."""
    return http.get(  # type: ignore[attr-defined]
        "https://translate.googleapis.com/translate_a/single",
        params={
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "q": text,
        },
        timeout=timeout,
    )


def _lang(code: str) -> str:
    from ..translate import _lang_code

    return _lang_code(code)


# MyMemory wants regional tags (`en-GB`, `ru-RU`), not the short ISO codes
# gtx accepts. Passing bare `en`/`ru` raises LanguageNotSupportedException and
# the fallback looks "broken" even though the backend is fine.
_MYMEMORY_LANGS: dict[str, str] = {
    "en": "en-GB",
    "ru": "ru-RU",
    "ja": "ja-JP",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "ko": "ko-KR",
    "zh": "zh-CN",
    "zh-CN": "zh-CN",
    "zh-TW": "zh-TW",
    "pt": "pt-PT",
    "it": "it-IT",
    "pl": "pl-PL",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "ar": "ar-SA",
}


def _mymemory_lang(code: str) -> str:
    key = _lang(code)
    return _MYMEMORY_LANGS.get(key, key)


class MyMemoryBackend:
    backend_id = "mymemory"

    def supports(self, source: str, target: str) -> bool:
        return bool(target)

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        try:
            from deep_translator import MyMemoryTranslator
        except ImportError:
            return ["" for _ in texts]
        src = source if source and source != "auto" else "en"
        out: list[str] = []
        for text in texts:
            try:
                got = MyMemoryTranslator(
                    source=_mymemory_lang(src),
                    target=_mymemory_lang(target),
                ).translate(text) or ""
                out.append(got.strip())
            except Exception:  # noqa: BLE001
                out.append("")
        return out


class DeepTranslatorBackend:
    backend_id = "deep-translator"

    def supports(self, source: str, target: str) -> bool:
        return bool(target)

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            return ["" for _ in texts]
        src = source if source and source != "auto" else "en"
        out: list[str] = []
        for text in texts:
            try:
                got = GoogleTranslator(source=_lang(src), target=_lang(target)).translate(text) or ""
                out.append(got.strip())
            except Exception:  # noqa: BLE001
                out.append("")
        return out


def slow_backends(*, skip_google: bool = False) -> list[TranslationBackend]:
    """Backends used when gtx is unavailable.

    ``skip_google`` is set while gtx is cooling down after a 429: deep-translator's
    Google path hits the same rate limit and only burns the cycle.
    """
    out: list[TranslationBackend] = [MyMemoryBackend()]
    if not skip_google:
        out.append(DeepTranslatorBackend())
    return out
