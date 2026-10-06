# Offline language packs + CTranslate2

## Для людей (нормальный путь)

После клона репы:

```bash
./install.sh
# спросить «офлайн-перевод?» → Enter (Y)
```

Или без вопросов:

```bash
./install.sh --system --offline-packs
```

Позже / отдельно:

```bash
kizurium-translator --pack-install all
kizurium-translator --packs
```

Модели лежат в ``$XDG_DATA_HOME/kizurium-translator/models/<id>/``.
Конвертировать OPUS-MT руками **не нужно** — каталог качает готовые CT2
артефакты с Hugging Face.

## Status

```bash
kizurium-translator --packs
```

Shows `Installed` or `Download (one command)` for each catalog slot
(`opus-mt-en-ru`, `opus-mt-ja-ru`).

## Strict offline

```toml
# ~/.config/kizurium-translator/config.toml
[translation]
offline_only = true
```

Or: `kizurium-translator --offline-only …`

In this mode the HTTP session / gtx / MyMemory backends are **never
instantiated**. Glossary, cache, translation memory, and installed CT2 packs
still work. Missing pack → log `language pack not installed` and an empty
result (no silent network fallback).

## Manual / developer path

Only if you already have a local CT2 directory:

```bash
pip install 'kizurium-translator[local-mt]'
kizurium-translator --pack-install opus-mt-en-ru --from /path/to/ct2-model
```

License: OPUS-MT models are CC-BY-4.0 — see `docs/licenses-inventory.toml`.

## What is *not* default

NLLB is intentionally **not** a catalog pack and not the default local backend.
First release uses pair-specific OPUS-MT models only. See
[licenses.md](licenses.md).
