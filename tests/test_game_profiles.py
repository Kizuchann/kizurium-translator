"""game profiles are DATA; core never branches on a game name.

"""

import ast
from pathlib import Path

import pytest

from kizurium_translator import profile as profile_mod
from kizurium_translator.config import Config, load
from kizurium_translator.layout import name_gap
from kizurium_translator.translate import enable_title_glossary

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "kizurium_translator"


@pytest.fixture(autouse=True)
def _clean_profile():
    profile_mod.reset_cache_for_tests()
    enable_title_glossary(False)
    yield
    profile_mod.reset_cache_for_tests()
    enable_title_glossary(False)


def test_bundled_profiles_load():
    catalog = profile_mod.all_profiles()
    assert "arknights" in catalog
    assert "genshin" in catalog
    assert "sekai" in catalog
    ak = catalog["arknights"]
    assert ak.lexicon_pack == "arknights"
    assert ak.speaker.gap_min_px == 16.0
    assert any(z.name == "dialogue_band" for z in ak.ui_zones)


def test_set_active_exposes_lexicon_pack_and_gap_params():
    assert profile_mod.active() is None
    assert profile_mod.speaker_gap_params() == (16.0, 1.5)
    prof = profile_mod.set_active("sekai")
    assert prof is not None
    assert profile_mod.active_lexicon_pack() == "sekai"
    assert profile_mod.speaker_gap_params() == (12.0, 1.2)
    # name_gap reads the active profile, not a game string.
    assert name_gap.name_dialogue_gap_threshold(20.0) == max(12.0, 1.2 * 20.0)


def test_apply_to_config_overrides_ocr_and_timeouts():
    cfg = Config()
    prof = profile_mod.get("sekai")
    assert prof is not None
    out = profile_mod.apply_to_config(cfg, prof)
    assert out.ocr_engines == ("rapid", "meiki", "tesseract")
    assert out.interval == 0.45
    assert out.interval_sub == 0.22
    # Original untouched.
    assert cfg.interval == 0.55


def test_env_wins_over_config_id():
    assert (
        profile_mod.resolve_requested_id(
            config_id="genshin",
            env={"KIZURIUM_TRANSLATOR_PROFILE": "arknights"},
        )
        == "arknights"
    )
    assert profile_mod.resolve_requested_id(config_id="genshin", env={}) == "genshin"


def test_config_toml_profile_id(tmp_path):
    path = tmp_path / "cfg.toml"
    path.write_text('[profile]\nid = "arknights"\n', encoding="utf-8")
    cfg = load(path)
    assert cfg.profile_id == "arknights"


def test_activate_turns_on_title_glossary():
    from kizurium_translator import translate

    assert not translate._TITLE_TRUTHY_ON
    profile_mod.activate("arknights")
    assert translate._TITLE_TRUTHY_ON
    profile_mod.activate(None)
    assert not translate._TITLE_TRUTHY_ON


def test_core_modules_have_no_if_game_equals_branches():
    """Plan: forbidden ``if game == "Arknights"`` style in core algorithms."""
    banned_dirs = (
        SRC / "layout",
        SRC / "ocr",
        SRC / "typography",
        SRC / "render",
        SRC / "live",
    )
    offenders: list[str] = []
    for directory in banned_dirs:
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                # Look for comparisons against string literals that look like titles.
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(
                        comparator.value, str
                    ):
                        val = comparator.value
                        if val in {
                            "Arknights",
                            "Genshin",
                            "Genshin Impact",
                            "sekai",
                            "arknights",
                            "genshin",
                        }:
                            # Allow comments/docstrings only — this is code.
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{val!r}")
    assert not offenders, "game-name branches in core:\n" + "\n".join(offenders)


def test_zone_role_hint_only_with_active_profile():
    box = (100, 900, 800, 1000)  # lower band on 1920x1080
    assert profile_mod.zone_role_hint(box, frame_w=1920, frame_h=1080) is None
    profile_mod.set_active("arknights")
    assert profile_mod.zone_role_hint(box, frame_w=1920, frame_h=1080) == "DIALOGUE"
    top = (1500, 40, 1800, 90)
    assert profile_mod.zone_role_hint(top, frame_w=1920, frame_h=1080) == "HUD"


def test_annotate_roles_applies_zone_soft_prior():
    from kizurium_translator.ocr.roles import annotate_roles

    profile_mod.set_active("arknights")
    lines = [
        {
            "text": "???",
            "box": (400, 920, 1400, 1000),
            "kind": "",
        }
    ]
    out = annotate_roles(lines, frame_size=(1920, 1080))
    assert out[0]["semantic_role"] == "DIALOGUE"
    assert out[0].get("role_from_zone") == "DIALOGUE"


def test_temp_profile_file_loads(tmp_path):
    path = tmp_path / "custom.toml"
    path.write_text(
        'id = "custom"\nlexicon_pack = "custom"\n'
        "[speaker]\ngap_min_px = 40\ngap_glyph_mult = 3.0\n",
        encoding="utf-8",
    )
    profile_mod.reset_cache_for_tests()
    catalog = profile_mod.all_profiles(root=tmp_path)
    assert catalog["custom"].speaker.gap_min_px == 40.0
    profile_mod.set_active("custom", root=tmp_path)
    assert name_gap.name_dialogue_gap_threshold(10.0) == 40.0
