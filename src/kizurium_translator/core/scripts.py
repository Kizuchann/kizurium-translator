"""Письменности и языки: одна таблица вместо кириллицы в коде.

Зачем файл.

Проверка «строка уже на языке назначения, не переводить её» была написана
четыре раза независимо, и все четыре раза через долю кириллицы - в
`skip_source`, `is_mostly_russian`, `drop_russian_columns` и в счётчике кадра.
Ни одно из этих мест не читало `Config.target_lang`, поэтому проект умел
переводить только на русский и смена языка в конфиге была бессмысленной:
фильтры продолжали считать русский «своим», а английский «чужим».

Здесь одна таблица «язык → письменность» и один набор регулярок. Места, которые
решают, переводить ли строку, зовут сюда и больше ничего о письменности не
знают.

Два решения, которые стоит понимать, потому что они неочевидны.

Неизвестный язык не считается латиницей. Иначе «перевести на несуществующий
язык» молча превратится в «перевести на английский»: строка на этом языке
удовлетворяет признаку «уже на своём» и отбрасывается. Лучше лишний запрос
бэкенду, чем выброшенная строка.

Латинца не даёт исходный язык. По ней определить нельзя: французский,
немецкий и английский выглядят одинаково. Возвращать `en` по умолчанию значит
назвать исходный язык неверно и склеить записи в кэше разных языков под одним
ключом. Правильный ответ здесь - `None`, и вызывающий код передаёт бэкенду
`auto`.
"""

from __future__ import annotations

import functools
import re

#: Язык → письменность. Единственное место, где живёт это знание.
SCRIPT_OF_LANG: dict[str, str] = {
    # Кириллица
    "ru": "cyrl", "uk": "cyrl", "bg": "cyrl", "sr": "cyrl", "mk": "cyrl",
    # Латиница
    "en": "latn", "fr": "latn", "de": "latn", "es": "latn", "pt": "latn",
    "it": "latn", "nl": "latn", "pl": "latn", "tr": "latn", "id": "latn",
    "vi": "latn", "ro": "latn", "sv": "latn", "no": "latn", "da": "latn",
    "fi": "latn", "cs": "latn", "hu": "latn",
    # Восточноазиатские
    "ja": "jpan",           # кану; одних иероглифов мало - это китайский
    "zh": "hans", "zh-CN": "hans",
    "zh-TW": "hant",
    "ko": "hang",
    # Прочие, на случай если кто-то их добавит
    "ar": "arab", "fa": "arab", "ur": "arab",
    "he": "hebr",
    "hi": "deva", "mr": "deva", "ne": "deva",
    "th": "thai",
    "el": "grek",
}

#: Письменность → регулярка её букв.
_PATTERN_SRC: dict[str, str] = {
    "latn": r"A-Za-zÀ-ɏ",
    "cyrl": r"Ѐ-ӿ",
    "jpan": r"぀-ヿ",
    "hans": r"㐀-䶿一-鿿",
    "hant": r"㐀-䶿一-鿿",
    "hang": r"가-힣ᄀ-ᇿ",
    "arab": r"؀-ۿݐ-ݿ",
    "hebr": r"֐-׿",
    "deva": r"ऀ-ॿ",
    "thai": r"฀-๿",
    "grek": r"Ͱ-Ͽἀ-῿",
}

PATTERN_OF_SCRIPT: dict[str, re.Pattern[str]] = {
    name: re.compile(f"[{body}]") for name, body in _PATTERN_SRC.items()
}

#: Наборы для подсчёта, непересекающиеся. Иероглифы одинаковы для китайского
#: и для традиционного, поэтому считать их надо одним счётчиком: иначе сумма
#: по наборам больше числа букв и любая строка из иероглифов выглядит как
#: смешанная из двух письменностей.
_COUNT_GROUPS: dict[str, str] = {
    "latn": "latn", "cyrl": "cyrl", "jpan": "jpan", "hang": "hang",
    "arab": "arab", "hebr": "hebr", "deva": "deva", "thai": "thai",
    "hans": "hans", "grek": "grek",
}

#: Классификатор одной буквы: одна регулярка с именованными группами вместо
#: десяти отдельных. `skip_source` зовётся на каждой строке в нескольких
#: местах цикла кадра, и десять проверок на букву давали цикл на 1.1 с дольше.
#: `lastgroup` говорит, в какую группу попала буква, - это и есть письменность.
_CLASSIFY_SRC = "|".join(
    f"(?P<{name}>[{_PATTERN_SRC[src]}])" for name, src in _COUNT_GROUPS.items()
)
_CLASSIFY = re.compile(_CLASSIFY_SRC)

_COUNT_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(f"[{_PATTERN_SRC[src]}]") for name, src in _COUNT_GROUPS.items()
}

#: Регулярка по языку. Иероглифы без каны считаются китайскими, а не
#: японскими: в игре строка из одних иероглифов - это китайская надпись, и
#: считать её японской значит отбросить её как «уже на своём».
_SCRIPT_PATTERNS: dict[str, re.Pattern[str]] = {
    **PATTERN_OF_SCRIPT,
    "jpan": re.compile(r"[぀-ヿ]"),
    "hans": re.compile(r"[㐀-䶿一-鿿]"),
    "hant": re.compile(r"[㐀-䶿一-鿿]"),
}

ALL_SCRIPTS: frozenset[str] = frozenset(PATTERN_OF_SCRIPT)

#: Обратная таблица для определения исходного языка. Для письменности с
#: несколькими языками берётся первый - он же самый частый, - и это осознанный
#: компромисс: точнее без словаря не определить, а «не определить» для
#: кириллицы означало бы отправлять русский текст в сеть как `auto`.
_CODE_BY_SCRIPT: dict[str, str] = {
    "cyrl": "ru", "jpan": "ja", "hang": "ko", "hans": "zh", "hant": "zh-TW",
}

#: Сколько букв нужно, чтобы строка считалась «на своём языке». Ниже этого -
#: одиночная буква интерфейса: `OK`, `LV`, номер, и такой текст не язык.
_MIN_LETTERS = 3

#: Доля букв целевой письменности, выше которой строка считается на своём
#: языке. Порог тот же, что был у кириллицы, и он не точный: он отвечает на
#: вопрос «похоже ли это на двуязычную вёрстку», а не на вопрос о происхождении.
_TARGET_SHARE = 0.25


def is_known(lang: str) -> bool:
    return _key(lang) in SCRIPT_OF_LANG


def _key(lang: str) -> str:
    return (lang or "").strip().lower()


#: Таблица с нормализованными ключами. Смешанный регистр в ключах означал,
#: что `zh-CN` не находился никогда: поиск приводит строку к нижнему.
SCRIPT_OF_LANG = {k.lower(): v for k, v in SCRIPT_OF_LANG.items()}


def script_of(lang: str) -> str | None:
    """Письменность языка. `None` для неизвестного - не латиница по умолчанию."""
    return SCRIPT_OF_LANG.get(_key(lang))


def pattern_of(script: str) -> re.Pattern[str]:
    """Регулярка письменности.

    `re.compile` для неизвестной письменности даёт регулярку, которой ничего не
    соответствует. Это осознанно: неизвестная письменность не должна
    превращаться в «совпало со всем».
    """
    return _SCRIPT_PATTERNS.get(script, re.compile(r"(?!)"))


def letters_of(text: str) -> list[str]:
    """Только буквы: цифры и пунктуация о происхождении ничего не говорят."""
    return [c for c in (text or "") if c.isalpha()]


def share_in_script(text: str, script: str) -> float:
    """Доля букв строки, написанных этой письменностью."""
    letters = letters_of(text)
    if not letters:
        return 0.0
    pattern = pattern_of(script)
    hits = sum(1 for c in letters if pattern.match(c))
    return hits / len(letters)


@functools.lru_cache(maxsize=4096)
def dominant_script(text: str) -> str | None:
    """Письменность, на которой написана большая часть букв.

    `None`, если письменностей больше одной или непонятно какой: смешанная
    строка не принадлежит ни одному языку целиком, и объявлять её чьей-то -
    значит отбросить половину текста.
    """
    letters = letters_of(text)
    if not letters:
        return None
    counts: dict[str, int] = {}
    for c in letters:
        m = _CLASSIFY.match(c)
        name = m.lastgroup if m is not None else None
        if name is not None:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    best, hits = max(counts.items(), key=lambda kv: kv[1])
    # Половина букв на одной письменности - это ещё смешанная строка, и
    # объявлять её целиком чьей-то значит отбросить вторую половину.
    if hits * 2 < len(letters):
        return None
    return best


def is_in_target_script(text: str, lang: str, *, min_share: float = _TARGET_SHARE,
                        min_letters: int = _MIN_LETTERS) -> bool:
    """Строка уже на языке назначения, и переводить её не нужно.

    `lang` неизвестен - ответ `False`. Признать строку «уже переведённой» без
    знания языка нельзя, а вот оставить её в работе можно всегда: лишний
    запрос бэкенду дешевле, чем потерянная строка на экране.
    """
    script = script_of(lang)
    if script is None:
        return False
    if len(letters_of(text)) < min_letters:
        return False
    return share_in_script(text, script) >= min_share


def code_for_script(script: str) -> str | None:
    """Исходный язык по письменности, или `None` если письменность неоднозначна.

    Латиница неоднозначна: по ней язык не определить. Возвращать `en` значит
    назвать исходный язык неверно и склеить записи кэша разных языков под
    одним ключом. Правильный ответ - `None`, и бэкенд получает `auto`.
    """
    return _CODE_BY_SCRIPT.get(script)

#: Язык назначения читается из снимка `core.active`, который ставит `configure`.
#: Модульная переменная здесь больше не пишется: две записи — две правды.
def set_target_language(lang: str) -> None:
    """Единственная запись языка назначения. Зовут из `configure`."""
    from . import active

    active.set_target_lang(lang)


def target_language() -> str:
    from . import active

    return active.target_lang()
