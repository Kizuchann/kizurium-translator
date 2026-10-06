"""Regex dictionaries

Pack file ``regex.tsv``:

```text
pattern<TAB>replacement<TAB>scope<TAB>priority
```

Compiled once at pack load. Malformed patterns and shapes that invite
catastrophic backtracking are rejected rather than applied. Scope and priority
match the exact-term rules: ``user`` > ``profile`` > ``game`` > ``global``.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .store import DATA_ROOT, SCOPE_RANK, _pack_id, _user_dictionary_roots

# Nested quantifiers and open-ended repetition on quantified groups are the
# classic ReDoS shapes. A pack that ships them is refused at load, not at match.
_REDOS_HINT = re.compile(
    r"\([^)]*[+*][^)]*\)[+*]"  # (a+)+ / (a*)*
    r"|\([^)]*[+*][^)]*\)\{"  # (a+){2,}
    r"|([^\\]|^)(\.\*){2,}"  #.*.*
    r"|([^\\]|^)\.\+\+"  #.++
)

_MAX_PATTERN_LEN = 200


@dataclass(frozen=True)
class RegexRule:
    pattern: str
    replacement: str
    scope: str
    priority: int
    pack: str
    compiled: re.Pattern[str]


class RegexDictionaryError(ValueError):
    """A pack rule failed validation and must not be loaded."""


def validate_pattern(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern`` or raise ``RegexDictionaryError``."""
    text = (pattern or "").strip()
    if not text:
        raise RegexDictionaryError("empty pattern")
    if len(text) > _MAX_PATTERN_LEN:
        raise RegexDictionaryError(f"pattern longer than {_MAX_PATTERN_LEN}")
    if _REDOS_HINT.search(text):
        raise RegexDictionaryError(f"refusing catastrophic pattern: {text!r}")
    try:
        return re.compile(text)
    except re.error as exc:
        raise RegexDictionaryError(f"malformed regex {text!r}: {exc}") from exc


def _read_regex_tsv(path: Path, pack: str) -> list[RegexRule]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[RegexRule] = []
    for i, line in enumerate(lines):
        if not line.strip() or (i == 0 and line.lower().startswith("pattern\t")):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        pattern, replacement, scope, priority = parts[:4]
        try:
            prio = int(priority)
        except ValueError:
            prio = 0
        compiled = validate_pattern(pattern)
        out.append(
            RegexRule(
                pattern=pattern,
                replacement=replacement,
                scope=scope or "global",
                priority=prio,
                pack=pack,
                compiled=compiled,
            )
        )
    return out


def load_regex_rules(*extra_roots: Path) -> tuple[RegexRule, ...]:
    """All pack regex rules from the repository and optional user roots."""
    found: list[RegexRule] = []
    roots = [DATA_ROOT, *extra_roots]
    seen: set[tuple[str, str, str]] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for manifest in sorted(root.rglob("manifest.toml")):
            directory = manifest.parent
            pack = _pack_id(directory)
            path = directory / "regex.tsv"
            if not path.is_file():
                continue
            for rule in _read_regex_tsv(path, pack):
                key = (rule.pattern, rule.scope, rule.pack)
                if key in seen:
                    continue
                seen.add(key)
                found.append(rule)
    return tuple(found)


_LOCK = threading.Lock()
_CACHE: tuple[RegexRule, ...] | None = None


def rules(extra_roots: tuple[Path, ...] | None = None) -> tuple[RegexRule, ...]:
    """Compiled rules, loaded once (plus explicit roots for tests)."""
    global _CACHE
    if extra_roots is not None:
        return load_regex_rules(*extra_roots)
    with _LOCK:
        if _CACHE is None:
            _CACHE = load_regex_rules(*_user_dictionary_roots())
        return _CACHE


def clear_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def _script_compatible(rule: RegexRule, text: str) -> bool:
    """Skip a Latin-only rule on CJK-only text and the reverse.

    Scope alone is not enough: a Japanese dialogue line must not be rewritten
    by an English ``Chapter N`` pattern that happens to match a digit run.
    """
    has_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]", text))
    has_lat = bool(re.search(r"[A-Za-z]", text))
    pat_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]", rule.pattern))
    pat_lat = bool(re.search(r"[A-Za-z]", rule.pattern))
    if pat_lat and not pat_cjk and has_cjk and not has_lat:
        return False
    if pat_cjk and not pat_lat and has_lat and not has_cjk:
        return False
    return True


def _scope_ok(rule: RegexRule, *, game_on: bool, game: str | None) -> bool:
    if rule.scope == "game":
        if game and rule.pack != game:
            return False
        if not game and not game_on:
            return False
        return True
    if rule.scope == "global":
        return True
    if rule.scope in ("user", "profile"):
        if rule.scope == "profile" and not game_on:
            return False
        return True
    return False


def apply_regex(
    text: str,
    *,
    game_on: bool = False,
    game: str | None = None,
    ruleset: tuple[RegexRule, ...] | None = None,
) -> str | None:
    """Apply the highest-priority matching rule, or ``None`` if none fit."""
    raw = (text or "").strip()
    if not raw:
        return None
    rows = list(ruleset if ruleset is not None else rules())
    rows.sort(key=lambda r: (SCOPE_RANK.get(r.scope, 0), r.priority, r.pattern), reverse=True)
    for rule in rows:
        if not _scope_ok(rule, game_on=game_on, game=game):
            continue
        if not _script_compatible(rule, raw):
            continue
        try:
            new, n = rule.compiled.subn(rule.replacement, raw, count=1)
        except re.error:
            continue
        if n and new and new != raw:
            return new
    return None
