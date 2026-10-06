# Contributing

AGPL-3.0-or-later, same as the project. A contribution is accepted under that
licence.

## What is useful

**A measurement, not an opinion.** A change that fixes a case someone saw on a
screen is worth more than a change that sounds more correct. Screenshot, the
frame it came from, and what the tool did instead of what it should have done.

**A test that fails before the fix.** Not one that passes either way.

**A pixel fixture when the change is about pixels.** The type corpus in
`tests/fixtures/type` works because the faces, their sizes and their properties
are known and committed. A bug in `measure_glyph_metrics` was found this way
and not by reading the code: the threshold had been set against a measurement
that returned a constant.

## How to work

```bash
uv venv --system-site-packages
uv sync --group dev

uv run pytest -q
uv run ruff check src/ tests/ scripts/
```

The overlay needs PyGObject, which `uv sync` does not install — it comes from
the distribution. `uv venv --system-site-packages` is what makes it visible.
`kizurium-translator --doctor` reports what is missing.

Live testing is a session, not a screenshot: start it, change scenes inside it,
and look at what stayed, what went and what did not appear.

## What to keep out

**The game in the core.** A title, a button name or a character is data. It
belongs in `data/profiles/` or a lexicon pack, enabled by `--profile`. The
translator sees pixels; it does not know what game is running.

**Comments that restate the code.** A comment earns its place by saying why a
decision was made, and the number the decision rests on. `t.comment = 0` needs
no help.

**Geometry fixed to one screen.** Thresholds scale through `core/scale.py` or as
a fraction of the frame width and height. A default region of `0,0 1920x1080` is
wrong on every machine but the one it was written on.

## Reports

A bug report is most useful with the frame it came from and the game's name.
Both are opt-in data in the repository — if the report needs a profile to
reproduce, say which one.