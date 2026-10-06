"""offline language pack catalog and install flow."""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translation.packs import (  # noqa: E402
    find_installed_pair,
    install_pack,
    install_pack_from_file,
    list_pack_status,
    load_catalog,
    sha256_file,
)


def test_catalog_lists_opus_pairs():
    packs = load_catalog()
    ids = {p.id for p in packs}
    assert "opus-mt-en-ru" in ids
    assert "opus-mt-ja-ru" in ids
    for p in packs:
        assert p.engine == "ctranslate2"
        assert p.license
        assert p.source_url
        assert p.downloadable, f"{p.id} must be one-command installable"


def test_catalog_en_ru_has_url_and_sha():
    en = next(p for p in load_catalog() if p.id == "opus-mt-en-ru")
    assert en.url.startswith("https://")
    assert len(en.sha256) == 64


def test_catalog_ja_ru_has_hf_repo():
    ja = next(p for p in load_catalog() if p.id == "opus-mt-ja-ru")
    assert ja.hf_repo
    assert ja.hf_revision  # pinned commit — no floating main
    assert not ja.url  # HF snapshot path, not a single zip


def test_list_status_download_when_missing(tmp_path):
    rows = list_pack_status(models_dir=tmp_path)
    assert rows
    assert all(not r.installed for r in rows)


def test_install_from_dir_and_find_pair(tmp_path):
    model = tmp_path / "raw-model"
    model.mkdir()
    (model / "model.bin").write_bytes(b"fake-ct2")
    (model / "shared_vocabulary.json").write_text("{}", encoding="utf-8")
    info = next(p for p in load_catalog() if p.id == "opus-mt-en-ru")
    dest = install_pack_from_file(model, info, models_dir=tmp_path / "models")
    assert (dest / "model" / "model.bin").is_file()
    assert (dest / "manifest.toml").is_file()
    rows = list_pack_status(models_dir=tmp_path / "models")
    en_ru = next(r for r in rows if r.info.id == "opus-mt-en-ru")
    assert en_ru.installed
    assert find_installed_pair("en", "ru", models_dir=tmp_path / "models") == dest / "model"


def test_sha256_mismatch_keeps_previous(tmp_path):
    models = tmp_path / "models"
    info = next(p for p in load_catalog() if p.id == "opus-mt-en-ru")
    # First install
    first = tmp_path / "v1"
    first.mkdir()
    (first / "keep.txt").write_text("v1", encoding="utf-8")
    install_pack_from_file(first, info, models_dir=models)
    assert (models / "opus-mt-en-ru" / "model" / "keep.txt").read_text() == "v1"
    # Bad archive with wrong checksum
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("model/x.bin", b"nope")
    wrong = info
    from dataclasses import replace

    wrong = replace(info, sha256="0" * 64)
    with pytest.raises(ValueError, match="sha256"):
        install_pack_from_file(bad, wrong, models_dir=models)
    # Previous version intact
    assert (models / "opus-mt-en-ru" / "model" / "keep.txt").read_text() == "v1"


def test_install_pack_unknown_id(tmp_path):
    with pytest.raises(KeyError):
        install_pack("no-such-pack", models_dir=tmp_path)


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert sha256_file(p) == hashlib.sha256(b"abc").hexdigest()
