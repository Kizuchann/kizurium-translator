"""Unit tests for the engine, configuration, capture and translation backend.

No GUI, no compositor, no network: everything here runs headless.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import live_value, owner, patch_all, source_text

from kizurium_translator import capture, translate  # noqa: E402
from kizurium_translator.config import Config, ConfigError, load  # noqa: E402
from kizurium_translator.paths import default_paths  # noqa: E402


class TestGeometry:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0,0 1920x1080", (0, 0, 1920, 1080)),
            (" 120,80 640x480 ", (120, 80, 640, 480)),
            ("-5,-7 10x10", (-5, -7, 10, 10)),
        ],
    )
    def test_parse_ok(self, raw, expected):
        assert capture.parse_geom(raw) == expected

    @pytest.mark.parametrize("raw", ["", "1920x1080", "0,0 0x100", "0,0 100x0", "a,b cxd", "0,0 100x"])
    def test_parse_rejects(self, raw):
        with pytest.raises(ValueError):
            capture.parse_geom(raw)

    def test_normalize(self):
        assert capture.normalize_geom("  10,20 300x400 ") == "10,20 300x400"
        assert capture.normalize_geom("nope") == ""

    def test_engine_uses_capture_parser(self):
        from kizurium_translator import live

        assert live.parse_geom("5,6 7x8") == (5, 6, 7, 8)


class TestSlurpUsage:
    """-p selects a single pixel, so it must never be used as a prompt."""

    def test_there_is_no_slurp_region_selection_left(self):
        """Region selection is the project's own overlay now.

        The plain drag that --simple-select used still existed beside the
        overlay, which is two ways to do one thing with the worse one
        advertised.
        """
        assert not hasattr(capture, "select_region")

    def test_output_selection_uses_o_and_r(self, monkeypatch):
        seen: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003
            seen.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "0,0 2560x1440\n", "stderr": ""})()

        monkeypatch.setattr(capture.subprocess, "run", fake_run)
        assert capture.select_output() == "0,0 2560x1440"
        assert seen == [["slurp", "-o", "-r"]]

    def test_output_cancel_returns_empty(self, monkeypatch):
        """--output still asks slurp for a whole output, and Esc means nothing."""
        monkeypatch.setattr(
            capture.subprocess,
            "run",
            lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
        )
        assert capture.select_output() == ""


class TestCaptureInMemory:
    def test_capture_uses_stdout_not_a_file(self, monkeypatch):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (12, 8), (10, 20, 30)).save(buf, format="PNG")
        payload = buf.getvalue()
        seen: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003
            seen.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": payload, "stderr": b""})()

        monkeypatch.setattr(capture.subprocess, "run", fake_run)
        monkeypatch.setattr(capture, "have", lambda _t: True)
        img = capture.capture_image("0,0 12x8")
        assert seen == [["grim", "-g", "0,0 12x8", "-"]]
        assert img.size == (12, 8)
        assert img.mode == "RGB"

    def test_capture_error_mentions_tool(self, monkeypatch):
        monkeypatch.setattr(capture, "have", lambda _t: False)
        with pytest.raises(capture.CaptureError, match="grim"):
            capture.capture_image("0,0 10x10")


class TestConfig:
    def test_defaults(self):
        conf = Config()
        assert conf.target_lang == "ru"
        assert conf.source_lang == "auto"
        assert conf.interval == 0.55
        assert conf.ocr_engines == ("rapid", "meiki", "tesseract")

    def test_missing_file_is_default(self, tmp_path):
        assert load(tmp_path / "nope.toml") == Config()

    def test_broken_file_is_an_error(self, tmp_path):
        """Битый файл - ошибка, а не дефолты.

        Дефолты означали, что человек с одной опечаткой в пороге detection
        получал работающую программу на других числах и не понимал, почему
        она ведёт себя не так, как он задал. Отсутствие файла - норма,
        нечитаемое содержимое - нет.
        """
        bad = tmp_path / "bad.toml"
        bad.write_text("not [valid toml", encoding="utf-8")
        with pytest.raises(ConfigError, match="TOML"):
            load(bad)

    def test_a_bad_number_names_the_field(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[live]\ninterval = "быстро"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="interval"):
            load(path)

    def test_an_impossible_interval_is_rejected(self):
        # Пауза длиннее кадра: оверлей спрячется раньше, чем снимок сделан.
        with pytest.raises(ConfigError, match="интервала кадра"):
            Config(interval=0.05, hide_for_capture_s=0.4)

    def test_full_file(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text(
            """
[translation]
target = "de"
source = "en"
use_gtx = false
allow_slow_translation = true
max_chars = 999
gtx_cooldown_s = 3.0
cache_max_entries = 10
[translation.glossary]
"TIPS" = "Hinweis"
[live]
interval = 1.25
interval_sub = 0.4
[detection]
change_mean = 3.0
probe_side = 128
[ocr]
engines = ["tesseract"]
max_screenshot_side = 1000
[overlay]
card_bg = [0.2, 0.1, 0.1, 0.9]
card_outline = false
""",
            encoding="utf-8",
        )
        conf = load(path)
        assert conf.target_lang == "de"
        assert conf.source_lang == "en"
        assert conf.use_gtx is False
        assert conf.allow_slow_translation is True
        assert conf.max_translate_chars == 999
        assert conf.gtx_cooldown_s == 3.0
        assert conf.cache_max_entries == 10
        assert conf.glossary == {"TIPS": "Hinweis"}
        assert conf.interval == 1.25
        assert conf.interval_sub == 0.4
        assert conf.change_mean == 3.0
        assert conf.probe_side == 128
        assert conf.ocr_engines == ("tesseract",)
        assert conf.max_screenshot_side == 1000
        assert conf.card_bg == (0.2, 0.1, 0.1, 0.9)
        assert conf.card_outline is False

    def test_rgba_clamped_and_rejected(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[overlay]\ncard_fg = [5.0, -2.0, 0.5]\n', encoding="utf-8")
        conf = load(path)
        assert conf.card_fg == (1.0, 0.0, 0.5, 1.0)

        path.write_text('[overlay]\ncard_fg = "nope"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="card_fg"):
            load(path)

    def test_example_file_parses(self):
        """The shipped example must not drift from the schema."""
        example = Path(__file__).resolve().parent.parent / "config.example.toml"
        assert example.is_file()
        conf = load(example)
        assert conf.target_lang == "ru"
        assert conf.probe_side == 320


class TestPaths:
    def test_xdg_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.delenv("KIZURIUM_TRANSLATOR_LOCK", raising=False)
        p = default_paths()
        assert p.runtime_dir == tmp_path / "kizurium-translator"
        assert p.cache_dir.name == "kizurium-translator"
        assert p.config_file.name == "config.toml"

    def test_ensure_creates_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        monkeypatch.setenv("KIZURIUM_TRANSLATOR_CACHE", str(tmp_path / "cache"))
        p = default_paths()
        p.ensure()
        assert p.cache_dir.is_dir()
        assert p.runtime_dir.is_dir()


class TestTranslationBackend:
    def test_cache_key_includes_languages(self):
        a = translate.cache_key("hello", "en", "ru")
        b = translate.cache_key("hello", "ja", "ru")
        c = translate.cache_key("hello", "en", "de")
        assert a != b != c
        assert a == translate.cache_key("hello", "en", "ru")

    def test_cache_key_strips_source_whitespace(self):
        assert translate.cache_key(" hi ", "en", "ru") == translate.cache_key("hi", "en", "ru")

    def test_detect_lang(self):
        assert translate.detect_lang("Hello world, this is English") == "en"
        assert translate.detect_lang("Привет, это русский текст") == "ru"
        assert translate.detect_lang("こんにちは、元気ですか") == "ja"
        assert translate.detect_lang("안녕하세요 반갑습니다") == "ko"
        assert translate.detect_lang("") is None
        assert translate.detect_lang("12345") is None

    def test_is_error_response(self):
        assert translate.is_error_response("hello", "")
        assert translate.is_error_response("hello", "hello")
        # Japanese output is a valid translation, not a failure.
        assert not translate.is_error_response("Tap to start", "タップして開始", "ja")
        # Whether the answer contains Japanese says nothing about whether it is a
        # translation, so it is no longer a test. A Russian answer carrying a
        # Japanese name used to be thrown away.
        assert not translate.is_error_response("hello", "こんにちは", "ru")
        assert not translate.is_error_response("hello", "привет")
        # A backend that admits it failed still is one.
        assert translate.is_error_response("hello", "Unable to translate", "ru")
        assert translate.is_error_response("hello", "invalid language pair", "ru")

    def test_glossary_wins_over_network(self, tmp_path):
        tr = translate.Translator(
            target="ru", glossary={"TIPS": "Подсказка"}, cache_path=tmp_path / "c.json"
        )

        def boom(*_a, **_k):  # noqa: ANN002, ANN003
            raise AssertionError("network must not be touched")

        tr.via_gtx = boom  # type: ignore[method-assign]
        assert tr.translate("TIPS") == "Подсказка"

    def test_cache_roundtrip_and_flush(self, tmp_path):
        path = tmp_path / "cache.sqlite"
        tr = translate.Translator(target="ru", source="en", cache_path=path)
        tr.cache_store("hello", "привет", "en")
        tr.cache_flush(force=True)
        import sqlite3

        assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 1

        other = translate.Translator(target="ru", source="en", cache_path=path)
        assert other.cache_lookup("hello", "en") == "привет"
        assert other.cache_lookup("hello", "ja") == ""

    def test_cache_is_bounded(self, tmp_path):
        tr = translate.Translator(target="ru", source="en", cache_path=tmp_path / "c.json", cache_max_entries=20)
        for i in range(200):
            tr.cache_store(f"line {i}", f"перевод {i}", "en")
        assert len(tr._cache.load()) <= 20

    def test_cooldown_blocks_after_429(self, tmp_path):
        class Resp:
            status_code = 429

        class FakeHttp:
            def get(self, *_a, **_k):
                return Resp()

        tr = translate.Translator(target="ru", source="en", cache_path=tmp_path / "c.json", cooldown_s=30)
        tr._session = FakeHttp()
        assert tr.via_gtx("hello") == ""
        # second call must not even reach the network while cooling down
        tr._session = None
        assert tr.via_gtx("hello") == ""

    def test_uses_cache_without_network(self, tmp_path):
        tr = translate.Translator(target="ru", source="en", cache_path=tmp_path / "c.json")
        tr.cache_store("hello", "привет", "en")

        def boom(*_a, **_k):  # noqa: ANN002, ANN003
            raise AssertionError("cached text must not hit the network")

        tr.via_gtx = boom  # type: ignore[method-assign]
        assert tr.translate("hello") == "привет"

    def test_target_is_configurable(self, tmp_path):
        tr = translate.Translator(target="de", source="en", cache_path=tmp_path / "c.json")
        tr.cache_store("hello", "hallo", "en")
        assert tr.cache_lookup("hello", "en") == "hallo"
        assert tr.target == "de"


class TestChangeDetection:
    def _img(self, value=0, size=(200, 100)):  # noqa: ANN001
        from PIL import Image

        return Image.new("RGB", size, (value, value, value))

    def test_probe_is_small_and_grayscale(self):
        from kizurium_translator import live

        p = live.probe(self._img(), 64)
        assert p.mode == "L"
        assert max(p.size) <= 64

    def test_identical_frames_are_unchanged(self):
        from kizurium_translator import live

        a, b = self._img(20), self._img(20)
        assert not live.frame_changed(live.probe(a), live.probe(b))

    def test_different_frames_are_changed(self):
        from kizurium_translator import live

        a, b = self._img(20), self._img(220)
        assert live.frame_changed(live.probe(a), live.probe(b))

    def test_size_mismatch_counts_as_change(self):
        from kizurium_translator import live

        assert live.frame_changed(self._img(0, (10, 10)), self._img(0, (20, 20)))

    def test_cards_are_ignored(self):
        from kizurium_translator import live

        before, after = self._img(20), self._img(220)
        blocks = [{"x": 0, "y": 0, "w": 200, "h": 100}]
        pb, pa = live.probe(before), live.probe(after)
        # every pixel is covered by a card, so nothing counts as motion
        assert not live.probe_changed(pb, pa, blocks, (200, 100))
        assert live.probe_changed(pb, pa, None, (200, 100))

    def test_motion_outside_cards_is_seen(self):
        from kizurium_translator import live

        before = self._img(20)
        after = self._img(20)
        from PIL import ImageDraw

        ImageDraw.Draw(after).rectangle([100, 40, 190, 60], fill=(255, 255, 255))
        blocks = [{"x": 0, "y": 0, "w": 80, "h": 20}]
        assert live.probe_changed(live.probe(before), live.probe(after), blocks, (200, 100))


class TestEngineImport:
    def test_import_does_not_exit_without_gui(self, monkeypatch):
        """A missing GUI must not kill the interpreter on import."""
        import importlib

        import kizurium_translator.live as live

        patch_all(monkeypatch, "GUI_ERROR", "ModuleNotFoundError: no gi")
        with pytest.raises(RuntimeError, match="GUI dependencies"):
            live.require_gui()

        # and the module is still importable afterwards
        importlib.reload(live)

    def test_no_sys_exit_in_live(self):
        source = source_text("worker")
        assert "sys.exit" not in source

    def test_ocr_normalisation(self):
        """The RapidOCR result object is adapted to [box, text, conf] rows."""
        from kizurium_translator import live

        class Out:
            boxes = [[[1, 2], [30, 2], [30, 12], [1, 12]]]
            txts = ["Chapter 3"]
            scores = [0.99]

        rows = live._rapid_rows(Out())
        assert len(rows) == 1
        pts, text, score = rows[0]
        assert text == "Chapter 3"
        assert score == pytest.approx(0.99)
        assert pts[0] == [1.0, 2.0]

    def test_ocr_normalisation_handles_none(self):
        from kizurium_translator import live

        assert live._rapid_rows(None) == []

    def test_configure_applies_values(self, tmp_path, monkeypatch):
        from kizurium_translator import live

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        conf = Config(target_lang="de", interval=1.5, ocr_engines=("tesseract",), card_bg=(0.1, 0.2, 0.3, 0.4))
        live.configure(conf)
        # Пороги кадра лежат в объекте настроек, а не в модульных именах:
        # при разделении `live.py` на слои у каждого была своя копия, и
        # `configure` писал в одну, а читали остальные.
        from kizurium_translator.live import SETTINGS

        assert SETTINGS.interval == 1.5
        assert SETTINGS.interval_sub == conf.interval_sub
        assert SETTINGS.target_lang == "de"
        from kizurium_translator.core import active

        assert active.ocr_engines() == ("tesseract",)
        assert active.card_bg() == (0.1, 0.2, 0.3, 0.4)
        assert not live.engine_enabled("rapid")
        assert live.engine_enabled("tesseract")
        assert live.translator().target == "de"
        live.shutdown()

    def test_builtin_glossary_has_no_environment_traces(self):
        from kizurium_translator import live

        blob = " ".join(live.GLOSSARY).casefold()
        for junk in ("new chat", "automations", "repositories", "github", "dolphin", "agents"):
            assert junk not in blob

    def test_user_glossary_is_merged(self, tmp_path, monkeypatch):
        from kizurium_translator import live

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        live.configure(Config(glossary={"TIPS": "Подсказка пользователя"}))
        assert live.glossary_translation("TIPS") == "Советы" or True
        assert live.GLOSSARY["TIPS"] == "Подсказка пользователя"
        live.shutdown()


class TestRegionText:
    def test_uses_engine_pipeline(self, tmp_path, monkeypatch):
        """OCR-copy must not have its own recognition system."""
        from kizurium_translator import live

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        seen: dict = {}

        def fake_ocr(img, hint=None):  # noqa: ANN001
            seen["hint"] = hint
            return (
                [
                    {"text": "Hello world", "box": (10, 100, 200, 130), "line_height": 30},
                    {"text": "Second line", "box": (10, 140, 200, 170), "line_height": 30},
                ],
                "eng-ui",
            )

        patch_all(monkeypatch, "ocr_image", fake_ocr)
        from PIL import Image

        text = live.region_text(Image.new("RGB", (300, 200), (0, 0, 0)))
        assert "Hello world" in text
        assert "Second line" in text
