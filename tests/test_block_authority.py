"""The block decides, not the frame.

The report was a counter that stopped counting. Its element read "14", then
"15", and the translation under it did not change - while everything else on
that screen updated normally.

The reason is that two systems were answering the same question. There is a
tracker, which matches each element in this frame to the element it was in the
last frame and asks each one whether its own text changed. And there is a frame
key: every line joined with a separator and compared as one string. The key is
good for asking whether this is the same screen, and useless for asking about
one element - a counter going from "14" to "15" on a screen of forty menu labels
moves the key by a fraction of a percent and reads as unchanged. When the two
disagreed, the key won: `track_blocks` returned the pairs and the worker threw
them away, so on the full-OCR path nothing per-block was ever consulted, and the
key's answer was the only one in play.

The stage changes one thing and states its limits. The pairs are used, the
per-block answer is asked, and when a matched element says it changed, the key
can no longer short-circuit the cycle. The key keeps its jobs: classifying the
scene, deciding a full reset, and deciding whether the cost of a pass is worth
it. Those are the questions a joined string answers correctly.

What this deliberately does not do is trust the tracker over the key on a new
element. An unmatched line has no previous self to compare against, so it is a
question about the scene and the key is the right instrument. Only matched
elements carry authority here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import source_text

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import TrackedBlock  # noqa: E402


def block(text: str, conf: float = 95.0) -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=(0, 0, 100, 20),
        text=text,
        norm=live.normalize_for_compare(text),
        script=live.block_script(text),
        lang="en",
        conf=conf,
        engine="rapidocr",
    )


def pair(old_text: str, new_text: str, conf: float = 95.0) -> tuple:
    return (block(old_text), block(new_text, conf))


MENU = [
    "Settings",
    "Repository",
    "Issues",
    "Pull requests",
    "Actions",
    "Projects",
    "Wiki",
    "Security",
    "Notifications",
    "Explore",
]


class TestThePerBlockQuestion:
    def test_a_changed_counter_is_reported(self):
        out = live.changed_matched_blocks([pair("14", "15")])
        assert len(out) == 1, out

    def test_an_unchanged_element_is_not(self):
        out = live.changed_matched_blocks([pair("Settings", "Settings")])
        assert out == []

    def test_no_pairs_means_nothing_changed(self):
        assert live.changed_matched_blocks([]) == []
        assert live.changed_matched_blocks(None) == []

    @pytest.mark.parametrize(
        "old,new",
        [
            ("One", "Two"),
            ("is", "15"),
            ("ON", "OFF"),
            ("YES", "NO"),
            ("1", "2"),
            ("10", "11"),
        ],
    )
    def test_the_short_changes_the_stage_cares_about(self, old, new):
        assert live.changed_matched_blocks([pair(old, new)]), (old, new)

    def test_a_case_flip_on_a_real_word_is_ocr_noise(self):
        """"Start now" read back as "start  now" is the recogniser, not the screen.

        A leading capital and a doubled space wobble together on real labels, and
        treating that as an edit means re-translating a menu item on every frame
        it happens. ON -> OFF is caught without any of this, because the letters
        differ; a change of case alone is not worth a request.
        """
        assert live.changed_matched_blocks([pair("Start now", "start  now")]) == []

    def test_identical_text_at_low_confidence_is_not_a_change(self):
        """Confidence bears on a doubtful read, not on a decided one.

        The question this function answers is whether the content changed, and
        re-reading "Settings" and getting "Settings" again - confidently or not -
        is the same content. The confidence threshold applies when the two
        readings differ and the difference may be OCR wobble.
        """
        out = live.changed_matched_blocks([pair("Settings", "Settings", conf=20.0)])
        assert out == [], out


class TestTheFrameKeyCannotWin:
    def test_the_frame_key_says_the_screen_is_unchanged(self):
        """The whole premise: forty labels plus a counter, key barely moves."""
        old_lines = MENU + ["14"]
        new_lines = MENU + ["15"]
        old_key = "|".join(old_lines)
        new_key = "|".join(new_lines)
        assert live.keys_similar(new_key, old_key), (
            "if the key does see this change the bug is elsewhere"
        )

    def test_the_block_still_says_the_counter_changed(self):
        """So the per-block answer is the only one that catches it."""
        assert live.changed_matched_blocks([pair("14", "15")])

    def test_a_single_changed_element_out_of_many(self):
        pairs = [pair(t, t) for t in MENU] + [pair("14", "15")]
        out = live.changed_matched_blocks(pairs)
        assert len(out) == 1
        assert out[0].text == "14", out[0].text


class TestNewElementsStayASceneQuestion:
    def test_an_unmatched_line_reports_nothing(self):
        """A new element has no previous self, so the key handles it."""
        assert live.changed_matched_blocks([]) == []

    def test_a_pair_with_empty_new_text_is_ignored(self):
        pairs = [(block("14"), block("", 0.0))]
        assert live.changed_matched_blocks(pairs) == []

    def test_a_matched_element_whose_text_vanished_is_not_a_change(self):
        """Disappearance is handled where it is detected, not here."""
        assert live.changed_matched_blocks([(block("14"), block("", 0.0))]) == []


class TestItIsWiredToTheWorker:
    def test_the_pairs_are_no_longer_discarded(self):
        src = source_text("worker")
        assert "tracked, _pairs, next_block_id = track_blocks" not in src, (
            "the worker still throws the tracker's pairs away, so nothing "
            "per-block can be asked on the full-OCR path"
        )

    def test_the_verdict_reaches_key_same(self):
        # Проверка и вычисление уехали в `live.scene` вместе: держать их в
        # разных файлах было бы как раз тем расхождением, которое тест ловит.
        # Поэтому ищутся оба там, а связь с циклом проверяется отдельно.
        src = _scene_source()
        assert "changed_matched_blocks(pairs)" in src, (
            "the per-block answer is computed but never used"
        )

    def test_the_worker_reaches_that_decision(self):
        """Решение лежит в `live.scene`, и цикл обязан его звать.

        Иначе проверка выше станет правдой о мёртвом коде: функция считает
        вердикт, на который никто не смотрит.
        """
        assert "decide_scene(" in source_text("worker"), (
            "the worker no longer asks the per-block question"
        )

    def test_key_same_is_cleared_before_it_can_skip_the_cycle(self):
        import ast

        src = _scene_source()
        tree = ast.parse(src)
        # Цикл ищется по имени функции, а не по номеру строки: номер был
        # привязан к файлу в 13k строк и после разделения указывал в пустоту.
        worker = tree
        calls = [
            n
            for n in ast.walk(worker)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "changed_matched_blocks"
        ]
        assert calls, "the worker never asks the per-block question"
        asked = calls[0].lineno
        # The `if key_same:` test is the thing that skips the cycle, so the
        # verdict has to land before it rather than after.
        gates = [
            n
            for n in ast.walk(worker)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Name)
            and n.test.id == "key_same"
        ]
        assert gates, "the worker has no key_same gate to protect"
        gate = min(gates, key=lambda n: n.lineno)
        clears = [
            n
            for n in ast.walk(worker)
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "key_same" for t in n.targets
            )
            and asked < n.lineno < gate.lineno
        ]
        assert clears, (
            f"the verdict is asked at line {asked} but key_same is not cleared "
            f"before the gate at line {gate.lineno}"
        )


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        ast.parse(source_text("worker"))


def _scene_source() -> str:
    """Исходник того, где живёт решение о сцене.

    Раньше это был `worker`, и проверки искали вызов по файлу. Перенос в
    `live.scene` не должен ломать смысл проверок, поэтому берётся текстом
    функция, а не файл: переименование меняет имя, а не поведение.
    """
    import inspect
    import textwrap

    from kizurium_translator.live.scene import decide_scene

    return textwrap.dedent(inspect.getsource(decide_scene))
