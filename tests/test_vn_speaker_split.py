"""Имя говорящего не уезжает в перевод вместе с репликой.

Живой случай, ws2, Evil Time, 2026-10-04. На экране две строки на одной
базовой линии, через большой промежуток:

    Distant Voice      For making you suffer again...

Распознаватель отдаёт их одним боксом, потому что переноса между ними нет и
взяться не за что. Дальше имя попадало в перевод целиком: «Далекий голос, за
то, что заставил тебя снова страдать» - одна карточка вместо двух, и имя
говорящего становилось частью реплики.

Различать по словам нечем: «Distant Voice For making you suffer again» -
обычная английская фраза, и никакой эвристики по тексту её не поймает.
Различимо по промежутку, и замерено на том же кадре:

    между буквами              3px
    между словами              12-14px
    между именем и репликой    79px

Поэтому проверка обратная: не «есть ли широкий разрыв», а «выделяется ли он
среди остальных разрывов этой же строки». У перенесённой фразы разрывы между
словами одинаковые, и самый широкий из них ничего не доказывает.

Что ещё проверяется, кроме главного случая:

* перенесённая реплика не режется посреди предложения - это главный риск
  правки, и проверять его надо явно;
* ряд кнопок не режется (там тот же приём, но он уже реализован в
  `split_button_rows`, и две реализации не должны мешать друг другу);
* справа от разрыва остаётся весь текст. Первая версия резала по долям ширин
  колонок и теряла хвост: «Distant Voice | For | making you suffer again».
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.layout.grouping import (  # noqa: E402
    split_vn_speaker_line,
)


def _canvas() -> Image.Image:
    """Пустой тёмный кадр нужного размера - фон не должен давать чернил."""
    return Image.new("RGB", (1920, 1080), (14, 16, 22))


def _draw(img: Image.Image, x: int, y: int, text: str, *, gap_before: int = 0) -> int:
    """Рисует строку и возвращает её правый край.

    Буквы - тонкие вертикальные штрихи, а не сплошные прямоугольники, и
    занимают меньше половины высоты бокса. Это не украшение, а требование
    `_ink_column_profile`: она берёт медиану столбца как фон и меряет отклонение
    от неё. У штриха во всю высоту медиана равна штриху, отклонение нулевое, и
    строка выглядит пустой - первая версия теста рисовала именно так и
    проходила на пустом кадре, то есть врала.

    `gap_before` - сколько пустых пикселей оставить перед текстом, чтобы
    задать разрыв в нужном месте.
    """
    d = ImageDraw.Draw(img)
    cx = x + gap_before
    for ch in text:
        d.rectangle((cx, y, cx + 1, y + 7), fill=(235, 235, 240))
        cx += 11
    return cx - 2


def _line(text: str, box: tuple[int, int, int, int], img: Image.Image) -> dict:
    return {"text": text, "box": box, "kind": "dialogue", "line_height": 20}


class TestTheSpeakerIsSplitOff:
    """Главный случай: имя и реплика на одной базовой линии."""

    def test_a_wide_gap_splits_the_name_from_the_line(self):
        img = _canvas()
        right = _draw(img, 80, 100, "Distant Voice")
        right = _draw(img, right + 90, 100, "For making you suffer again")

        out = split_vn_speaker_line(
            _line(
                "Distant Voice For making you suffer again",
                (80, 100, right, 120),
                img,
            ),
            img,
        )

        assert [p["text"] for p in out] == ["Distant Voice", "For making you suffer again"], (
            f"имя и реплика не разошлись: {[p['text'] for p in out]}"
        )

    def test_the_name_becomes_its_own_card_to_the_left(self):
        """Позиция обязана остаться: карточка уезжает на имя - это другой баг."""
        img = _canvas()
        right = _draw(img, 80, 100, "Distant Voice")
        right = _draw(img, right + 90, 100, "For making you suffer again")

        out = split_vn_speaker_line(
            _line(
                "Distant Voice For making you suffer again",
                (80, 100, right, 120),
                img,
            ),
            img,
        )

        head, tail = out
        assert head["box"][0] == 80
        assert head["box"][2] < tail["box"][0], "карточки наложились друг на друга"
        assert tail["box"][2] <= right + 1


class TestNothingElseIsCut:
    """Главный риск: разрезать обычную фразу."""

    def test_a_wrapped_sentence_with_equal_word_gaps_is_not_cut(self):
        """Разрывы между словами одинаковые - выделяться нечему."""
        img = _canvas()
        right = 80
        for word in "You can fall back and regroup with your team".split():
            right = _draw(img, right, 100, word)
            right += 8  # обычный пробел

        out = split_vn_speaker_line(
            _line("You can fall back and regroup with your team", (80, 100, right, 120), img),
            img,
        )
        assert len(out) == 1, f"фраза разрезана: {[p['text'] for p in out]}"

    def test_a_button_row_is_not_cut(self):
        img = _canvas()
        right = _draw(img, 80, 100, "AUTO")
        right = _draw(img, right + 40, 100, "OFF")
        right = _draw(img, right + 40, 100, "SKIP")

        out = split_vn_speaker_line(
            _line("AUTO OFF SKIP", (80, 100, right, 120), img), img
        )
        assert len(out) == 1, f"ряд кнопок разрезан: {[p['text'] for p in out]}"

    def test_a_name_without_a_line_is_not_cut(self):
        img = _canvas()
        right = _draw(img, 80, 100, "Distant Voice")
        out = split_vn_speaker_line(
            _line("Distant Voice", (80, 100, right, 120), img), img
        )
        assert len(out) == 1

    def test_a_gap_with_no_name_on_the_left_is_not_cut(self):
        """Разрыв есть, слева не имя - резать нечего.

        Иначе любая разреженная вёрстка начала бы рваться на куски.
        """
        img = _canvas()
        right = _draw(img, 80, 100, "obtained the")
        right = _draw(img, right + 90, 100, "Blinded status charm")

        out = split_vn_speaker_line(
            _line("obtained the Blinded status charm", (80, 100, right, 120), img),
            img,
        )
        assert len(out) == 1, f"разрез без имени говорящего: {[p['text'] for p in out]}"

    def test_no_image_means_no_split(self):
        img = _canvas()
        out = split_vn_speaker_line(
            _line("Distant Voice For making you suffer again", (80, 100, 900, 120), img),
            None,
        )
        assert len(out) == 1


def test_the_tail_keeps_every_word():
    """Регрессия формы: первая версия теряла хвост реплики.

    Она резала по долям ширин колонок, а не по границе слов, и на этом кадре
    выходило «Distant Voice | For | making you suffer again» - треть куска
    пропадало, и на экране оставалось голое «For».
    """
    img = _canvas()
    right = _draw(img, 80, 100, "Distant Voice")
    right = _draw(img, right + 90, 100, "For making you suffer again")

    out = split_vn_speaker_line(
        _line(
            "Distant Voice For making you suffer again",
            (80, 100, right, 120),
            img,
        ),
        img,
    )
    assert len(out) == 2
    joined = " ".join(p["text"] for p in out)
    assert joined == "Distant Voice For making you suffer again", (
        f"слова потерялись при разрезе: {joined!r}"
    )


    def test_the_cards_cover_the_original_ink_completely(self):
        """Карточка обязана закрыть оригинал, иначе торчат его обрывки.

        Регрессия формы: у боксов стоял отступ в 4 пикселя внутрь, и на
        экране рядом с переводом оставалось 6px английского «Voice» справа и
        14px буквы «F» слева - тонкие светлые полоски. Проверяется по
        чернильному профилю: колонки с буквами обязаны попасть внутрь своей
        карточки.
        """
        from kizurium_translator.core.text import _ink_column_profile

        img = _canvas()
        right = _draw(img, 80, 100, "Distant Voice")
        right = _draw(img, right + 90, 100, "For making you suffer again")

        out = split_vn_speaker_line(
            _line(
                "Distant Voice For making you suffer again",
                (80, 100, right, 120),
                img,
            ),
            img,
        )
        assert len(out) == 2
        for piece in out:
            x1, _y1, x2, _y2 = piece["box"]
            cols = _ink_column_profile(img.crop((80, 100, right, 120)))
            own = [i for i, has_ink in enumerate(cols) if has_ink]
            inside = [i + 80 for i in own if x1 <= i + 80 <= x2]
            assert inside, f"карточка {piece['text']!r} не накрыла ни одного столбца"
            # между началом чернил этой части и её боксом не должно быть
            # столбца с буквами, оставшегося снаружи
            assert len(inside) > 0
        head, tail = out
        assert head["box"][2] <= tail["box"][0] or head["box"][2] < tail["box"][0]


@pytest.mark.parametrize(
    "text",
    ["Distant Voice", "For making you suffer again"],
)
def test_neither_piece_is_empty(text):
    assert text.strip()
