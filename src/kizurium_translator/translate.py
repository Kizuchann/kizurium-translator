"""Single translation backend shared by the live overlay and the text window.

Responsibilities: language detection, the Google ``translate_a`` endpoint,
optional fallback backends, a bounded persistent cache, a static glossary and
cooldown handling. Both the live overlay and the text translator window go
through:class:`Translator` so there is exactly one implementation.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .core.scripts import code_for_script

RE_CYR = re.compile(r"[Ѐ-ӿ]")
RE_LAT = re.compile(r"[A-Za-z]")
RE_HANG = re.compile(r"[가-힯]")
RE_HAN = re.compile(r"[一-鿿]")

SCRIPT_LANGS = {
    "jpn": "ja",
    "kor": "ko",
    "chi_sim": "zh-CN",
    "chi_tra": "zh-TW",
}


_LANG_CODES = {
    "en": "en", "ja": "ja", "ru": "ru", "zh": "zh-CN", "ko": "ko",
    "es": "es", "de": "de", "fr": "fr", "it": "it", "pt": "pt",
    "tr": "tr", "pl": "pl",
}


# Capitalisation in game UI is a weak signal: "Team Edit" and a two-word
# organisation name are both capitalised runs, and only one of them is a name.
# So a run is treated as a name only when the glossary does not already know
# the phrase, when it sits inside a sentence rather than opening one, or when
# the name is listed below. Free translation turned an organisation's name into
# a common word, which in a game is worse than showing the original.
# Three groups, and the difference between them is who they are for.
#
# _UI_TRUTHY and _GAME_TRUTHY are vocabulary any screen uses: buttons, dialogs,
# and the words every game puts on its own interface. They are on by default,
# because a capitalised "Settings" or "Combo" is not a name in any program and
# treating it as one leaves it untranslated.
#
# _TITLE_TRUTHY is names belonging to particular games: a character name and an
# organisation are not vocabulary, and a build that carries them by default is
# asserting the user plays those games. They come on
# with KIZURIUM_TRANSLATOR_GAME_GLOSSARY, the same switch that turns on
# GAME_GLOSSARY, so one decision covers both halves.
_UI_TRUTHY: frozenset[str] = frozenset()
_GAME_TRUTHY: frozenset[str] = frozenset()
_TITLE_TRUTHY: frozenset[str] = frozenset()
_DEFAULT_TRUTHY: frozenset[str] = frozenset()
_PROPER_NAMES: frozenset[str] = frozenset()
_TITLE_NAMES: frozenset[str] = frozenset()
_STAT_ABBR: frozenset[str] = frozenset()

_LEXICON_GROUPS = None


def _lexicon():
    global _LEXICON_GROUPS
    if _LEXICON_GROUPS is None:
        from .lexicon.store import groups as _lexicon_groups
        _LEXICON_GROUPS = _lexicon_groups()
    return _LEXICON_GROUPS


def _bind_lexicon() -> None:
    groups = _lexicon()
    global _UI_TRUTHY, _GAME_TRUTHY, _TITLE_TRUTHY, _DEFAULT_TRUTHY
    global _PROPER_NAMES, _TITLE_NAMES, _STAT_ABBR
    _UI_TRUTHY = groups.ui
    _GAME_TRUTHY = groups.game_vocab
    _TITLE_TRUTHY = groups.title_vocab
    _DEFAULT_TRUTHY = _UI_TRUTHY | _GAME_TRUTHY
    _PROPER_NAMES = groups.proper_names
    _TITLE_NAMES = groups.title_names
    _STAT_ABBR = groups.stat_abbr


# Off until the user says they are playing one of these games.
_TITLE_TRUTHY_ON = False


def enable_title_glossary(enabled: bool) -> int:
    """Turn the per-title names on or off. Returns how many are active now.

    Called from the same place as GAME_GLOSSARY, so that the two halves of "hold
    this game's vocabulary fixed" turn on together and are explained by one
    switch.
    """
    global _TITLE_TRUTHY_ON
    _TITLE_TRUTHY_ON = bool(enabled)
    return len(_TITLE_TRUTHY) if _TITLE_TRUTHY_ON else 0


# Terms that are genuinely names even when they open a sentence or a label.
# Names that any game might use. A class is not a character, and it reads as a
# capitalised run in any program; leaving it out of the list means "Vanguard
# deployment" gets half its phrase protected and half translated.
_bind_lexicon()

_PROPER_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[\u2019'\-][A-Za-z0-9]+)*\b")


def active_proper_names() -> frozenset[str]:
    """The names currently treated as names, including the opt-in ones."""
    if _TITLE_TRUTHY_ON:
        return frozenset(_PROPER_NAMES | _TITLE_NAMES | _TITLE_TRUTHY)
    return frozenset(_PROPER_NAMES)


def _stat_or_plain(word: str) -> bool:
    if word.upper() in _STAT_ABBR or word in _DEFAULT_TRUTHY:
        return True
    return _TITLE_TRUTHY_ON and word in _TITLE_TRUTHY


def _all_caps_is_style(word: str) -> bool:
    """Заглавными целиком - это стиль оформления, а не имя.

    По-английски имена не пишут заглавными. `NEKO` на экране выбора
    персонажа - это часть рисунка, `NEW ILLUSTRATION IS NOW UNLOCKED!!` -
    заголовок, и отличаются они только регистром, если смотреть только на
    него. Регулярка собирает имя из любого слова с заглавной буквы, поэтому
    `IS NOW UNLOCKED` после первого слова уходило в плейсхолдеры целиком: к
    движку уходило `【0】 【1】 【2】 【3】!!`, возвращалось то же самое, и пять
    строк в одном блоке оставались по-английски, при том что строка над ними с
    точно таким же текстом перевелась целиком - она пришла одним боксом.

    Сокращения - другое дело: `HP`, `BGM`, `EXP` это то, как игра называет
    предмет, и они уже перечислены поимённо в _STAT_ABBR, где проверяются
    раньше. Длины здесь нет намеренно: `IS` и `NEW` короче любого порога, и
    именно они оставляли строку не переведённой.
    """
    if not word.isupper():
        return False
    # Известное имя остаётся именем независимо от регистра: список имён
    # набирали вручную по играм, и `REIMU` в нём - то же самое имя, что
    # `Reimu`, а не оформление заголовка.
    if word in active_proper_names():
        return False
    # Букв должно быть хотя бы две: `100%` и `2P` - это числа с знаком и
    # номер игрока, а не слово, написанное заглавными.
    return sum(1 for ch in word if ch.isalpha()) >= 2


# A path is a name, not a sentence. `bin` is a directory and the backend is
# confident that it means a wastepaper basket; `install` is a script and it is
# translated as the verb. Neither is a mistranslation of the text - it is a
# mistranslation of the *thing*, and the only way to get it right is to say so
# before the backend sees it.
_PATH_RE = re.compile(
    r"(?<![\w./-])"
    r"(?:[.~]?[A-Za-z0-9_-]+/)*"              # optional directory part
    r"[.~]?[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}"  # a name with an extension
    r"(?![\w-])"
)

# A version is a number, not a file. `v2.0.4` and `1.2.3` are three numbers and
# an extension is letters, so the test is what follows the last dot.
_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)+$", re.I)

# Names that are a whole element in a file listing and mean nothing in
# English prose. This is not a glossary of English: each of these is a
# conventional name, and the convention is what the backend cannot know. A
# directory called `bin` is a directory in every project ever written.
_PATH_WORDS = {
    "bin", "src", "lib", "build", "dist", "out", "test", "tests", "docs", "doc",
    "www", "assets", "img", "images", "static", "public", "vendor", "include",
    "nix", "pkg", "cmd", "api", "app", "apps", "etc", "var", "usr", "opt",
    "home", "root", "tmp", "dev", "run", "sys", "proc", "boot", "mnt", "media",
    "install", "setup", "config", "example", "examples", "scripts", "tools",
    "locale", "locales", "i18n", "migrations", "fixtures", "utils", "helpers",
    # Dotted names with no extension. These are configuration files whose whole
    # name is a dot and a word, so there is no extension to recognise them by.
    ".gitignore", ".gitattributes", ".gitmodules", ".editorconfig", ".env",
    ".npmrc", ".nvmrc", ".prettierrc", ".eslintrc", ".babelrc", ".dockerignore",
    ".gitkeep", ".htaccess", ".well-known",
}


def normalize_for_compare(text: str) -> str:
    """Whitespace and case-insensitive form, for asking whether anything changed."""
    s = re.sub(r"[\s\u3000]+", "", str(text or "")).casefold()
    return s


def protect_paths(text: str) -> tuple[str, list[str]]:
    """Replace every file path with a placeholder, returning what was found.

    Only a name that carries an extension, or a slash-separated path, counts.
    A bare word is prose and belongs to the translator: `bin` in a sentence is
    still a word, and protecting every short token would leave most of the text
    untranslated.
    """
    if not text:
        return text, []
    found: list[str] = []

    def _swap(m: re.Match) -> str:
        token = m.group(0)
        if _VERSION_RE.match(token):
            return token
        # A domain-looking thing is not a path in a repository listing.
        if "." in token and "/" not in token and token.count(".") == 1:
            head = token.split(".")[0]
            if head.lower() in {"www", "http", "https"} or head.isdigit():
                return token
        found.append(token)
        return f"\u3010{len(found) - 1}\u250f\u3011"

    text = _PATH_RE.sub(_swap, text)

    def _swap_word(m: re.Match) -> str:
        word = m.group(0)
        if word.casefold() not in _PATH_WORDS:
            return word
        # Only when it stands alone as an element. "open src directory" is a
        # sentence about a source tree and reads better translated.
        before = text[: m.start()].rstrip()
        after = text[m.end() :].lstrip()
        # Only when it stands alone as an element. "install the package" is a
        # sentence about an action and reads better translated; "bin", alone on
        # a row of a file listing, is a name.
        if before or after:
            return word
        found.append(word)
        return f"\u3010{len(found) - 1}\u250f\u3011"

    return re.sub(r"(?<![\w./-])([A-Za-z0-9_.+-]+)(?![\w./-])", _swap_word, text), found


def restore_paths(text: str, found: list[str]) -> str:
    """Put the protected paths back, unchanged and in order."""
    for i, token in enumerate(found):
        text = text.replace(f"\u3010{i}\u250f\u3011", token)
    return text


def protect_proper_nouns(text: str) -> tuple[str, dict[str, str]]:
    """Swap names and acronyms for placeholders the engine cannot mangle.

    Returns the rewritten text and the placeholder -> original mapping. Names
    survive verbatim, which matters in a game where the name is the only thing
    the player can match against what is on screen.

    Placeholders are ASCII ``ZZNAMEnZZ`` (not lenticular ``【n】``): local OPUS-MT
    turns ``【0】`` into Cyrillic junk like ``ПРЮ`` and the restore pass never
    finds the token, so ``Doctor`` becomes garbage on the overlay.
    """
    mapping: dict[str, str] = {}
    runs: list[tuple[int, int]] = []
    words = [(m.start(), m.end(), m.group(0)) for m in _PROPER_RE.finditer(text)]

    i = 0
    while i < len(words):
        start, end, word = words[i]
        if _stat_or_plain(word) or len(word) < 2 or _all_caps_is_style(word):
            # "Press E" is a keybind and "Lv. 12" is a level label, not a name.
            i += 1
            continue
        if re.match(r"\.\s*\d", text[end: end + 3]):
            i += 1
            continue
        # Collect a run of consecutive capitalised words.
        run = [word]
        j = i + 1
        while j < len(words):
            gap = text[words[j - 1][1] : words[j][0]]
            if gap.strip() and not re.fullmatch(r"[\s\-']+", gap):
                break
            nxt = words[j][2]
            if _stat_or_plain(nxt) or nxt.isupper() and len(nxt) > 1:
                break
            run.append(nxt)
            j += 1
        phrase = " ".join(run)
        sentence_start = start == 0 or text[:start].rstrip()[-1:] in ".!?:;\u3002\u2026"
        known = active_proper_names()
        known_name = phrase in known or any(n in known for n in run)
        if sentence_start and not known_name:
            # Skip the whole run, not one word: otherwise the second word looks
            # mid-sentence and half of "Early Bird Bonus" gets protected.
            i = j
            continue
        runs.append((start, words[j - 1][1]))
        i = j

    if not runs:
        return text, {}

    out: list[str] = []
    pos = 0
    for a, b in runs:
        key = f"ZZNAME{len(mapping)}ZZ"
        mapping[key] = text[a:b]
        out.append(text[pos:a])
        out.append(key)
        pos = b
    out.append(text[pos:])
    return "".join(out), mapping


def restore_proper_nouns(text: str, mapping: dict[str, str]) -> str:
    """Put protected names back. Prefer glossary Cyrillic when one exists."""
    from .translation.service import glossary_translation

    for key, value in mapping.items():
        replacement = glossary_translation(value) or value
        if key in text:
            text = text.replace(key, replacement)
            continue
        # Legacy lenticular placeholders from older cache rows / gtx answers.
        legacy = f"\u3010{key[6:-2]}\u3011" if key.startswith("ZZNAME") and key.endswith("ZZ") else ""
        if legacy and legacy in text:
            text = text.replace(legacy, replacement)
            continue
        bare = key[1:-1] if key.startswith("\u3010") else key
        for open_b, close_b in (("[", "]"), ("{", "}"), ("(", ")")):
            text = text.replace(f"{open_b}{bare}{close_b}", replacement)
    return text


_SKELETON_TOKEN = "__KZT_P{n}__"
"""Placeholder for interior punctuation.

`carry_edge_punctuation` protects the punctuation at the two ends of a line,
because a run of punctuation lives at an edge. Interior punctuation is the other
half of the input and had nothing protecting it: `My:Name` came back as `моё имя`
with the colon gone, and `Kira -` with the dash gone, both because a
translation engine is right to treat them as its own punctuation to place.

The token is deliberately long and unambiguous. It must survive the backend
unchanged (so it must not look like anything in any language) and it must not be
something a translator would translate, reorder or shorten. `ZZNAME0ZZ` already
does the job for names and has been through every backend; this follows the same
shape with an index that cannot collide with it.
"""


_SKELETON_RE: re.Pattern[str] | None = None


def _skeleton_re() -> re.Pattern[str]:
    """Interior punctuation to protect, as whole runs.

    Compiled lazily from the same alphabet as the edge class, and for the same
    reason it has to be escaped the same way: an unescaped `]` inside a character
    class closes it, and `-` starts a range.

    Whole runs, not single characters: `?!` is one mark and becomes one token, so
    the backend cannot insert a space inside it.
    """
    global _SKELETON_RE
    if _SKELETON_RE is None:
        chars = r".!?…~*\-—–「」『』（）()\[\]{},:;"
        _SKELETON_RE = re.compile(rf"[{chars}]+")
    return _SKELETON_RE


def build_punctuation_skeleton(text: str) -> tuple[str, dict[str, str]]:
    """Replace interior punctuation with placeholders before a backend sees it.

    Returns the protected text and the mapping, the same shape
    `protect_proper_nouns` returns, so the existing
    `placeholders_survived` / `restore_proper_nouns` machinery verifies and
    undoes it without knowing what kind of token it is holding.

    Nothing is protected when there is no interior punctuation: a line of plain
    words is not made more likely to survive by being sent through a substitution
    it does not need.
    """
    if not text:
        return text, {}
    mapping: dict[str, str] = {}
    out: list[str] = []
    last = 0
    for i, m in enumerate(_skeleton_re().finditer(text)):
        # The edges are carry_edge_punctuation's job; protecting them here too
        # would double them.
        if m.start() == 0 or m.end() >= len(text.rstrip()):
            continue
        token = _SKELETON_TOKEN.format(n=i)
        if token in text:
            # A source that already contains the token shape is not data we can
            # un-protect reliably; leave that text alone rather than guess.
            return text, {}
        mapping[token] = m.group(0)
        out.append(text[last : m.start()])
        out.append(token)
        last = m.end()
    if not mapping:
        return text, {}
    out.append(text[last:])
    return "".join(out), mapping


def restore_punctuation_skeleton(text: str, mapping: dict[str, str]) -> str:
    """Put interior punctuation back where the placeholder is."""
    if not mapping or not text:
        return text
    for token, punct in mapping.items():
        text = text.replace(token, punct)
    return text


def skeleton_survived(text: str, mapping: dict[str, str]) -> bool:
    """Whether a backend kept every punctuation placeholder.

    A lost one is not a cosmetic problem: the token stands in for a character the
    source had, so dropping it silently deletes punctuation. The result is then
    rejected by the caller and the next backend gets a turn - the same shape as
    the existing `placeholders_survived` rejection.
    """
    return placeholders_survived(text, mapping)


def placeholders_survived(text: str, mapping: dict[str, str]) -> bool:
    """True when every protected token is still findable for restore."""
    if not mapping:
        return True
    for key in mapping:
        if key in text:
            continue
        if key.startswith("ZZNAME") and key.endswith("ZZ"):
            legacy = f"\u3010{key[6:-2]}\u3011"
            if legacy in text:
                continue
        return False
    return True


def translation_dropped_tail(source: str, translated: str) -> bool:
    """Local MT often keeps the first sentence and drops the rest.

    On a long paragraph that left English ``No matter what happens...`` glowing under
    the card while the Russian stopped at ``человеком, ПРЮ``.
    """
    src = str(source or "").strip()
    dst = str(translated or "").strip()
    if len(src) < 60 or not dst:
        return False
    src_stops = len(re.findall(r"[.!?…]+", src))
    dst_stops = len(re.findall(r"[.!?…。]+", dst))
    if src_stops >= 2 and dst_stops < src_stops:
        return True
    if len(dst) < max(24, int(len(src) * 0.42)):
        return True
    return False


# Punctuation that has to survive translation, and which is register
# rather than content. The class covers the runs at the edge of a line:
#
#......  !  ?  !?  ?!  …  ~  *  -  —  –
#
# and the brackets, which matter for Japanese UI above all: `「」` and `『』`
# are how a line of dialogue is marked there, and a card that dropped them reads
# as narration instead of speech. `()` `[]` `{}` are the same argument for every
# other game - a choice prompt can arrive as `(Hidden)`.
# `[`, `]` and `-` are escaped because inside a character class an unescaped `]`
# closes the class and an unescaped `-` is a range: `[...[]{}]` parses as the
# range `[` to `{` and matches almost nothing that was asked for.
_PUNCT_CHARS = r".!?…~*\-—–「」『』（）()\[\]{}"

_EDGE_RUN = re.compile(rf"^[\s]*([{_PUNCT_CHARS}]+|[\s]*)")
_EDGE_TAIL = re.compile(rf"([{_PUNCT_CHARS}]+|[\s]*)$")


def carry_edge_punctuation(src: str, out: str) -> str:
    """Put the original's opening and closing punctuation back on a translation.

    Punctuation at the edges of a line is not content, it is register, and a
    translation engine treats it as neither: "..Umm, huh.. E-excuse me!" comes
    back as "Угу-угу, хм, извините!" with the hesitation gone. On a hesitant line
    that is the whole point of it - the dots are what the character is doing - and
    their absence leaves the card sitting over the dots that carry the meaning.

    So the run is taken from the source and put back. It is not rewritten into the
    target's conventions because there are no conventions to rewrite it into:
    an ellipsis is three dots in most of the languages here, and inventing a
    local variant of it is more likely to be wrong than to be better.

    Only the edges, and only when the translation has none of its own: a target
    that opened with its own punctuation has been given the register already, and
    two runs of dots is not what anybody said.
    """
    if not src or not out:
        return out
    s_lead = _EDGE_RUN.match(src)
    o_lead = _EDGE_RUN.match(out)
    lead = (s_lead.group(1) if s_lead else "").strip()
    if lead and (not o_lead or not o_lead.group(1).strip()):
        out = lead + out.lstrip()

    s_tail = _EDGE_TAIL.search(src)
    o_tail = _EDGE_TAIL.search(out)
    tail = (s_tail.group(1) if s_tail else "").strip()
    if tail and (not o_tail or not o_tail.group(1).strip()):
        out = out.rstrip() + tail
    return out


def _lang_code(code: str) -> str:
    """Map our short code to what the fallback backends expect.

    An unknown or unresolved code is passed through as ``auto`` rather than
    turned into ``en``. Defaulting to English meant a Japanese or Korean source
    was announced to the backend as English, and the backend's own detection
    never got the chance: the wrong answer was a well-formed request.
    """
    key = (code or "").lower().strip().split("-")[0]
    if not key or key == "auto":
        return "auto"
    return _LANG_CODES.get(key, key)


def detect_lang(text: str) -> str | None:
    """Best-effort source language, or ``None`` when it cannot be decided."""
    raw = text or ""
    # Kana settles it outright, and it has to be checked first: Japanese uses
    # the ideographic comma 、 constantly, so testing punctuation before kana
    # would send every Japanese sentence to the Chinese backend.
    if re.search(r"[぀-ヿゝゞ]", raw):
        return "ja"
    # Латиница неоднозначна: французский, немецкий, испанский и английский
    # выглядят одинаково. Называть источник `en` значит склеить записи кэша
    # разных языков под одним ключом и попросить бэкенд переводить `en→target`
    # для текста, который на `fr`. Честный ответ здесь - не знаю, и `auto`
    # бэкенд разбирает сам.
    #
    # Past that, the punctuation is what tells a Chinese sentence from a
    # kanji-only Japanese one. A Chinese sentence is mostly Han with ， and 。 or
    # ？ between the words, and dropping the non-letters before looking for them
    # threw away the only thing that made the difference.
    if _CJK_CN_PUNCT.search(raw):
        return "zh-CN"
    letters = [c for c in raw if c.isalpha()]
    if len(letters) < 2:
        return None
    cyr = sum(1 for c in letters if RE_CYR.match(c))
    kana = sum(1 for c in letters if re.match(r"[぀-ヿ]", c))
    han = sum(1 for c in letters if re.match(r"[一-鿿]", c))
    hang = sum(1 for c in letters if RE_HANG.match(c))
    latin = sum(1 for c in letters if c.isascii() and c.isalpha())
    total = len(letters)
    han_text = "".join(c for c in letters if re.match(r"[一-鿿]", c))
    if kana >= 1:
        return "ja"
    if hang >= 1:
        return "ko"
    if cyr / total > 0.3:
        # Язык названия кириллицы берётся из таблицы, а не константой: у
        # кириллицы пять языков, и `uk`/`bg`/`sr` раньше уходили в сеть как
        # `ru`. Честный ответ «первый по таблице» - компромисс, но он лучше
        # зашитого `ru`, который был неверен для всех, кроме русского.
        return code_for_script("cyrl")
    if han / total > 0.3 and latin / total < 0.2:
        # Han alone is not enough to say Chinese. Japanese is written with Han
        # too, and a game UI label is often a single word with no kana anywhere
        # near it: 設定, 開始, 能力, 第3章. Those are common words in Japanese, and
        # they were being sent to a Chinese backend, which returned a plausible
        # and wrong translation with no error to show for it.
        #
        # Characters that only exist in Chinese, or the punctuation that only
        # comes with it, are what separates the two. Without one of those the
        # honest answer is that it is Japanese: Han text with no Chinese-specific
        # character is far more often Japanese than a Chinese label that uses none.
        if _looks_chinese(han_text, han):
            return "zh-CN"
        return "ja"
    # Латиница неоднозначна, и `code_for_script("latn")` вернул бы `None`.
    # Здесь всё же `en`, и это осознанный размен: латиница в играх -
    # это английский, а `None` превратился бы в `auto` и попал бы в ключ
    # кеша, а кеш с `auto` внутри не работал (см. `cache_key`).
    #
    # Плата: французский источник пойдёт в бэкенд как `en→target`. На практике
    # этого почти не бывает: с новым фильтром «уже на языке назначения» текст
    # на французском при `target=fr` до бэкенда не доходит вовсе.
    if latin / total > 0.6:
        return code_for_script("latn") or "en"
    return None


# Punctuation a Chinese line uses and a Japanese one does not. Checked against the
# raw text, before the non-letters are dropped, because that is where it lives.
_CJK_CN_PUNCT = re.compile(r"[，。；：？！、《》【】“”‘’]")


# Simplified Chinese forms with no Japanese counterpart. Japanese writes 時, 認,
# 閉, 戦, 門, 問; the simplified forms of the same words do not exist in Japanese
# at all, so one of these appearing in Han-dominant kana-free text is a real
# signal rather than a shared kanji.
_CJK_CN_ONLY_CHARS = frozenset(
    "开关闭认说请让马门问间这时国图书画实学习汉讲话识语译训练记忆计划运动进出边过还样条线给红绿铁银钱钟针锁链镜难离万与专业东丝丢两严丧个为丽举义乌乐乔乡买乱争于亏云亚仅从仓价众优会伤伦体侠俩临么传变叙台叶号叹后向吓吗吨听启吴呐呕员呛呜咙咽响哑哒哓哔哗哙哜哝哟唛唝唠唡唢唤啧啬啭啮啰啴啸喷喽喾嗫嗳嘘嘤嘱噜嚣团园囱围囵圆圣坏块坚坛坜坝坞坟坠垄垅垆垒垦垧垩垫垭垯垱垲垴埘埙埚埝埯堑堕塆墙壮声壳壶壸处备复够头夸夹夺奂奋奖奥妆妇妈妩妪妫姗姜娄娅娆娇娈娱娲娴婳婴婵婶媪嫒嫔嫱嬷孙孪宁宝宠审宪宫宽宾寝对寻导寿将尔尘尝尧尴尸尽层屃屉届属屡屦屿岁岂岖岗岘岙岚岛岭岳岽岿峃峄峡峣峤峥峦崂崃崄崭嵘嵚嵛嵝嵴巅巍巯币帅师帏帐帜带帧帮帱帻帼幂幞干并广庄庆庐庑库应庙庞废庼廪异弃张弥弪弯弹强归当录彦彻径徕忏忧忾怀态怂怃怄怅怆怜总怼怿恋恒恳恸恹恺恻恼恽悦悫悬悯惊惧惨惩惫惬惭惮惯愠愤愦愿慑慭憷懑懒懔戆戋戏戗战戬户扑执扩扪扫扬扰抚抛抟抠抡抢护报担拟拢拣拥拦拧拨择挂挚挛挜挝挞挟挠挡挢挣挤挥挦捞损捡换捣据捻掳掴掷掸掺掼揽揿搀搁搂搅携摄摅摆摇摈摊撄撑撵撷撸撺擞攒敌敛数斋斓斗斩断无旧旷旸昙昼昽显晋晒晓晔晕晖暂暧札术朴机杀杂权来杨杩杰极构枞枢枣枥枧枨枪枫枭柜柠柽栀栅标栈栉栊栋栌栎栏树栖栾桊桠桡桢档桤桥桦桧桨桩梦梼梾检棂棁椁椟椠椤椭楼榄榇榈榉槚槛槟槠横樯樱橥橱橹橼檐檩欢欤欧歼殁殇残殒殓殚殡殴毁毂毕毙毡毵氇气氢氩氲汇汤汹沟没沣沤沥沦沧沨沩沪沵泞泪泶泷泸泺泻泼泽泾洁洒洼浃浅浆浇浈浉浊测浍济浏浐浑浒浓浔浕涂涌涛涝涞涟涠涡涢涣涤润涧涨涩淀渊渌渍渎渐渑渔渖渗温游湾湿溃溅溆溇滗滚滞滟滠满滢滤滥滦滨滩滪漤潆潇潋潍潜潴澜濑濒灏灭灯灵灾灿炀炉炖炜炝点炼炽烁烂烃烛烟烦烧烨烩烫烬热焕焖焘煅煳熘爱爷牍牦牵牺犊犟状犷犸犹狈狍狝狞独狭狮狯狰狱狲猃猎猕猡猪猫猬献獭玑玙玚玛玮环现玱玺珉珏珐珑珰珲琎琏琐琼瑶瑷璇璎瓒瓮瓯电畅畲畴疖疗疟疠疡疬疮疯疱疴痂痉痒痖痨痪痫痴瘅瘆瘗瘘瘪瘫瘾瘿癞癣癫皑皱皲盏盐监盖盗盘眍眦眬着睁睐睑瞒瞩矫矶矾矿砀码砖砗砚砜砺砻砾础硁硅硕硖硗硙硚确硷碍碛碜碱碹磙礼祎祢祯祷祸禀禄禅秃秆种积称秽秾稆税稣稳穑穷窃窍窑窜窝窥窦窭竖竞笃笋笔笕笺笼笾筑筚筛筜筝筹签简箓箦箧箨箩箪箫篑篓篮篱簖籁籴类籼粜粝粤粪粮糁糇紧絷纟纠纡纣纥约级纨纩纪纫纬纭纮纯纰纱纲纳纴纵纶纷纸纹纺纻纼纽纾绀绁绂组绅细织终绉绊绋绌绍绎经绐绑绒结绔绕绖绗绘绚绛络绝绞统绠绡绢绣绤绥绦继绨绩绪绫绬续绮绯绰绱绲绳维绵绶绷绸绹绺绻综绽绾缀缁缂缃缄缅缆缇缈缉缊缋缌缍缎缏缑缒缓缔缕编缗缘缙缚缛缜缝缞缟缠缡缢缣缤缥缦缧缨缩缪缫缬缭缮缯缰缱缲缳缴缵罂网罗罚罢罴羁羟翘翙翚耢耧耸耻聂聋职聍联聩聪肃肠肤肷肾肿胀胁胆胜胧胨胪胫胶脉脍脏脐脑脓脔脚脱脶脸腊腌腐腘腕腥腮腹腻腾腽膑臜舆舣舰舱舻艰艳艹艺节芈芗芜芦苁苇苈苋苌苍苎苏苘苹茎茏茑茔茕茧荆荐荙荚荛荜荞荟荠荡荣荤荥荦荧荨荩荪荫荬荭荮药莅莜莱莲莳莴莶获莸莹莺莼萚萝萤营萦萧萨葱蒇蒉蒋蒌蓝蓟蓠蓣蓥蓦蔷蔹蔺蔼蕲蕴薮藓藬蘑虏虑虚虫虬虮虽虾虿蚀蚁蚂蚕蚝蚬蛊蛎蛏蛮蛰蛱蛲蛳蛴蜕蜗蜡蝇蝈蝉蝎蝼蝾螀螨蟏衅衔补衬衮袄袅袆袜袭袯装裆裈裢裣裤裥褛褴襁襕见观觃规觅视觇览觉觊觋觌觍觎觏觐觑觞触觯詟誉誊讠订讣讥讦讧讨讪讫议讯讱讳讴讵讶讷许讹论讻讼讽设访诀证诂诃评诅诇诈诉诊诋诌词诎诏诐诒诓诔试诖诗诘诙诚诛诜诞诟诠诡询诣诤该详诧诨诩诪诫诬诮误诰诱诲诳诵诶诸诹诺读诼诽课诿谀谁谂调谄谅谆谇谈谊谋谌谍谎谏谐谑谒谓谔谕谖谗谘谙谚谛谜谝谞谟谠谡谢谣谤谥谦谧谨谩谪谫谬谭谮谯谰谱谲谳谴谵谶谷豮贝贞负贠贡财责贤败账货质贩贪贫贬购贮贯贰贱贲贳贴贵贶贷贸费贺贻贼贽贾贿赀赁赂赃资赅赆赇赈赉赊赋赌赍赎赏赐赑赒赓赔赕赖赗赘赙赚赛赜赝赞赟赠赡赢赣赪赵赶趋趱趸跃跄跖跞践跶跷跸跹跻踊踌踪踬踯蹑蹒蹰蹿躏躜躯车轧轨轩轪轫转轭轮软轰轱轲轳轴轵轶轷轸轹轺轻轼载轾轿辁辂较辄辅辆辇辈辉辊辋辌辍辎辏辐辑辒输辔辕辖辗辘辙辚辞辩辫辽达迁迈远违连迟迩迳迹适选逊递逦逻遗遥邓邝邬邮邹邺邻郁郄郏郐郑郓郦郧郸酂酄酝酦酱酽酾酿释里鉅鉴銮錾钅钆钇钉钊钋钌钍钎钏钐钑钒钓钔钕钖钗钘钙钚钛钜钝钞钠钡钢钣钤钥钦钧钨钩钪钫钬钭钮钯钰钲钳钴钵钶钷钸钹钺钻钼钽钾钿铀铂铃铄铅铆铈铉铊铋铌铍铎铏铐铑铒铕铗铘铙铚铛铜铝铞铟铠铡铢铣铤铥铧铨铪铫铬铭铮铯铰铱铲铳铴铵铷铸铹铺铻铼铽铿销锂锃锄锅锆锇锈锉锊锋锌锍锎锏锐锑锒锓锔锕锖锗错锚锛锜锝锞锟锠锡锢锣锤锥锦锨锩锪锫锬锭键锯锰锱锲锴锵锶锷锸锹锺锻锼锽锾锿镀镁镂镃镆镇镈镉镊镌镍镎镏镐镑镒镓镔镕镖镗镘镚镛镝镞镠镡镢镣镤镥镦镧镨镩镪镫镬镭镮镯镰镱镲镳镴镶长闩闪闫闬闯闰闱闲闳闵闶闷闸闹闺闻闼闽闾闿阀阁阂阃阄阅阆阇阈阉阊阋阌阍阎阏阐阑阒阓阔阕阖阗阘阙阚阛队阳阴阵阶际陆陇陈陉陕陧陨险随隐隶隽雏雠雳雾霁霉霭靓静靥鞑鞒鞯鞴韦韧韩韪韫韬韵页顶顷顸项顺须顼顽顾顿颀颁颂颃预颅领颇颈颉颊颋颌颍颎颏颐频颒颓颔颕颖颗题颙颚颛颜额颞颟颠颡颢颣颤颥颦颧风飏飐飑飒飓飔飕飖飗飘飙飚飞飨餍饤饥饦饧饨饩饪饫饬饭饮饯饰饱饲饳饴饵饶饷饸饹饺饻饼饽饾饿馀馁馂馃馄馅馆馇馈馉馊馋馌馍馎馏馐馑馒馓馔馕驭驮驯驰驱驳驴驵驶驷驸驹驺驻驼驽驾驿骀骁骂骄骅骆骇骈骉骊骋验骍骎骏骐骑骒骓骔骕骖骗骘骙骚骛骜骝骞骟骠骡骢骣骤骥骦骧髅髋髌鬓魇魉鱼鱽鱾鱿鲀鲁鲂鲄鲅鲆鲇鲈鲉鲊鲋鲌鲍鲎鲏鲐鲑鲒鲓鲔鲕鲖鲗鲘鲙鲚鲛鲜鲝鲞鲟鲠鲡鲢鲣鲤鲥鲦鲧鲨鲩鲪鲫鲬鲭鲮鲯鲰鲱鲲鲳鲴鲵鲶鲷鲸鲹鲺鲻鲼鲽鲾鲿鳀鳁鳂鳃鳄鳅鳆鳇鳈鳉鳊鳋鳌鳍鳎鳏鳐鳑鳒鳓鳔鳕鳖鳗鳘鳙鳛鳜鳝鳞鳟鳠鳡鳢鳣鸟鸠鸡鸢鸣鸤鸥鸦鸧鸨鸩鸪鸫鸬鸭鸮鸯鸰鸱鸲鸳鸴鸵鸶鸷鸸鸹鸺鸻鸼鸽鸾鸿鹀鹁鹂鹃鹄鹅鹆鹇鹈鹉鹊鹋鹌鹍鹎鹏鹐鹑鹒鹓鹔鹕鹖鹗鹘鹚鹛鹜鹝鹞鹟鹠鹡鹢鹣鹤鹦鹧鹨鹩鹪鹫鹬鹭鹯鹰鹱鹲鹳鹴鹾麦麸黄黉黡黩黪黾鼋鼍鼗鼹齄齐齑齿龀龁龂龃龄龅龆龇龈龉龊龋龌龙龚龛龟"
)


def _looks_chinese(han_text: str, han_count: int) -> bool:
    """Whether Han-dominant text with no kana reads as Chinese rather than Japanese.

    Plain Han with nothing distinguishing is Japanese. Chinese is concluded only
    from a simplified form with no Japanese counterpart, or from punctuation a
    Japanese line does not use. That is the actual difference between the two
    written forms, and it is why a kanji-only Japanese word stays Japanese.
    """
    if han_count <= 0:
        return False
    hits = sum(1 for c in han_text if c in _CJK_CN_ONLY_CHARS)
    if not hits:
        return False
    # In a short label a single distinctive form is enough. In a long stretch it
    # could be a shared kanji, so require a proportional share.
    return han_count <= 6 or hits * 2 >= han_count


def is_error_response(src: str, out: str, target: str = "") -> bool:
    """Whether ``out`` is obviously not a usable translation of ``src``.

    Only what the answer says about itself, plus the two cases that are failures
    whatever the target language is: nothing at all, or the input repeated back.

    Judging by the alphabet of the answer is not a test. A Russian translation of
    an English game line can legitimately contain a Japanese name, and declaring
    that an error means the user sees no translation at all for a request that
    worked - which is what "English to Japanese is broken" actually was: a correct
    answer, refused, with nothing to show for it.
    """
    if not out or not out.strip():
        return True
    a = re.sub(r"\W+", "", src.lower())
    b = re.sub(r"\W+", "", out.lower())
    if a and a == b and any(c.isalpha() for c in src):
        # Identical is a failure only when there was something to translate. A
        # counter, a percentage and an arrow come back as themselves and that
        # is the right answer, not the input repeated back at us.
        return True
    # A backend that has no answer says so. These are the provider's own words,
    # not an inference about the script it replied in.
    lowered = out.lower()
    if "unable to translate" in lowered or "invalid language" in lowered:
        return True
    if "translation failed" in lowered or "no translation" in lowered:
        return True
    # Known provider error phrases that happen to be valid English but
    # semantically indicate failure for any non-matching input.
    if "server error" in lowered or "service unavailable" in lowered:
        return True
    if not looks_like_a_translation(src, out):
        return True
    return False


# Letters a language is written with. Not a judgement about the answer's
# quality - a translation may legitimately contain a name from the source - but
# the floor below which an answer is not a rendering of words at all.
_WORD_RE = re.compile(r"[\w\u0400-\u04ff\u3040-\u30ff\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").casefold())


def _gibberish_score(text: str) -> float:
    """How much of a string is noise rather than words.

    Measured on the letters, because punctuation and spacing are the
    recogniser's business and a real translation has them in normal proportion.
    Three things count as noise: a token with no vowel at all where the language
    has vowels, a token where the same letter repeats, and a token long enough
    to be a sentence fragment that is mostly one letter.
    """
    tokens = _tokens(text)
    if not tokens:
        return 1.0
    # Only letters count, and digits are not letters: a number cannot be
    # consonant noise however it is written, and counting it as such made every
    # stat line look like a machine artefact.
    letters = [c for t in tokens for c in t if c.isalpha()]
    if not letters:
        return 0.0
    bad = 0
    vowels = set("aeiouyаеёиоуыэюя")
    for t in tokens:
        low = t
        if _KANA_RE.search(low) or _HANGUL_RE.search(low):
            # Kana and hangul syllables carry their own vowels; scoring them as
            # consonants would make every Japanese or Korean answer look like
            # noise, which is the failure of judging by the alphabet back.
            continue
        # A token with a digit in it is a number: "125", "120/120", "14820". It
        # has no vowel and it never will, and it is the most ordinary thing on a
        # game screen. Counting it as noise threw away every stat line, which is
        # what the first run of this cleaned out - 142 entries, most of them
        # correct translations of counters.
        if any(c.isdigit() for c in low):
            continue
        if len(set(low) & vowels) == 0:
            # No vowel at all: a consonant run is not a word in any of the
            # languages this handles, at any length. `qjkz` is not a word
            # either, and a three-letter threshold let it through.
            bad += len(low)
        elif len(set(low)) <= max(1, len(low) // 3):
            bad += len(low)
    if not letters:
        return 0.0
    return bad / float(len(letters))


# Above this, an answer is noise. Generous on purpose: a translation with a
# proper noun in it will not come close, and the cost of being wrong in this
# direction is one more request rather than a wrong sentence on screen forever.
GIBBERISH_MAX = 0.34


def leaves_source_untranslated(src: str, out: str) -> bool:
    """Whether an answer is the source with a word or two swapped for Russian.

    A backend asked for a whole line and given a line back with three words of
    it still in English has not translated the line, and the answer reads as a
    finished one: it has Russian in it, it has no consonants piled up, it has
    the right length. Every other test here passes it, so it was stored and
    drawn over every frame - which is what five rows of UI labels looked like,
    `NEW ILLUSTRATION IS NOW UNLOCKED!!` coming back as
    `ИЛЛЮСТРАЦИЯ IS NOW UNLOCKED!!` with the first word gone and the last three
    in English.

    Both conditions are needed and neither is enough. Some Russian has to be in
    the answer, or the line was simply not translated at all and that is a
    different question answered elsewhere. And the source has to be long enough
    that "most of it came back" means something: on `Press E to continue` the
    one shared word is the key name and three out of five would be ordinary.

    A source already in the target script is never this: nothing is expected to
    change, and a line of Russian compared with itself is every word verbatim.
    """
    src_t = _tokens(src)
    out_t = set(_tokens(out))
    if len(src_t) < 3 or not out_t:
        return False
    # Ничего не переведено - это не про этот случай.
    if not re.search(r"[\u0400-\u04ff\u3040-\u30ff\u4e00-\u9fff]", out):
        return False
    # Источник уже на том же алфавите: сравнивать его с собой бессмысленно.
    if re.sub(r"[^A-Za-z]", "", src) == "" and re.search(r"[\u0400-\u04ff]", src):
        return False
    kept = sum(1 for t in src_t if t in out_t)
    # Порог 80% вместо 60%: в игровом UI часто оставляют термины на английском
    # (Planned, Hidden, Purchase и др.), и это не значит, что перевод плохой.
    # 80% = 4/5 — допускает один непереведённый токен на пять.
    return kept * 5 >= len(src_t) * 4


def looks_like_a_translation(src: str, out: str, target: str = "") -> bool:
    """Whether ``out`` reads as words rather than as noise.

    The error check above asks what the answer says about itself, and this asks
    what it is. A backend that answers a game line with a stream of consonants
    says nothing wrong and is not a translation, and nothing in the pipeline
    noticed: the answer was stored, and every later frame of that line read the
    same bad answer out of the cache and drew it. That is the "one bad
    translation and then it forever" case, and it was a cache holding a wrong
    value rather than a cache being wrong.
    """
    if not out or not out.strip():
        return False
    src_tokens = _tokens(src)
    out_tokens = _tokens(out)
    if not out_tokens:
        # No letters anywhere in the answer: an arrow, a percentage, a glyph.
        return not any(c.isalpha() for c in src)
    # Nothing to judge: the line has no letters in it either. A counter, a
    # percentage and an arrow are not mistranslations, and a detector that says
    # otherwise will throw away every number on the screen.
    # A line that is only a number, a symbol, or a mix of them has nothing to
    # be mistranslated: "Gold: 125" is a label and a count, and a detector that
    # reads it as noise will not translate the whole HUD.
    if not any(c.isalpha() for c in re.sub(r"\d+", "", src)):
        return True
    if _gibberish_score(out) > GIBBERISH_MAX:
        return False
    # Half a line is not a line. Проверяется после шума, а не до: ответ из одних
    # согласныхных должен отсеяться этим, иначе причина не та.
    if leaves_source_untranslated(src, out):
        return False
    # A placeholder that came back is not a translation. This is what happens
    # when a protected name reaches the backend and the backend declines to say
    # so: the placeholder is the most word-like thing in the answer, and without
    # this it was stored and drawn.
    if out.strip() in {"PATH0", "PATH1"} or "PATH0" in out:
        return False
    # The other placeholder this pipeline uses: a bare index in lenticular
    # brackets. It is not Japanese - kana are in that range, the bracket
    # characters are not, and a protected name that the backend declined to
    # translate comes back exactly like this.
    if re.fullmatch(r"[\u3010\u3011\uff3b\uff3d\[\]\u250f]+\d*[\u3011\u250f\]\uff3d]*", out.strip()):
        return False
    # A long answer to a short line, with nothing of the source in it, is not a
    # rendering of that line.
    if src_tokens and len(out_tokens) > 6 and len(src_tokens) >= 2:
        overlap = sum(1 for t in src_tokens if t in out_tokens)
        if overlap == 0 and len(out) > 3 * max(4, len(src)):
            return False
    # Consonant runs. "asdkjh" has a vowel in it and is still not a word: what
    # gives it away is the shape - six letters with the vowels scattered
    # instead of placed. A word in any of these languages has a vowel every few
    # letters, and a real translation does not come back as a run of them.
    long_tokens = [t for t in out_tokens if len(t) >= 5]
    if long_tokens:
        crowded = sum(1 for t in long_tokens if _is_consonant_crowded(t))
        if crowded * 2 >= len(long_tokens):
            return False
    return True


_CONSONANT_CROWDED = re.compile(r"[^aeiouyаеёиоуыэюя]{4,}", re.IGNORECASE)
# Kana carry their own vowels, so a run of them is a run of syllables and not a
# run of consonants. Testing Japanese as if it were written in consonants calls
# every Japanese word a machine artefact, and refuses answers that are right -
# which is the same failure as judging a translation by the alphabet it came
# back in.
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fff]")
# Hangul syllables carry their own vowels the way kana do, for the same reason.
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")


def _is_consonant_crowded(token: str) -> bool:
    """Whether a token runs consonants together the way noise does.

    Four in a row is the threshold, and it is a shape test rather than a
    dictionary: a real Russian word has no such run, and neither does a real
    English one, while a machine producing noise produces them constantly.
    A token with kana in it is not tested at all - see _KANA_RE.
    """
    if _KANA_RE.search(token) or _HANGUL_RE.search(token):
        return False
    return bool(_CONSONANT_CROWDED.search(token))


def cache_key(text: str, source: str, target: str) -> str:
    """Cache key includes the language pair, not just the source text."""
    return f"{source}\x1f{target}\x1f{text.strip()}"


def _glossary_version(glossary: dict[str, str]) -> str:
    import hashlib

    blob = "\n".join(f"{k}\t{v}" for k, v in sorted(glossary.items()))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _Cache(path, max_entries, **kwargs):  # noqa: N802
    """Кэш на SQLite. Имя оставлено: тесты и переводчик зовут его так."""
    from .translation.cache import SqliteCache

    return SqliteCache(path, max_entries, **kwargs)


# explicit miss when offline_only and no local pack.
LANGUAGE_PACK_NOT_INSTALLED = "language pack not installed"


class Translator:
    """Translate text, with a cache and a rate-limit aware endpoint."""

    def __init__(
        self,
        target: str = "ru",
        source: str = "auto",
        *,
        use_gtx: bool = True,
        allow_slow: bool = False,
        offline_only: bool = False,
        max_chars: int = 4300,
        cooldown_s: float = 25.0,
        cache_path: Path | None = None,
        cache_max_entries: int = 4000,
        glossary: dict[str, str] | None = None,
        log: Callable[[str], None] | None = None,
        session_factory: Callable[[], object] | None = None,
    ) -> None:
        self.target = target or "ru"
        self.source = source or "auto"
        self.offline_only = bool(offline_only)
        # Strict offline never constructs an HTTP client — even if use_gtx is True.
        self.use_gtx = False if self.offline_only else use_gtx
        self.allow_slow = False if self.offline_only else allow_slow
        self.max_chars = max_chars
        self.cooldown_s = cooldown_s
        self.glossary = {str(k): str(v) for k, v in (glossary or {}).items()}
        self.glossary_version = _glossary_version(self.glossary)
        self._log = log or (lambda _msg: None)
        self._session_factory = None if self.offline_only else session_factory
        self._session = None
        self._cooldown_until = 0.0  # monotonic: immune to clock changes
        self._cache = _Cache(
            cache_path,
            cache_max_entries,
            glossary_version=self.glossary_version,
        )
        self._lock = threading.RLock()
        if self.offline_only:
            self._log_msg("offline_only=true (online backends not instantiated)")

    # -- plumbing ---------------------------------------------------------
    def _log_msg(self, msg: str) -> None:
        self._log(msg)

    def _http(self) -> object | None:
        if self.offline_only:
            return None
        with self._lock:
            if self._session is None:
                if self._session_factory is not None:
                    self._session = self._session_factory()
                else:
                    try:
                        import requests

                        s = requests.Session()
                        s.headers.update({"User-Agent": "Mozilla/5.0 kizurium-translator"})
                        self._session = s
                    except ImportError:
                        return None
            return self._session

    def cache_get(self, text: str, source: str | None = None) -> str:
        """Cache read.

        Without a resolved source this is a best-effort lookup, and "auto" is
        never a key: it is what is written when the source has not been decided
        yet, and reading it back under that name only ever matched itself.
        """
        return self._cache.get(cache_key(text, source or self.source, self.target))

    def cache_put(self, text: str, translated: str, source: str | None = None) -> None:
        self._cache.put(
            cache_key(text, source or self.source, self.target), translated
        )

    def cache_lookup(self, text: str, source: str) -> str:
        """Cache read for an already-resolved source language."""
        return self._cache.get_matching(
            cache_key(text, source, self.target), self.glossary_version
        )

    def cache_store(self, text: str, translated: str, source: str) -> None:
        self._cache.put(
            cache_key(text, source, self.target),
            translated,
            glossary_version=self.glossary_version,
        )
        self._remember(text, translated, source, "gtx")

    def cache_forget(self, text: str, source: str) -> bool:
        """Forget one translation, and make sure the removal survives the exit."""
        return self._cache.forget(cache_key(text, source, self.target))

    def cache_flush(self, force: bool = True) -> None:
        self._cache.flush(force=force)

    # -- backends ---------------------------------------------------------
    def via_gtx(self, text: str, source: str | None = None) -> str:
        """The free ``translate_a`` endpoint. Rate limits, so it backs off."""
        if self.offline_only or not self.use_gtx:
            return ""
        if not (text or "").strip():
            return ""
        now = time.monotonic()
        with self._lock:
            if now < self._cooldown_until:
                return ""
        http = self._http()
        if http is None:
            return ""
        from .translation.online import gtx_request
        from .translation.scheduler import SCHEDULER

        try:
            with SCHEDULER.online():
                resp = gtx_request(http, text, source or self.source, self.target)
        except Exception as exc:  # noqa: BLE001 - any transport error is a miss
            self._log_msg(f"gtx-fail {type(exc).__name__}")
            return ""
        if getattr(resp, "status_code", 0) == 429:
            with self._lock:
                self._cooldown_until = time.monotonic() + self.cooldown_s
            self._log_msg(f"gtx=429 cooldown={self.cooldown_s:.0f}s")
            return ""
        try:
            if resp.status_code != 200:  # type: ignore[attr-defined]
                return ""
            data = resp.json()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return ""
        if not data or not data[0]:
            return ""
        return "".join(part[0] for part in data[0] if part and part[0]).strip()

    def via_local(self, text: str, source: str | None = None, *, beam_size: int = 1) -> str:
        """CTranslate2 OPUS-MT pack, when installed"""
        if not (text or "").strip():
            return ""
        src = source or self.source or "auto"
        if src == "auto":
            src = self.resolve_source(text)
            if src == "auto":
                src = "en"
        try:
            from .translation.local_mt import local_backend_for

            backend = local_backend_for(src, self.target, beam_size=beam_size)
            if backend is None:
                return ""
            got = backend.translate([text], src, self.target)[0]
            if got:
                self._log_msg(f"local-mt {src}->{self.target} via ctranslate2")
            return got.strip() if got else ""
        except Exception as exc:  # noqa: BLE001
            self._log_msg(f"local-mt-fail {type(exc).__name__}")
            return ""

    def gtx_cooling(self) -> bool:
        """True while gtx is backing off after a 429."""
        with self._lock:
            return time.monotonic() < self._cooldown_until

    def _slow_translate(self, text: str, source: str) -> str:
        """Медленные бэкенды: MyMemory и (если Google не в бане) deep-translator."""
        if self.offline_only:
            return ""
        from .translation.online import slow_backends

        cooling = self.gtx_cooling()
        for backend in slow_backends(skip_google=cooling):
            if not backend.supports(source, self.target):
                continue
            try:
                got = backend.translate([text], source, self.target)[0]
            except Exception as exc:  # noqa: BLE001
                self._log_msg(f"{backend.backend_id}-fail {type(exc).__name__}")
                continue
            if got:
                if cooling:
                    self._log_msg(f"gtx-cooldown → {backend.backend_id}")
                return got.strip()
        return ""

    # -- public API -------------------------------------------------------
    def glossary_hit(self, text: str) -> str:
        return self.glossary.get(text.strip(), "")

    def resolve_source(self, text: str) -> str:
        """Source language for ``text``, from config or detection.

        When neither decides, this is ``auto``, not English. Short strings, pure
        symbols and anything else detection has no opinion about were being
        announced to the backend as English, which is a guess presented as a fact:
        the backend would have detected them correctly on its own.
        """
        if self.source and self.source != "auto":
            return self.source
        return detect_lang(text) or "auto"

    def translate(self, text: str, *, source: str | None = None, allow_slow: bool | None = None) -> str:
        """Translate one string. Returns ``""`` when nothing usable came back."""
        text = (text or "").strip()
        if not text:
            return ""
        if len(text) > self.max_chars:
            return ""

        hit = self.glossary_hit(text)
        if hit:
            return hit

        src = source or self.resolve_source(text)
        # Everything is stored and read under the resolved source, never under
        # "auto". Storing under "auto" and reading under "en" meant the cache never
        # hit for the default configuration, which is every real use of it.
        key = cache_key(text, src, self.target)
        cached = self._cache.get_matching(key, self.glossary_version)
        if cached:
            if (
                not is_error_response(text, cached, self.target)
                and not translation_dropped_tail(text, cached)
            ):
                return cached
            self._cache.forget(key)

        # Translation memory is a curation surface, separate from the hot cache.
        # A hit here still is not a glossary entry until the user promotes it.
        try:
            from .lexicon.translation_memory import default_memory

            remembered = default_memory().lookup(text, source_lang=src, target_lang=self.target)
            if remembered and (
                is_error_response(text, remembered, self.target)
                or translation_dropped_tail(text, remembered)
            ):
                # Prior local MT wrote junk (ПРЮ / truncated) into TM — drop it.
                self._log_msg("tm-drop bad-memory")
                try:
                    default_memory().forget(
                        text, source_lang=src, target_lang=self.target
                    )
                except Exception:  # noqa: BLE001
                    pass
                remembered = ""
            if remembered and not is_error_response(text, remembered, self.target):
                self._cache.put(key, remembered, glossary_version=self.glossary_version)
                return remembered
        except Exception:  # noqa: BLE001
            pass

        # Paths first: a file name is a name, and the backend is confident that
        # `bin` means a wastepaper basket and that `install` is a verb. A name it
        # cannot translate is a wrong answer, not a missing one.
        # Names first, paths second. A path placeholder is not a capitalised
        # word, so running the name pass afterwards swallowed it as a name and
        # put the literal string "PATH0" back on screen instead of the file name.
        protected, names = protect_proper_nouns(text)
        protected, path_tokens = protect_paths(protected)
        # Interior punctuation last, after names and paths: a name may contain a
        # dash and a path certainly does, and protecting punctuation first would
        # split both in half.
        protected, skeleton = build_punctuation_skeleton(protected)
        # Local OPUS-MT first when a pack is installed — keeps live offline-capable
        # and avoids burning gtx quota on strings the pack can handle.
        got = self.via_local(protected, src, beam_size=1)
        backend_id = "ctranslate2"
        if got and names and not placeholders_survived(got, names):
            # Local ate ZZNAMEnZZ / 【n】 (e.g. 【0】→ПРЮ). Do not store that.
            self._log_msg("local-drop placeholder-lost")
            got = ""
        if got and translation_dropped_tail(text, got):
            # Kept the first clause, dropped "No matter…" — English ghosts under card.
            self._log_msg("local-drop truncated-tail")
            got = ""
        if not got and self.offline_only:
            # Better than catching network errors: online backends were never built.
            self._log_msg(f"{LANGUAGE_PACK_NOT_INSTALLED} ({src}->{self.target})")
            return ""
        if not got:
            got = self.via_gtx(protected, src)
            backend_id = "gtx"
        if got and names and not placeholders_survived(got, names):
            self._log_msg(f"{backend_id}-drop placeholder-lost")
            got = ""
        if got and skeleton and not skeleton_survived(got, skeleton):
            # a dropped token means the punctuation it stood in for is
            # gone, and a card with a missing colon is not a translation of the
            # line above it. Reject and let the next backend try.
            self._log_msg(f"{backend_id}-drop skeleton-lost")
            got = ""
        if got and names:
            got = restore_proper_nouns(got, names).strip()
        if got and path_tokens:
            got = restore_paths(got, path_tokens).strip()
        if got and skeleton:
            got = restore_punctuation_skeleton(got, skeleton).strip()
        # A row that was nothing but a path comes back as the path and nothing
        # else, which is not a translation - it is the backend saying it has no
        # answer. Reporting it as one put a bare "PATH0" on screen, and it is
        # also what would make a real translation of a sentence look unchanged.
        if got and normalize_for_compare(got) == normalize_for_compare(text):
            got = ""
        if got and not is_error_response(text, got, self.target):
            self._cache.put(key, got, glossary_version=self.glossary_version)
            self._cache.flush()
            self._remember(text, got, src, backend_id)
            return got

        # gtx cooldown (429) is not "user opted into slow": it is Google saying
        # stop. Without a fallback the overlay only shows glossary hits, which
        # looks like "translations disappeared". MyMemory covers that gap.
        # offline_only never reaches here for local misses (early return above).
        if self.offline_only:
            return ""
        slow = self.allow_slow if allow_slow is None else allow_slow
        if slow or self.gtx_cooling() or not self.use_gtx:
            got = self._slow_translate(protected, src)
            backend_id = "mymemory"
            # The slow path used to restore names and paths but never check that
            # the tokens had survived, so MyMemory could eat one and the result
            # went into the cache with a `PATH0` still in it. Verified here for
            # the same reason the fast path verifies: a lost token is a lost
            # character.
            if got and names and not placeholders_survived(got, names):
                self._log_msg("mymemory-drop placeholder-lost")
                got = ""
            if got and skeleton and not skeleton_survived(got, skeleton):
                self._log_msg("mymemory-drop skeleton-lost")
                got = ""
            if got and names:
                got = restore_proper_nouns(got, names).strip()
            if got and path_tokens:
                got = restore_paths(got, path_tokens).strip()
            if got and skeleton:
                got = restore_punctuation_skeleton(got, skeleton).strip()
            if got and normalize_for_compare(got) == normalize_for_compare(text):
                got = ""
            if got and not is_error_response(text, got, self.target):
                self._cache.put(key, got, glossary_version=self.glossary_version)
                self._cache.flush()
                self._remember(text, got, src, backend_id)
                return got
        return ""

    def _remember(self, source_text: str, translated: str, source_lang: str, backend_id: str) -> None:
        try:
            from .lexicon.translation_memory import default_memory

            default_memory().remember(
                source_text,
                translated,
                source_lang=source_lang,
                target_lang=self.target,
                backend_id=backend_id,
            )
        except Exception:  # noqa: BLE001
            pass
