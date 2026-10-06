"""The faces the overlay draws with, and the ones it measures against.

These are in the repository because the overlay asked the system for `Sans` and
got whatever the machine had. That is a different face on every machine, and not
only a different look - the metrics differ, so a label laid out here wraps, fits
or overflows differently there, and a card that covers its text exactly on the
machine it was built on leaves the original showing through on someone else's. A
translator that draws over what it read has to measure the same way everywhere.
Registration goes through fontconfig rather than by asking Pango for a name,
because Pango resolves a name against whatever fontconfig can see, and what it
can see is the machine: a private configuration with the repository's directory
added to it makes the face list the same everywhere.

The families a role names are a list and not one name, because most game faces
have no Cyrillic in them at all - Rajdhani, Michroma and Orbitron cover Latin
and not one Russian letter, and the overlay translates into Russian, so asking
for one of them alone puts boxes on the screen where the translation is. Pango
walks the list per character, so a face is used where it has the glyphs and the
next covers the rest, which is what a game does with a character missing from its
own font.

A role says what kind of type it is - a dense panel of numbers, a stat row, a
heading, a plain label - and gets those faces nearest first. Which one is used
for a given line is decided by how its own type measures and not by its text: a
face is taken when the line is narrow and lettered the way that face is
lettered, the one thing a translation can measure about the original and cannot
change.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
from pathlib import Path

# cairo's font type, which fontconfig's backend is. Not exported through
# PyGObject, and it is an enum value in a C header.
_CAIRO_FONT_TYPE_FT = 6


FONT_DIR = Path(__file__).resolve().parent / "fonts"

# Faces bundled with the package, and what each one is for. The order inside
# each role is the preference order: the face a screen of that kind is most
# often set in comes first.
#
# Faces with Cyrillic, which are the ones that can carry a translation into
# Russian. Faces without (Rajdhani, Orbitron, Michroma) stay in the list so a
# Latin HUD line still measures in the face it was set in; Pango walks past
# them for any Russian letter.
FACES: dict[str, str] = {
    "NotoSans": "Noto Sans",
    "NotoSerif": "Noto Serif",
    "Exo2": "Exo 2",
    "Oswald": "Oswald",
    "Rubik": "Rubik",
    "Nunito": "Nunito",
    "Comfortaa": "Comfortaa",
    "Unbounded": "Unbounded",
    "Tektur": "Tektur",
    "Onest": "Onest",
    "RussoOne": "Russo One",
    "PTSerif": "PT Serif",
    "AlumniSans": "Alumni Sans",
    "PressStart2P": "Press Start 2P",
    "GolosText": "Golos Text",
    "Rajdhani": "Rajdhani",
    "Orbitron": "Orbitron",
    "Michroma": "Michroma",
    "Play": "Play",
    "Cuprum": "Cuprum",
}

# A face that can draw Russian, used as the last name in every list. Pango
# reaches it for any character the face before it does not have, which is what
# keeps a translation off the tofu path.
_CYRILLIC_FALLBACK = "Noto Sans"

# Roles, and the faces each is drawn in. The role of a line is decided from what
# the line looks like, not from what it says, so a game with an unfamiliar
# vocabulary still gets the right face.
ROLE_FACES: dict[str, tuple[str, ...]] = {
    # Numbers and values in a panel: narrow, dense, tracked out.
    "stat": ("Oswald", "Cuprum", "Tektur", "Rajdhani", "Exo 2"),
    # A heading or a title: wide, heavy, the largest thing in its region.
    "display": ("Unbounded", "Russo One", "Oswald", "Exo 2"),
    # Rounded playful plates — character names on a rhythm sheet.
    "rounded": ("Nunito", "Comfortaa", "Rubik", "Onest"),
    # Novel dialogue and italic nicknames.
    "serif": ("PT Serif", "Noto Serif", "Noto Sans"),
    # Body text in a panel: whatever has Cyrillic and reads at small sizes.
    "body": ("Golos Text", "Onest", "Noto Sans", "Rubik"),
    # A name, a label, a button: the default.
    "label": ("Onest", "Noto Sans", "Rubik", "Exo 2"),
    # Numbers in a rhythm or score readout: condensed and usually bold.
    "score": ("Tektur", "Oswald", "Alumni Sans", "Rajdhani", "Exo 2"),
    # Pixel / HUD bitmap look.
    "pixel": ("Press Start 2P", "Noto Sans"),
    # Anything measured as having no particular character to it.
    "any": ("Noto Sans",),
}

# Words that mean a name table entry is a style rather than a family, and so
# that the family beside it is the one to ask for.
_STYLE_WORDS = (
    "light",
    "regular",
    "bold",
    "black",
    "heavy",
    "medium",
    "semibold",
    "semi bold",
    "extrabold",
    "extra bold",
    "thin",
    "italic",
    "oblique",
    "condensed",
    "narrow",
)

# A face that is a deliberately stylised one, kept apart from the rest because
# it is only ever a fallback inside a list and never the whole of it: these
# have no Cyrillic, so a translation drawn in one of them alone would be boxes.
_DISPLAY_ONLY = {"Orbitron", "Michroma"}


def _load_fontconfig() -> ctypes.CDLL | None:
    """fontconfig, with its signatures declared.

    The signatures are not optional. A C function that returns a pointer is
    declared to ctypes as returning int by default, and a pointer that arrives
    truncated to 32 bits is not a pointer - passing it back to the next call
    dereferences whatever happened to be in the upper half of the register, and
    the process dies on the spot with no message. Which is what it did.
    """
    name = ctypes.util.find_library("fontconfig") or "libfontconfig.so.1"
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.FcConfigCreate.restype = ctypes.c_void_p
    lib.FcConfigAppFontAddDir.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.FcConfigAppFontAddDir.restype = ctypes.c_bool
    lib.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.FcConfigAppFontAddFile.restype = ctypes.c_bool
    lib.FcConfigBuildFonts.argtypes = [ctypes.c_void_p]
    lib.FcConfigSetCurrent.argtypes = [ctypes.c_void_p]
    lib.FcConfigSetCurrent.restype = ctypes.c_bool
    # Blank configs from FcConfigCreate have no cache dirs; without one,
    # BuildFonts prints "No writable cache directories" once per face.
    lib.FcConfigParseAndLoadFromMemory.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_bool,
    ]
    lib.FcConfigParseAndLoadFromMemory.restype = ctypes.c_bool
    return lib


class _Registration:
    """The repository's fonts, registered once, and the map that sees them."""

    def __init__(self) -> None:
        self.done = False
        self.config = None
        self.font_map = None
        self.available: set[str] = set()
        self.reason = ""

    def run(self) -> None:
        if self.done:
            return
        self.done = True
        if not FONT_DIR.is_dir():
            self.reason = "no fonts directory in the package"
            return
        files = sorted(FONT_DIR.glob("*.ttf")) + sorted(FONT_DIR.glob("*.otf"))
        if not files:
            self.reason = "fonts directory is empty"
            return
        fc = _load_fontconfig()
        if fc is None:
            self.reason = "fontconfig is not available on this machine"
            return
        try:
            cfg = fc.FcConfigCreate()
            if not cfg:
                self.reason = "fontconfig made no configuration"
                return
            # FcConfigCreate is empty: no cache dirs. Attach one before any
            # scan, otherwise BuildFonts dumps the same stderr line per face.
            cache = ensure_fontconfig_cache()
            if cache is not None:
                snippet = (
                    '<?xml version="1.0"?>'
                    '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
                    "<fontconfig>"
                    f"<cachedir>{cache}</cachedir>"
                    "</fontconfig>"
                )
                fc.FcConfigParseAndLoadFromMemory(cfg, snippet.encode(), False)
            for path in (str(FONT_DIR), *_system_font_dirs()):
                fc.FcConfigAppFontAddDir(cfg, path.encode())
            fc.FcConfigBuildFonts(cfg)
            if not fc.FcConfigSetCurrent(cfg):
                self.reason = "fontconfig refused the configuration"
                return
            self.config = cfg
        except Exception as exc:  # noqa: BLE001 - one bad call must not stop the run
            self.reason = f"{type(exc).__name__}: {exc}"
            return
        # Ask the loader which of the faces it can actually see, rather than
        # trusting the files. A file that is present and unusable is worse than
        # one that is absent, because the fallback list would name it and stop.
        self.available = _families_in_use(fc)
        if not self.available:
            self.reason = self.reason or "no bundled face could be named"
        self.font_map = _private_font_map()

    def families(self) -> list[str]:
        if self.done is False:
            self.run()
        return [
            name for key, name in FACES.items() if not self.available or key in self.available
        ]


_STATE = _Registration()
_LOCK = threading.Lock()


def ensure_registered() -> _Registration:
    """Register the bundled faces. Safe to call from anywhere, does it once."""
    with _LOCK:
        _STATE.run()
    return _STATE


def ensure_fontconfig_cache() -> Path | None:
    """Make sure fontconfig has a writable cache directory.

    ``FcConfigCreate`` starts with no cache dirs at all, and the previous helper
    also rewrote ``XDG_CACHE_HOME`` to its parent (``~/.cache`` → ``~``), which
    pointed the rest of the process at the wrong place. Create a real directory,
    leave the environment alone beyond filling a missing ``XDG_CACHE_HOME``, and
    return the path so the private config can attach it before ``BuildFonts``.
    """
    roots: list[Path] = []
    xdg = (os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg:
        roots.append(Path(xdg))
    else:
        home_cache = Path.home() / ".cache"
        roots.append(home_cache)
        # Only set when unset: do not move an existing XDG_CACHE_HOME.
        os.environ["XDG_CACHE_HOME"] = str(home_cache)
    state = (os.environ.get("XDG_STATE_HOME") or "").strip()
    roots.append(Path(state) if state else Path.home() / ".local" / "state")
    for root in roots:
        path = root / "fontconfig"
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".kizurium-write-test"
            probe.write_bytes(b"")
            probe.unlink(missing_ok=True)
            return path
        except OSError:
            continue
    return None


def prepare_gui_env() -> None:
    """Quiet known-harmless startup noise before GTK or fontconfig load.

    GTK prints an a11y-bus warning when ``org.a11y.Bus`` is masked; setting
    ``GTK_A11Y=none`` is what GTK itself suggests. Fontconfig cache setup is
    here so a later private registration already has a place to write.
    """
    os.environ.setdefault("GTK_A11Y", "none")
    ensure_fontconfig_cache()


def _system_font_dirs() -> list[str]:
    out: list[str] = []
    # XDG_DATA_DIRS covers NixOS (/nix/store/...-share) and FHS distros.
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    roots = [
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
        *[p for p in data_dirs.split(":") if p],
        "/usr/share",
        "/usr/local/share",
    ]
    seen: set[str] = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        for sub in ("fonts", "fontconfig"):
            path = os.path.join(root, sub)
            if os.path.isdir(path):
                out.append(path)
    return out


def _families_in_use(fc: ctypes.CDLL) -> set[str]:
    """Which of the bundled faces can be named, read from the files' own tables.

    Not through fontconfig: the file being present says it was found, not that
    the loader will hand it back under that name, and the name table says what
    the file calls itself. A face whose name does not match is left out of the
    lists, so a card never asks for a face that will not come.
    """
    del fc
    files = sorted(FONT_DIR.glob("*.ttf")) + sorted(FONT_DIR.glob("*.otf"))
    names = {_font_family(f) for f in files}
    return {key for key, name in FACES.items() if name in names}


_FAMILY_CACHE: dict[str, str] | None = None


def _font_family(path: Path) -> str:
    """The family name out of a font file's name table, without a dependency.

    Read directly rather than through a library: this runs before anything else
    has set up, and a missing fontTools must not be what decides the faces.
    """
    global _FAMILY_CACHE
    if _FAMILY_CACHE is None:
        _FAMILY_CACHE = {}
    hit = _FAMILY_CACHE.get(path.name)
    if hit is not None:
        return hit
    name = ""
    try:
        import struct

        data = path.read_bytes()
        if len(data) < 12:
            return ""
        tag = data[:4]
        base = 12 if tag == b"ttcf" else 0
        num_tables = struct.unpack(">H", data[base + 4: base + 6])[0]
        for i in range(num_tables):
            rec = 12 + i * 16
            if rec + 16 > len(data):
                break
            tag = data[rec : rec + 4]
            if tag not in (b"name", b"OTTO"):
                continue
            off, length = struct.unpack(">II", data[rec + 8: rec + 16])
            table = data[off : off + length]
            count, string_off = struct.unpack(">HH", table[2:6])
            by_id: dict[int, str] = {}
            for r in range(count):
                rec2 = 6 + r * 12
                if rec2 + 12 > len(table):
                    break
                pid, eid, lid, nid, slen, soff = struct.unpack(
                    ">HHHHHH", table[rec2: rec2 + 12]
                )
                if nid not in (1, 16, 21):
                    continue
                raw = table[string_off + soff : string_off + soff + slen]
                try:
                    text = raw.decode("utf-16-be" if pid == 3 else "latin-1")
                except Exception:
                    continue
                if text and nid not in by_id:
                    by_id[nid] = text
            # 16 is the typographic family, which is the family's own name. 1 is
            # the subfamily, which for a variable font is the default instance:
            # "Rubik Light" where the family is "Rubik". So where both are
            # offered, the one that is not a style is the family to ask for.
            best = ""
            for nid in (1, 16, 21):
                cand = by_id.get(nid, "")
                if cand and not any(w in cand.casefold() for w in _STYLE_WORDS):
                    best = cand
                    break
            if not best:
                best = by_id.get(16) or by_id.get(1) or by_id.get(21, "")
            if best:
                name = best
                break
    except Exception:
        name = ""
    _FAMILY_CACHE[path.name] = name
    return name


def _private_font_map():
    """A Pango font map over the configuration just registered."""
    try:
        import gi

        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo, cairo

        # The enum, not the number: PangoCairo checks the type of what it is
        # given and a bare int is not what it expects.
        return PangoCairo.font_map_new_for_font_type(cairo.FontType.FT)
    except Exception:
        return None


def family_list(role: str) -> str:
    """The CSS family list a role is drawn in, ending in something with Cyrillic.

    The list is what Pango walks per character, so a face with no Russian in it
    is fine as long as a face with Russian is behind it in the same list.
    """
    faces = ROLE_FACES.get(role) or ROLE_FACES["any"]
    names: list[str] = []
    for key in faces:
        if key in _DISPLAY_ONLY and role not in ("display",):
            # A stylised face with no Cyrillic is only ever a first choice where
            # the text is Latin; anywhere else it would be a screen full of
            # boxes with the translation on it.
            continue
        name = FACES.get(key)
        if not name:
            name = next((n for n in FACES.values() if n == key), "")
        if name and name not in names:
            names.append(name)
    if _CYRILLIC_FALLBACK not in names:
        names.append(_CYRILLIC_FALLBACK)
    return ", ".join(names)
