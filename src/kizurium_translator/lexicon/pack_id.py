"""A pack id is a name, not a path.

Pack paths have to stay under their XDG directory. The id arrives
from a command line - `--import-dictionary --pack-id...`, and the name a
promotion from translation memory is given - and it was joined straight onto the
dictionaries directory. `--pack-id../../evil` then wrote a manifest and a terms
file outside `dictionaries_dir`, which is a write primitive driven by an argument
and nothing else.

The rule is the strict one rather than "strip the dots": a pack id is an
identifier, it never needs a separator, so anything that is not an identifier
character is refused outright. The error names the offending id rather than
echoing the whole path, so a hostile id does not end up in a log line either.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_PACK_ID = 64

_PACK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class UnsafePackId(ValueError):
    """A pack id that is not an identifier."""


def is_safe_pack_id(pack_id: str) -> bool:
    """Whether a pack id may be used as a directory name.

    Checks the characters, not the resolved path. A traversal like `..` fails on
    the first character rule rather than on a containment test that would have to
    trust `resolve()` to notice.
    """
    raw = str(pack_id or "")
    if not raw or len(raw) > MAX_PACK_ID:
        return False
    if raw in (".", ".."):
        return False
    return bool(_PACK_ID_RE.match(raw))


def safe_pack_dir(root: Path, pack_id: str) -> Path:
    """`root / pack_id`, refusing anything that would leave `root`.

    The containment check is kept as well as the character rule. Both are cheap,
    and the character rule is the one that makes the common case impossible while
    the containment check is the one that holds if the rule is ever widened.
    """
    raw = str(pack_id or "")
    if not is_safe_pack_id(raw):
        raise UnsafePackId(
            f"pack id must be a plain identifier (letters, digits, dot, dash, "
            f"underscore; starting with a letter or digit; at most {MAX_PACK_ID} "
            f"characters), got {raw[:MAX_PACK_ID]!r}"
        )
    root_resolved = Path(root).resolve()
    target = (root_resolved / raw).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise UnsafePackId(f"pack id {raw!r} would resolve outside the dictionaries directory")
    return target
