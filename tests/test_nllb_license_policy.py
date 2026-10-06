"""NLLB is not a default / catalog offline model.

"""

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translation.packs import (  # noqa: E402
    load_catalog,
    nllb_is_default_blocked,
)

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "docs" / "licenses-inventory.toml"


def test_nllb_blocker_matches_ids_and_engines():
    assert nllb_is_default_blocked("nllb-200-distilled-600M")
    assert nllb_is_default_blocked("opus-mt-en-ru", "nllb")
    assert not nllb_is_default_blocked("opus-mt-en-ru", "ctranslate2")
    assert not nllb_is_default_blocked("opus-mt-ja-ru")


def test_shipped_catalog_has_no_nllb():
    packs = load_catalog()
    assert packs, "empty catalog"
    for p in packs:
        assert not nllb_is_default_blocked(p.id, p.engine)
        assert p.engine == "ctranslate2"
        assert p.id.startswith("opus-mt-")


def test_load_catalog_rejects_nllb_row(tmp_path):
    bad = tmp_path / "catalog.toml"
    bad.write_text(
        """
[[pack]]
id = "nllb-200-en-ru"
label = "NLLB"
version = "1"
source = "en"
target = "ru"
engine = "nllb"
model_format = "ctranslate2"
quantization = "int8"
url = ""
sha256 = ""
size_bytes = 0
license = "CC-BY-NC-4.0"
license_url = ""
source_url = ""
min_app_version = "0.1.0"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="NLLB"):
        load_catalog(bad)


def test_inventory_lists_nllb_as_non_default():
    data = tomllib.loads(INVENTORY.read_text(encoding="utf-8"))
    nllb = next(row for row in data["component"] if row["name"] == "NLLB")
    assert nllb["bundled"] is False
    assert nllb["optional"] is True
    assert nllb["redistributable"] is False
    assert "NOT a default" in nllb["notes"] or "not a default" in nllb["notes"].lower()


def test_docs_state_the_nllb_policy():
    """Both documents have to say NLLB is not shipped and not a default.

    Checked as a claim rather than as a phrase: the wording is free to change, the
    decision is not.
    """
    licenses_doc = (ROOT / "docs" / "licenses.md").read_text(encoding="utf-8")
    assert "NLLB" in licenses_doc
    low = licenses_doc.lower().replace("**", "").replace("`", "")
    for word in ("not default", "not a default", "not bundled", "not shipped"):
        if word in low:
            break
    else:
        pytest.fail("licenses.md не говорит, что NLLB не по умолчанию и не в поставке")

    packs_doc = (ROOT / "docs" / "language-packs.md").read_text(encoding="utf-8")
    assert "NLLB" in packs_doc
    low = packs_doc.lower().replace("**", "").replace("`", "")
    assert "not a catalog pack" in low or "not default" in low or "not a default" in low
