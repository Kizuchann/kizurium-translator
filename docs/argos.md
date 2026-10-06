# Argos Translate — optional candidate

## Status

Argos is **not** wired into the live Translator path and is **not** the
offline guarantee. CTranslate2 + OPUS-MT packs remain the supported local MT.

Upstream: https://github.com/argosopentech/argos-translate  
Known concern: https://github.com/argosopentech/argos-translate/issues/385
(helper models / networking during “offline” use).

## Required proof before core enablement

```text
network blocked
→ local translation still works
```

Probe:

```python
from kizurium_translator.translation.argos_probe import probe_argos

print(probe_argos())
```

| result | meaning |
|--------|---------|
| `available=False` | package not installed — optional only |
| `offline_ok=False` | installed but touches network or missing packs |
| `strict_offline_allowed=True` | only then Argos may be considered for core use |

If the probe sees a hidden network dependency, Argos stays disabled for
`offline_only` mode even when the package is present.

## Install (manual, optional)

```bash
pip install argostranslate
# then install language packages via Argos' own CLI/API — not via our catalog
```

We deliberately do **not** add `argostranslate` to `pyproject.toml` extras yet:
it is large, pulls its own model store, and has not passed the offline gate on
this machine as a default dependency.
