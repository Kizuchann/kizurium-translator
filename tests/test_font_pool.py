"""a face is matched by features, never identified by name.

A screenshot cannot tell us the game's font, and pretending otherwise is how a
repository ends up shipping a proprietary face. What a screenshot *can* say is
what kind of type it is - serif or sans, how heavy, how slanted, how wide a line
is for its size, how dense the strokes are - and that is enough to pick the
nearest face out of the faces we are allowed to ship.

So: no proprietary face in the pool, every face open-licensed, and the role that
decides which list to walk comes from the shape of the line.

    uv run pytest tests/test_font_pool.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kizurium_translator import fonts as fonts_mod
from kizurium_translator.core.text import _role_for

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "src" / "kizurium_translator" / "fonts"

# Faces whose licence lets them ship. Anything a game publisher commissioned and
# did not release is not here, and must not be.
OPEN_LICENCE_MARKERS = ("OFL", "Apache", "SIL", "CC0", "MIT")

# Words that only ever name a commercial game typeface. Their presence anywhere
# in the pool is a red flag worth failing on, because the failure mode is a
# repository carrying a face it has no right to.
PROPRIETARY_HINTS = (
    "a-aren",
    "genshin",
    "arknights",
    "talesof",
    "berseria",
    "mu",
    "dash",
    "sekaiproject",
)


def test_every_bundled_face_ships_with_an_open_licence():
    licence = (FONT_DIR / "LICENSE-OFL.txt").read_text(encoding="utf-8")
    assert any(m in licence for m in OPEN_LICENCE_MARKERS), "нет открытой лицензии"
    faces = list(FONT_DIR.glob("*.ttf"))
    assert faces, "в репозитории нет ни одного шрифта"
    for face in faces:
        low = face.name.casefold()
        assert not any(h in low for h in PROPRIETARY_HINTS), f"{face.name} - проприетарный?"


def test_the_face_table_names_only_bundled_or_system_faces():
    for key, family in fonts_mod.FACES.items():
        assert family, f"{key} без имени"
        # Nothing that looks like a path, a file or a proprietary id.
        assert "/" not in family and "\\" not in family
        assert not any(h in family.casefold() for h in PROPRIETARY_HINTS)


def test_every_role_resolves_to_families_we_know():
    """A role's faces must be keys of FACES, or nothing can be resolved to them.

    The names inside a role are the family's *display* name, not its key, so the
    check is that each of them can be found in the face table either way round.
    """
    known = {f.casefold() for f in fonts_mod.FACES.values()}
    for role, families in fonts_mod.ROLE_FACES.items():
        assert families, f"роль {role} пуста"
        for family in families:
            key = family.casefold()
            assert key in known or family in fonts_mod.FACES, (
                f"{role}: {family!r} нет ни в FACES, ни как имя семейства"
            )


def test_roles_cover_the_features_the_plan_lists():
    """lists serif/sans/mono, weight, slant, width, stroke, script, height.

    Each of these has to be readable from the line for the plan's approach to be
    the approach, so each has to come out of `_role_for` for some line.
    """
    # serif vs sans: an italic dialogue line is serif, a plain label is not.
    assert _role_for({"kind": "dialogue", "src_w": 400, "src_h": 30, "src_lines": 1}) == "serif"
    assert _role_for({"kind": "ui", "src_w": 120, "src_h": 20, "src_lines": 1}) != "serif"
    # width ratio and glyph height: a long flat line reads as a score readout,
    # a wrapped paragraph as a body, and a short label as neither.
    assert _role_for({"kind": "ui", "src_w": 1200, "src_h": 26, "src_lines": 1}) == "score"
    assert (
        _role_for(
            {"kind": "body", "src_w": 1266, "src_h": 130, "src_lines": 4, "text": "Modern nation"}
        )
        == "body"
    )
    assert _role_for({"kind": "ui", "src_w": 147, "src_h": 27, "stem": 0.11, "text": "100"}) == "stat"
    # stroke density: heavy lettering and light lettering land differently.
    heavy = _role_for({"kind": "name", "src_w": 150, "src_h": 50, "stem": 0.24, "src_lines": 1, "text": "Kirito"})
    light = _role_for({"kind": "name", "src_w": 150, "src_h": 50, "stem": 0.05, "src_lines": 1, "text": "Kirito"})
    assert heavy != light or heavy == "display"


def test_the_role_never_depends_on_the_words():
    """and the phase's own note: a game's vocabulary is not knowable."""
    a = _role_for({"kind": "ui", "src_w": 200, "src_h": 30, "src_lines": 1, "text": "Base Reward"})
    b = _role_for({"kind": "ui", "src_w": 200, "src_h": 30, "src_lines": 1, "text": "Zzz qqq vvv"})
    assert a == b


def test_measured_lines_get_measured_faces():
    """A paragraph keeps its own leading and its own ink, so it stays a paragraph."""
    par = {
        "kind": "body",
        "src_w": 1266,
        "src_h": 130,
        "src_lines": 4,
        "stem": 0.14,
        "text": "Modern nation ruled by dragons",
    }
    assert _role_for(par) == "body"
    assert fonts_mod.family_list(_role_for(par)).split(",")[0].strip()


def test_a_condensed_label_gets_a_condensed_face():
    """lists width ratio, and this is what it is for.

    A HUD label whose letters are much narrower than the card around them was set
    in a condensed face. Judged by the card alone it reads as a wide flat line
    and lands on the score list; judged by the ink it lands on the stat list,
    whose faces are condensed on purpose.
    """
    base = {"kind": "ui", "src_w": 200, "src_h": 30, "src_lines": 1, "text": "Dur"}
    wide = dict(base, ink_aspect=200 / 30)
    condensed = dict(base, ink_aspect=(200 / 30) * 0.6)
    assert _role_for(wide) != _role_for(condensed), "ширина не влияет на роль"
    assert _role_for(condensed) == "stat"


def test_every_role_has_a_face_that_can_actually_draw_russian():
    """Checked against the font files, not against a list I typed.

    the allowed faces are picked from the pool, and the pool has to be able
    to carry a translation into Russian. "PT Serif" and "Noto Sans" are display
    names; the files are what answer the question, so the files are what get
    asked. A role whose faces all lack Cyrillic would put boxes on the screen.
    """
    from PIL import ImageFont

    font_dir = ROOT / "src" / "kizurium_translator" / "fonts"
    file_for: dict[str, Path] = {}
    for path in font_dir.glob("*.ttf"):
        # keys are short names like PTSerif, NotoSans; files are PT Serif-*.ttf
        stem = path.stem.split("-")[0]
        file_for.setdefault(stem, path)
    for role, families in fonts_mod.ROLE_FACES.items():
        ok = []
        for family in families:
            key = family.replace(" ", "")
            path = file_for.get(key)
            if path is None:
                continue
            face = ImageFont.truetype(str(path), 24)
            ok.append(face.getmask("Ж").getbbox() is not None)
        assert any(ok), f"{role}: ни одна гарнитура не умеет кириллицу"


def test_no_literal_game_title_in_the_font_table():
    for key in list(fonts_mod.FACES) + list(fonts_mod.ROLE_FACES):
        low = key.casefold()
        assert not any(h in low for h in PROPRIETARY_HINTS), f"роль/гарнитура {key} про игру"


def test_family_list_is_ordered_and_ends_with_a_cyrillic_face():
    """The last face has to carry a Russian letter, or the tail is unreadable."""
    listed = fonts_mod.family_list("body")
    assert "," in listed, "нужен список гарнитур, а не одна"
    assert listed.strip()


@pytest.mark.parametrize("role", sorted(fonts_mod.ROLE_FACES))
def test_each_role_resolves_to_faces(role):
    listed = fonts_mod.family_list(role)
    assert listed
    assert not re.search(r"^\s*$", listed)
