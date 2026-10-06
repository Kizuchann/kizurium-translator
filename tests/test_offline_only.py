"""offline_only never instantiates online backends."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.config import Config, load  # noqa: E402
from kizurium_translator.translate import (  # noqa: E402
    LANGUAGE_PACK_NOT_INSTALLED,
    Translator,
)


def test_config_loads_offline_only(tmp_path):
    conf = tmp_path / "c.toml"
    conf.write_text("[translation]\noffline_only = true\n", encoding="utf-8")
    loaded = load(conf)
    assert loaded.offline_only is True
    assert Config().offline_only is False


def test_offline_only_never_builds_http_session(tmp_path):
    calls: list[str] = []

    def factory():
        calls.append("session")
        raise AssertionError("HTTP session must not be created in offline_only")

    logs: list[str] = []
    tr = Translator(
        target="ru",
        source="en",
        use_gtx=True,
        allow_slow=True,
        offline_only=True,
        cache_path=tmp_path / "c.sqlite",
        glossary={},
        log=logs.append,
        session_factory=factory,
    )
    assert tr.offline_only is True
    assert tr.use_gtx is False
    assert tr.allow_slow is False
    assert tr._http() is None
    assert tr.via_gtx("Hello") == ""
    assert tr._slow_translate("Hello", "en") == ""
    assert calls == []
    assert any("offline_only=true" in m for m in logs)


def test_offline_only_logs_missing_pack_and_skips_gtx(tmp_path, monkeypatch):
    logs: list[str] = []
    tr = Translator(
        target="ru",
        source="en",
        offline_only=True,
        cache_path=tmp_path / "c.sqlite",
        glossary={},
        log=logs.append,
        session_factory=lambda: (_ for _ in ()).throw(AssertionError("no http")),
    )
    monkeypatch.setattr(tr, "via_local", lambda *a, **k: "")
    tr.via_gtx = lambda *a, **k: (_ for _ in ()).throw(AssertionError("gtx"))
    assert tr.translate("Hello world") == ""
    assert any(LANGUAGE_PACK_NOT_INSTALLED in m for m in logs)


def test_offline_only_uses_local_pack(tmp_path, monkeypatch):
    logs: list[str] = []
    tr = Translator(
        target="ru",
        source="en",
        offline_only=True,
        cache_path=tmp_path / "c.sqlite",
        glossary={},
        log=logs.append,
    )
    monkeypatch.setattr(tr, "via_local", lambda text, source=None, beam_size=1: f"лок:{text}")
    assert tr.translate("Hello") == "лок:Hello"
    assert not any(LANGUAGE_PACK_NOT_INSTALLED in m for m in logs)


def test_offline_only_glossary_still_works(tmp_path):
    tr = Translator(
        target="ru",
        source="en",
        offline_only=True,
        cache_path=tmp_path / "c.sqlite",
        glossary={"Settings": "Настройки"},
        log=lambda m: None,
    )
    assert tr.translate("Settings") == "Настройки"


def test_cli_flag_sets_offline_only():
    from argparse import Namespace

    from kizurium_translator.cli import resolve_config

    conf = resolve_config(
        Namespace(
            config=None,
            target=None,
            source=None,
            engine=None,
            interval=None,
            no_gtx=False,
            offline_only=True,
            allow_slow_translation=False,
        )
    )
    assert conf.offline_only is True
