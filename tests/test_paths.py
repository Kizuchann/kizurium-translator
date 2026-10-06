"""Filesystem locations must follow the XDG base directory specification.

The module claimed XDG support in its docstring while hardcoding ~/.config and
~/.cache, so a user who keeps their configuration elsewhere was ignored, and
the runtime directory fell back to the world-writable /tmp.
"""

import os
import stat
from pathlib import Path

from kizurium_translator import config, paths


def _clear(monkeypatch) -> None:
    """No XDG or override variables, so the fallbacks are what is under test."""
    for name in list(os.environ):
        if name.startswith("XDG_") or name.startswith(paths.ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)


class TestConfigHome:
    def test_xdg_config_home_is_honoured(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert paths.default_paths().config_file == tmp_path / "kizurium-translator" / "config.toml"

    def test_config_falls_back_to_dot_config(self, monkeypatch):
        _clear(monkeypatch)
        assert paths.default_paths().config_file == (
            Path.home() / ".config" / "kizurium-translator" / "config.toml"
        )

    def test_an_empty_variable_counts_as_unset(self, monkeypatch, tmp_path):
        # XDG says a relative or empty value must be ignored, not obeyed.
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", "  ")
        assert paths.default_paths().config_file == (
            Path.home() / ".config" / "kizurium-translator" / "config.toml"
        )

    def test_config_path_helper_agrees_with_paths(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert config.config_path() == paths.default_paths().config_file

    def test_config_path_ignores_an_empty_variable(self, monkeypatch):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert config.config_path() == (
            Path.home() / ".config" / "kizurium-translator" / "config.toml"
        )


class TestCacheHome:
    def test_xdg_cache_home_is_honoured(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert paths.default_paths().cache_dir == tmp_path / "kizurium-translator"

    def test_cache_falls_back_to_dot_cache(self, monkeypatch):
        _clear(monkeypatch)
        assert paths.default_paths().cache_dir == Path.home() / ".cache" / "kizurium-translator"

    def test_the_translate_cache_lives_under_the_cache_dir(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        p = paths.default_paths()
        assert p.cache_dir in p.translate_cache.parents
        assert p.translate_cache.name == "translate-cache.sqlite"

    def test_legacy_json_cache_is_removed(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        p = paths.default_paths()
        p.cache_dir.mkdir(parents=True)
        (p.cache_dir / "translate-cache.json").write_text('{"en\\u001fru\\u001fHi": "Привет"}', encoding="utf-8")
        (p.cache_dir / "translate-cache.json.bak").write_text("{}", encoding="utf-8")
        removed = paths.retire_legacy_translation_caches(p.cache_dir)
        assert len(removed) == 2
        assert not (p.cache_dir / "translate-cache.json").exists()
        assert not (p.cache_dir / "translate-cache.json.bak").exists()

    def test_legacy_home_sqlite_is_merged_into_xdg_cache(self, monkeypatch, tmp_path):
        """~/kizurium-translator/translate-cache.sqlite → proper XDG cache."""
        import sqlite3

        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        legacy_dir = fake_home / "kizurium-translator"
        legacy_dir.mkdir()
        legacy = legacy_dir / "translate-cache.sqlite"
        con = sqlite3.connect(legacy)
        con.execute(
            "CREATE TABLE translations ("
            "cache_key TEXT PRIMARY KEY, source_lang TEXT NOT NULL, "
            "target_lang TEXT NOT NULL, backend_id TEXT NOT NULL, "
            "glossary_version TEXT NOT NULL, dictionary_version TEXT NOT NULL, "
            "model_version TEXT NOT NULL, source_text TEXT NOT NULL, "
            "translated_text TEXT NOT NULL, created_at REAL NOT NULL, "
            "last_used_at REAL NOT NULL, use_count INTEGER NOT NULL DEFAULT 0)"
        )
        con.execute(
            "INSERT INTO translations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "en\x1fru\x1fCan I trust you?",
                "en",
                "ru",
                "gtx",
                "",
                "",
                "",
                "Can I trust you?",
                "Могу ли я тебе доверять?",
                1.0,
                1.0,
                1,
            ),
        )
        con.commit()
        con.close()

        p = paths.default_paths()
        p.ensure()
        assert p.translate_cache.is_file()
        assert not legacy.exists()
        dst = sqlite3.connect(p.translate_cache)
        row = dst.execute(
            "SELECT translated_text FROM translations WHERE source_text=?",
            ("Can I trust you?",),
        ).fetchone()
        dst.close()
        assert row == ("Могу ли я тебе доверять?",)

    def test_storage_report_lists_sqlite_and_local_roots(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        (tmp_path / "run").mkdir()
        report = "\n".join(paths.storage_report(paths.default_paths()))
        assert "translate-cache.sqlite" in report
        assert "SQLite" in report
        assert str(tmp_path / "cache") in report
        assert "Google gtx" in report
        assert "аналитики нет" in report
        assert "docs/trust.md" in report


class TestStateHome:
    def test_xdg_state_home_is_honoured(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert paths.default_paths().state_dir == tmp_path / "kizurium-translator"

    def test_state_falls_back_to_local_state_not_share(self, monkeypatch):
        # XDG_STATE_HOME has no default in the specification, so ~/.local/state
        # is the one that matches its meaning. ~/.local/share is a different dir.
        _clear(monkeypatch)
        assert paths.default_paths().state_dir == (
            Path.home() / ".local" / "state" / "kizurium-translator"
        )


class TestRuntimeDir:
    def test_xdg_runtime_dir_is_used_when_it_exists(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert paths.default_paths().runtime_dir == tmp_path / "kizurium-translator"

    def test_no_shared_tmp_fallback(self, monkeypatch):
        """XDG_RUNTIME_DIR unset must not put anything in /tmp.

        /tmp is world-writable: another user can pre-create the lock file, read
        the region file and delete the pid file, which is not a hypothetical on a
        shared machine.
        """
        _clear(monkeypatch)
        p = paths.default_paths()
        assert p.runtime_dir != Path("/tmp") / "kizurium-translator"
        for path in (p.lock, p.pid, p.text_pid, p.log, p.selector_log,
                     p.selector_request, p.selector_lock, p.selector_geometry):
            assert path != Path("/tmp") / path.name
            assert "tmp" not in path.parts[1:], path

    def test_a_missing_runtime_dir_falls_back_to_a_user_directory(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        # Pointed at a path that does not exist: trusting it would write into a
        # directory the session manager is not managing.
        missing = tmp_path / "not-there"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(missing))
        p = paths.default_paths()
        assert p.runtime_dir != missing / "kizurium-translator"
        assert "state" in p.runtime_dir.parts or "local" in p.runtime_dir.parts

    def test_an_empty_runtime_dir_falls_back_too(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", "")
        p = paths.default_paths()
        assert p.runtime_dir != Path("/tmp") / "kizurium-translator"

    def test_volatile_state_is_under_the_runtime_dir(self, monkeypatch, tmp_path):
        """The pid and the lock belong on tmpfs.

        They describe a running session, and a stale one left there after a
        logout is the correct thing to find: the next run overwrites them.
        """
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        p = paths.default_paths()
        for path in (p.lock, p.pid, p.text_pid,
                     p.selector_request, p.selector_lock):
            assert p.runtime_dir in path.parents, path

    def test_remembered_selector_geometry_survives_logout(self, monkeypatch, tmp_path):
        """Last region must not live on tmpfs — logout wiped it and broke restore."""
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        p = paths.default_paths()
        assert p.runtime_dir not in p.selector_geometry.parents
        assert p.state_dir in p.selector_geometry.parents

    def test_logs_are_not_on_tmpfs(self, monkeypatch, tmp_path):
        """Logs must survive the session that produced them.

        XDG_RUNTIME_DIR is wiped by the session manager, so a log written there
        is gone by the time anyone reads it after a failure. State home is the
        one location that persists for this user.
        """
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        p = paths.default_paths()
        for path in (p.log, p.selector_log):
            assert p.runtime_dir not in path.parents, path
            assert p.state_dir in path.parents, path

    def test_ensure_creates_the_runtime_dir_private(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        p = paths.default_paths()
        p.ensure()
        mode = stat.S_IMODE(p.runtime_dir.stat().st_mode)
        assert mode == 0o700, oct(mode)

    def test_ensure_tightens_the_mode_of_an_existing_directory(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        p = paths.default_paths()
        p.runtime_dir.mkdir(parents=True, exist_ok=True)
        p.runtime_dir.chmod(0o755)
        p.ensure()
        # mkdir's mode argument does not apply to a directory that already exists,
        # so a pre-existing 0755 has to be corrected explicitly.
        assert stat.S_IMODE(p.runtime_dir.stat().st_mode) == 0o700

    def test_ensure_survives_a_read_only_parent(self, monkeypatch, tmp_path):
        # An unwritable filesystem is a reason not to be private, not a crash.
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "nope"))
        p = paths.default_paths()
        p.runtime_dir.mkdir(parents=True, exist_ok=True)
        p.runtime_dir.chmod(0o555)
        try:
            p.ensure()
        finally:
            p.runtime_dir.chmod(0o700)


class TestOverridesStillWin:
    def test_an_explicit_override_beats_every_base_directory(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
        monkeypatch.setenv("KIZURIUM_TRANSLATOR_CONFIG", str(tmp_path / "mine.toml"))
        monkeypatch.setenv("KIZURIUM_TRANSLATOR_CACHE", str(tmp_path / "mine-cache"))
        p = paths.default_paths()
        assert p.config_file == tmp_path / "mine.toml"
        assert p.cache_dir == tmp_path / "mine-cache"

    def test_every_location_can_be_overridden(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        names = ("CONFIG", "CACHE", "TRANSLATE_CACHE", "LOCK", "PID", "TEXT_PID",
                 "LOG", "SELECTOR_LOG", "SELECTOR_REQUEST", "SELECTOR_LOCK",
                 "SELECTOR_GEOMETRY")
        for name in names:
            monkeypatch.setenv(paths.ENV_PREFIX + name, str(tmp_path / f"{name}.x"))
        p = paths.default_paths()
        assert p.config_file == tmp_path / "CONFIG.x"
        assert p.lock == tmp_path / "LOCK.x"
        assert p.pid == tmp_path / "PID.x"
        assert p.text_pid == tmp_path / "TEXT_PID.x"
        assert p.log == tmp_path / "LOG.x"
        assert p.selector_log == tmp_path / "SELECTOR_LOG.x"
        assert p.selector_request == tmp_path / "SELECTOR_REQUEST.x"
        assert p.selector_lock == tmp_path / "SELECTOR_LOCK.x"
        assert p.selector_geometry == tmp_path / "SELECTOR_GEOMETRY.x"


class TestNoHardcodedHome:
    def test_nothing_hardcodes_the_home_config_directory(self):
        source = (Path(paths.__file__)).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in source.splitlines()
                         if not ln.lstrip().startswith("#"))
        # The only literal ~/.config allowed is inside the XDG fallback itself.
        assert code.count('.config"') <= 1, code

    def test_config_module_does_not_hardcode_the_home_directory(self):
        source = Path(config.__file__).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in source.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert code.count('".config"') <= 1, code
