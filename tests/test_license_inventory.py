""": third-party license inventory must stay complete."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "docs" / "licenses-inventory.toml"
REQUIRED = {
    "JMdict",
    "Tatoeba",
    "OPUS-MT",
    "RapidOCR",
    "CTranslate2",
    "NLLB",
    "pymorphy3",
    "MeikiOCR",
}
REQUIRED_FIELDS = (
    "name",
    "source",
    "url",
    "version",
    "license",
    "license_url",
    "redistributable",
    "attribution_required",
    "commercial_use",
    "bundled",
    "optional",
    "checksum",
)


def test_inventory_lists_every_required_component():
    data = tomllib.loads(INVENTORY.read_text(encoding="utf-8"))
    names = {row["name"] for row in data["component"]}
    missing = REQUIRED - names
    assert not missing, f"license inventory missing {missing}"


def test_inventory_rows_have_required_fields():
    data = tomllib.loads(INVENTORY.read_text(encoding="utf-8"))
    for row in data["component"]:
        for field in REQUIRED_FIELDS:
            assert field in row, f"{row.get('name')!r} missing {field}"


def test_nothing_copyrighted_is_marked_bundled_without_license():
    data = tomllib.loads(INVENTORY.read_text(encoding="utf-8"))
    for row in data["component"]:
        if row["bundled"]:
            assert row["license"] and row["license"] != "see-package"
            assert row["redistributable"] is True


def test_human_summary_exists():
    assert (ROOT / "docs" / "licenses.md").is_file()
