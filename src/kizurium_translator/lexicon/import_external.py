"""Импорт внешних словарей в формат пачек kizurium

Поддерживаемые источники (только то, что пользователь положил сам):

- XUnity AutoTranslator ``*.txt``: ``source=target``, ``r:pattern=repl``,
  ``sr:pattern=repl``;
- простой текст TENUKI/RPG-style: ``source=target`` или TSV ``source\\ttarget``.

Copyrighted full game scripts сюда не бандлятся. Импорт пишет пачку в
``XDG_DATA_HOME/.../dictionaries/<pack-id>/`` и требует, чтобы пользователь
сам подтвердил право на использование файла.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .pack_id import is_safe_pack_id, safe_pack_dir
from .regex_rules import RegexDictionaryError, validate_pattern


@dataclass
class ImportResult:
    pack_dir: Path
    exact: int = 0
    regex: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


_XUNITY_REGEX = re.compile(r"^(?:r|sr):(.+?)=(.*)$", re.DOTALL)
_PLAIN_EQ = re.compile(r"^(.+?)=(.*)$", re.DOTALL)


def _unescape_xunity(text: str) -> str:
    # XUnity stores some escapes; keep a conservative subset.
    return (
        text.replace("\\n", "\n")
.replace("\\r", "\r")
.replace("\\t", "\t")
.replace("\\=", "=")
    )


def _normalize_pattern(pattern: str) -> str:
    """Move inline flags before ``^`` when XUnity wrote ``^(?i)...``.

    Python 3.11+ rejects global flags after the start of the expression.
    """
    m = re.match(r"^(\^)(\(\?[aiLmsux]+\))(.*)$", pattern)
    if m:
        return f"{m.group(2)}{m.group(1)}{m.group(3)}"
    return pattern


def _normalize_repl(repl: str) -> str:
    """XUnity uses ``$1`` group refs; ``re.sub`` wants ``\\1``."""
    return re.sub(r"\$(\d+)", r"\\\1", repl)


def parse_xunity_lines(text: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """Return (exact pairs, regex pairs, skip notes)."""
    exact: list[tuple[str, str]] = []
    regex: list[tuple[str, str]] = []
    notes: list[str] = []
    for raw in text.splitlines():
        line = raw.strip("\ufeff").rstrip("\n")
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            # Scene / directive markers — out of scope for our pack format.
            notes.append(f"directive skipped: {line[:60]}")
            continue
        m = _XUNITY_REGEX.match(line)
        if m:
            pattern = _normalize_pattern(_unescape_xunity(m.group(1)))
            repl = _normalize_repl(_unescape_xunity(m.group(2)))
            try:
                validate_pattern(pattern)
            except RegexDictionaryError as exc:
                notes.append(f"regex rejected: {exc}")
                continue
            regex.append((pattern, repl))
            continue
        m = _PLAIN_EQ.match(line)
        if m:
            src, dst = _unescape_xunity(m.group(1)), _unescape_xunity(m.group(2))
            if src and dst and src != dst:
                exact.append((src, dst))
            else:
                notes.append("empty or identity exact skipped")
            continue
        notes.append(f"unparsed: {line[:60]}")
    return exact, regex, notes


def parse_plain_dictionary(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """TENUKI / generic ``source=target`` or TSV ``source\\ttarget``."""
    exact: list[tuple[str, str]] = []
    notes: list[str] = []
    for raw in text.splitlines():
        line = raw.strip("\ufeff").rstrip("\n")
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] and parts[1] and parts[0] != parts[1]:
                exact.append((parts[0], parts[1]))
            else:
                notes.append("bad tsv row skipped")
            continue
        m = _PLAIN_EQ.match(line)
        if m:
            src, dst = m.group(1), m.group(2)
            if src and dst and src != dst:
                exact.append((src, dst))
            else:
                notes.append("empty or identity exact skipped")
            continue
        notes.append(f"unparsed: {line[:60]}")
    return exact, notes


def detect_format(text: str, hint: str | None = None) -> str:
    if hint in ("xunity", "tenuki", "plain"):
        return "plain" if hint == "tenuki" else hint
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        if s.startswith("r:") or s.startswith("sr:"):
            return "xunity"
    return "plain"


def write_pack(
    pack_dir: Path,
    *,
    pack_id: str,
    exact: list[tuple[str, str]],
    regex: list[tuple[str, str]],
    scope: str = "user",
    source_language: str = "auto",
    target_language: str = "ru",
    source_note: str = "",
) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = (
        f'id = "{pack_id}"\n'
        f'version = "1"\n'
        f'source_language = "{source_language}"\n'
        f'target_language = "{target_language}"\n'
        f'scope = "{scope}"\n'
        f'license = "user-provided"\n'
        f'source_url = ""\n'
        f'import_note = "{source_note.replace(chr(34), "")}"\n'
    )
    (pack_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
    if exact:
        lines = ["source\ttarget\tscope\tpriority\tflags"]
        for src, dst in exact:
            # Tabs inside cells break TSV; refuse rather than corrupt the pack.
            if "\t" in src or "\t" in dst or "\n" in src or "\n" in dst:
                continue
            lines.append(f"{src}\t{dst}\t{scope}\t50\t")
        (pack_dir / "terms.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if regex:
        lines = ["pattern\treplacement\tscope\tpriority"]
        for pattern, repl in regex:
            if "\t" in pattern or "\t" in repl:
                continue
            lines.append(f"{pattern}\t{repl}\t{scope}\t50")
        (pack_dir / "regex.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def import_dictionary_file(
    source: Path,
    *,
    pack_id: str | None = None,
    dest_root: Path | None = None,
    fmt: str | None = None,
    scope: str = "user",
) -> ImportResult:
    """Import one external dictionary file into a user pack directory."""
    text = source.read_text(encoding="utf-8", errors="replace")
    kind = detect_format(text, fmt)
    if kind == "xunity":
        exact, regex, notes = parse_xunity_lines(text)
    else:
        exact, notes = parse_plain_dictionary(text)
        regex = []
    from ..paths import default_paths

    root = dest_root or default_paths().dictionaries_dir
    # The sanitiser used to allow `.`, so a file called `...zip` produced a stem of
    # `..` and `root / ".."` was the parent of the dictionaries directory. Both
    # this and an explicit `--pack-id` now go through the same check.
    pid = pack_id or re.sub(r"[^a-zA-Z0-9._-]+", "-", source.stem).strip("-").lower() or "imported"
    # A generated name is a *guess* at a name, not user intent, so an unusable
    # guess falls back to a fixed id rather than failing the import. An explicit
    # `--pack-id` is user intent and is refused loudly: silently renaming what
    # somebody asked for is worse than telling them it was not acceptable.
    if not is_safe_pack_id(pid):
        # `safe_pack_dir` raises for an explicit id, which is the point: silently
        # renaming what somebody asked for is worse than refusing it. A generated
        # name is only a guess, so that gets a fixed id rather than a failed import.
        if pack_id:
            safe_pack_dir(root, pid)  # raises UnsafePackId
        pid = "imported"
    pack_dir = safe_pack_dir(root, pid)
    write_pack(
        pack_dir,
        pack_id=pid,
        exact=exact,
        regex=regex,
        scope=scope,
        source_note=f"imported from {source.name} as {kind}",
    )
    return ImportResult(
        pack_dir=pack_dir,
        exact=len(exact),
        regex=len(regex),
        skipped=len(notes),
        warnings=notes[:20],
    )
