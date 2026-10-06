"""Нормальный перевод не должен отбрасываться как мусор OCR.

Реальный случай, 2026-10-03, окно ws2 (Evil Time, пропуск пролога):

    исходник:  Body temperature low... administering Hexamethas
    перевод:   Низкая температура тела... вводим Hexamethasone 20

Перевод нормальный, но на экране было `shown=0 retry` бесконечно. Причина
в том, что отбрасывающий фильтр применялся к переводу, а не к источнику, и
обе его половины ошибались на этой строке:

* `is_mostly_russian` считал кириллицу по буквам, а не по гласным, и требовал
  0.72. На этой строке доля 0.675 - ниже порога, хотя строка явно русская.
  Одно латинское слово `Hexamethasone` (13 букв из 40) утянуло долю вниз.
* `is_garbage_ocr` считал латиницу посимвольно (`RE_LAT` без `+`), поэтому
  `len(lat) >= 4` означало "четыре отдельные буквы", а не "четыре слова". Одно
  нормальное латинское слово открывало ветку "латиница без нормальных слов",
  и условие `len(good) == 1 and len(t) > 18` срабатывало на нормальном тексте.

Вторая ошибка опаснее первой: `is_garbage_ocr` - это фильтр OCR-каши, и он
не должен решать, жив ли перевод. Здесь он и решил, что перевода нет.

Что проверяется:

* перевод, который в норме должен показаться, проходит фильтры;
* конкретные строки из живого лога не отбрасываются;
* отбрасывание мусора по-прежнему работает - иначе фильтр можно было бы
  просто удалить, и это был бы другой, тоже неправильный ответ.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.layout.grouping import is_mostly_russian  # noqa: E402
from kizurium_translator.ocr.engine import is_garbage_ocr  # noqa: E402

# Строки из живого лога: (исходник, перевод) - обе должны дойти до экрана.
LIVE_CASES = [
    (
        "Body temperature low... administering Hexamethas",
        "Низкая температура тела... вводим Hexamethasone 20",
    ),
    ("Distant Voice", "Далекий голос"),
    ("AUTO", "Авто"),
    ("OFF", "ВЫКЛ."),
    ("SKIP", "Пропустить"),
    # из того же окна, длинные реплики
    (
        "In the heart of Chernobog a young girl",
        "В самом сердце Chernobog молодая девушка",
    ),
    (
        "The girl named Amia tries to explain",
        "Девушка по имени Амия пытается объяснить",
    ),
]


def _drop_conditions() -> list[str]:
    """Условия, отбрасывающие перевод как мусор OCR, из тела сборки карточек.

    Фильтр применяется к переводу, и именно это проверяется: условие берётся
    текстом из исходника, поэтому проверяется то, что реально выполнится, а не
    старый список. Ищется в `live.build`: сборка карточек вынесена из
    `worker()` в отдельную функцию, и условие переехало вместе с ней.
    """
    import ast
    import textwrap

    from kizurium_translator.live import build as _build

    source = pathlib.Path(_build.__file__).read_text()
    tree = ast.parse(source)
    fn = next(
        n
        for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_blocks"
    )
    return [
        ast.unparse(node.test)
        for node in ast.walk(fn)
        if isinstance(node, ast.If) and "is_garbage_ocr" in ast.unparse(node.test)
    ]


def _mt_dropped(src: str, dst: str) -> bool:
    """Повторяет условие отбрасывания из `live.build.build_blocks` дословно."""
    conditions = _drop_conditions()
    assert conditions, "в build_blocks() нет условия, отбрасывающего перевод как мусор OCR"
    import textwrap

    # Выражение собирается в функцию и вызывается с теми же аргументами.
    code = (
        "def _drop(translated, dialogue_mode, japanese_mode):\n"
        "    return bool(\n"
        + textwrap.indent(conditions[0], "        ")
        + "\n    )\n"
    )
    ns: dict = {
        "is_mostly_russian": is_mostly_russian,
        "is_garbage_ocr": is_garbage_ocr,
    }
    exec(compile(code, "<drop>", "exec"), ns)  # noqa: S102 - исходник проекта
    return ns["_drop"](dst, False, False)


class TestAGoodTranslationIsNotGarbage:
    """Перевод доходит до экрана."""

    @pytest.mark.parametrize(("src", "dst"), LIVE_CASES, ids=lambda v: v[:22])
    def test_the_live_translation_is_not_dropped(self, src, dst):
        assert not _mt_dropped(src, dst), (
            f"перевод отброшен как мусор: {dst!r}"
        )

    def test_a_single_latin_word_does_not_open_the_latin_garbage_branch(self):
        """Русский текст с одним латинским словом - это нормальный перевод.

        Отдельный случай, потому что именно он ломался. Ветка «латиница без
        нормальных слов» открывалась на любом слове: порог `len(lat) >= 4`
        считает буквы, а не слова, и `Hexamethasone` даёт 13.
        """
        dst = "Низкая температура тела... вводим Hexamethasone 20"
        assert not is_garbage_ocr(dst), (
            "русский текст с одним латинским словом не должен считаться мусором"
        )

    def test_the_latin_branch_still_opens_on_pure_latin_noise(self):
        """Обратный ход: правка не выключила фильтр, а сузила его.

        Без этой проверки «починить» можно было бы и удалением функции
        `is_garbage_ocr`, и это был бы другой, тоже неправильный ответ.
        """
        from kizurium_translator.core.scripts import dominant_script

        assert dominant_script("Mw (r ~ 6-8 nMkcenen)") == "latn"
        assert dominant_script("Низкая температура тела... вводим Hexamethasone 20") == "cyrl"


class TestGarbageIsStillGarbage:
    """Фильтр нельзя просто выключить."""

    @pytest.mark.parametrize(
        "text",
        [
            # Живой мусор из лога OCR по траве.
            "Mw (r ~ 6-8 nMkcenen)",
            "Mq rk zz pp ww vv nn bb cc dd ff",
            "|  >< # @ @",
        ],
    )
    def test_ocr_noise_is_still_rejected(self, text):
        assert is_garbage_ocr(text), f"мусор прошёл фильтр: {text!r}"

    def test_a_real_english_sentence_is_not_garbage(self):
        assert not is_garbage_ocr("Body temperature low, administering Hexamethasone")

    def test_a_real_russian_sentence_with_a_number_is_not_garbage(self):
        """Тот же случай, что в логе, целиком - с цифрой в конце.

        Цифра была частью отбрасывания: её не должно быть достаточно,
        чтобы перевод пропал.
        """
        assert not is_garbage_ocr("Босс повержен. Получено 250 очков опыта")
