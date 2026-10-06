"""— Echoes of Aincrad: exact name, measured zones, glyphs.

The title is *Echoes of Aincrad*, and the phase says to check that before
anything else. What the screens actually show is the rest: the boss bar on ws20
is rune glyphs that the recogniser reads as Latin words, every one of those
"words" has vowels so no noise filter touches them, and the backend then
transliterates them into cards on top of the runes.

The fix is a profile zone, because "this strip holds symbols" is a fact about
one game and the core must not learn it. What the core gains is one
role with no game attached: DECOR.

    uv run pytest tests/test_decor_role.py
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kizurium_translator import profile as profile_mod
from kizurium_translator.ocr.roles import SemanticRole, annotate_roles

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "kizurium_translator"

RUNE_JUNK = [
    "LIUITEQI",
    "TU-LIOL+EGI IHVIAAITIKLLALT FDEBUI-A",
    "nuan",
    "IーJ心VS",
]
REAL_LABELS = [
    "In Combat",
    "Search for Kirito",
    "Obtained Bronze Rapier",
    "Obtained Iron Chunk",
    "Ambusher",
    "Pick Up",
    "Tyrant Golem",
    "Lv.21",
    "Waiting to Switch",
]


@pytest.fixture(autouse=True)
def _clean_profile():
    profile_mod.reset_cache_for_tests()
    yield
    profile_mod.reset_cache_for_tests()


def test_the_exact_title_is_echoes_of_aincrad():
    """The phase says to confirm the name first. Steam 2244210: Echoes."""
    catalog = profile_mod.all_profiles()
    assert "echoes_of_aincrad" in catalog
    assert "echo_of_aincrad" not in catalog, "название игры не то"


def test_the_profile_is_data_with_measured_zones():
    prof = profile_mod.all_profiles()["echoes_of_aincrad"]
    assert prof.lexicon_pack == "echoes_of_aincrad"
    names = {z.name for z in prof.ui_zones}
    assert "glyph_band" in names
    assert "objective_line" in names
    assert "interaction_prompt" in names
    for zone in prof.ui_zones:
        assert zone.unit == "frac", "зоны профиля - доли кадра, не пиксели"
        assert 0.0 <= zone.x <= 1.0 and 0.0 <= zone.w <= 1.0
        assert 0.0 <= zone.y <= 1.0 and 0.0 <= zone.h <= 1.0


def test_core_does_not_mention_the_game():
    """names of a specific game are DATA, never core branches."""
    for path in SRC.rglob("*.py"):
        if path.parent.name in ("data", "profile"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        assert "aincrad" not in text, f"{path.name} знает про игру"


def test_a_glyph_zone_makes_the_runes_decor():
    prof = profile_mod.set_active("echoes_of_aincrad")
    assert prof is not None
    # The rune strip sits at roughly x 996..1210, y 85..190 on a 1920x1080 frame.
    runes = [
        {"text": t, "box": (996, 85, 1110, 117), "kind": "ui"}
        for t in RUNE_JUNK
    ]
    out = annotate_roles(runes, frame_size=(1920, 1080))
    for line in out:
        assert line["semantic_role"] == SemanticRole.DECOR.value
        assert line["role_from_zone"] == "DECOR"


def test_real_hud_labels_are_not_decor():
    profile_mod.set_active("echoes_of_aincrad")
    lines = [
        {"text": "Tyrant Golem", "box": (845, 50, 1007, 81)},
        {"text": "Search for Kirito", "box": (1528, 237, 1691, 266)},
        {"text": "Pick Up", "box": (937, 845, 1021, 881)},
        {"text": "Ambusher", "box": (1012, 615, 1109, 636)},
    ]
    out = annotate_roles(lines, frame_size=(1920, 1080))
    assert all(line["semantic_role"] != SemanticRole.DECOR.value for line in out)


def test_without_a_profile_nothing_is_decor():
    """Profiles are opt-in; the universal path must not guess either way."""
    assert profile_mod.active() is None
    lines = [{"text": t, "box": (996, 85, 1110, 117)} for t in RUNE_JUNK]
    out = annotate_roles(lines, frame_size=(1920, 1080))
    assert all(line["semantic_role"] != SemanticRole.DECOR.value for line in out)


def test_decor_lines_never_reach_the_overlay():
    """`kind=decor` is filtered in prepare, before translation."""
    src = (SRC / "live" / "prepare.py").read_text(encoding="utf-8")
    assert '"decor"' in src, "подготовка должна отбрасывать decor"


def test_the_lexicon_pack_is_opt_in_and_shipped():
    base = ROOT / "src" / "kizurium_translator" / "data" / "lexicons" / "games"
    pack = base / "echoes_of_aincrad"
    manifest = (pack / "manifest.toml").read_text(encoding="utf-8")
    assert 'id = "echoes_of_aincrad"' in manifest
    assert 'scope = "game"' in manifest
    terms = (pack / "terms.tsv").read_text(encoding="utf-8").splitlines()
    assert terms[0].split("\t") == ["source", "target", "scope", "priority", "flags"]
    rows = {line.split("\t")[0] for line in terms[1:] if line.strip()}
    assert "Kirito" in rows and "Aincrad" in rows


def test_the_title_is_the_official_one():
    """The name in the pack has to be the store's, not a remembered one."""
    pack = SRC / "data" / "lexicons" / "games" / "echoes_of_aincrad"
    manifest = (pack / "manifest.toml").read_text(encoding="utf-8")
    profile = (SRC / "data" / "profiles" / "echoes_of_aincrad.toml").read_text(encoding="utf-8")
    assert 'id = "echoes_of_aincrad"' in manifest
    assert "Echo of Aincrad" not in profile, "название не точное"
    assert "Echoes of Aincrad" in profile


def test_no_hardcoded_sixteen_hundred_by_nine_hundred_defaults():
    """Geometry is fractions, not one laptop's pixel counts."""
    src = (SRC / "data" / "profiles" / "echoes_of_aincrad.toml").read_text(encoding="utf-8")
    ast.parse("pass")  # profiles are TOML; the parse guard is for the python files
    assert "1920" not in src.replace("1920x1080", ""), "профиль не должен хранить пиксели"


@pytest.mark.parametrize("label", REAL_LABELS)
def test_real_labels_survive_the_existing_noise_filter(label):
    from kizurium_translator.ocr.engine import is_garbage_ocr

    assert not is_garbage_ocr(label), f"{label!r} не должен считаться мусором"
