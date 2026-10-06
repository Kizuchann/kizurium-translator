"""Local MT via CTranslate2 + OPUS-MT packs.

Uses an installed language pack (``models/<id>/model``).
Live defaults: ``compute_type=int8``, ``beam_size=1``. Missing optional
deps or missing packs are soft misses — online backends still run.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..threads import budget
from .packs import find_installed_pair


class CTranslate2Backend:
    backend_id = "ctranslate2"

    def __init__(
        self,
        model_dir: Path,
        *,
        compute_type: str = "int8",
        beam_size: int = 1,
        tokenizer_name: str | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.tokenizer_name = tokenizer_name
        self._lock = threading.Lock()
        self._translator = None
        self._tokenizer = None
        self._sp_source = None
        self._sp_target = None

    def supports(self, source: str, target: str) -> bool:
        return self.model_dir.is_dir()

    def _thread_budget(self) -> tuple[int, int]:
        """CTranslate2 inter/intra threads, from the process-wide budget.

        The total worker count is derived from the physical core count
        and the parts do not each guess. This used to read `cpus // 2` from
        `os.cpu_count()`, which on a 16-core laptop meant eight intra-op threads
        for a translation that runs *while* the OCR for the next frame is
        waiting - sixteen runnable threads on a machine that also has to capture
        a frame and paint it.
        """
        b = budget()
        return b.local_mt_inter, b.local_mt_intra

    def _load(self) -> None:
        if self._translator is not None:
            return
        import ctranslate2

        inter, intra = self._thread_budget()
        self._translator = ctranslate2.Translator(
            str(self.model_dir),
            compute_type=self.compute_type,
            inter_threads=inter,
            intra_threads=intra,
        )
        spm_src = self.model_dir / "source.spm"
        spm_tgt = self.model_dir / "target.spm"
        if spm_src.is_file() and spm_tgt.is_file():
            import sentencepiece as spm

            self._sp_source = spm.SentencePieceProcessor(model_file=str(spm_src))
            self._sp_target = spm.SentencePieceProcessor(model_file=str(spm_tgt))
            return
        # Fallback: a HuggingFace tokenizer, for a pack that shipped model weights
        # without the sentencepiece files.
        #
        # `local_files_only` inside `_load_tokenizer_only` is the whole point of
        # this branch. Without it, `from_pretrained` reaches huggingface.co to
        # fetch the tokenizer - and this runs *before* the `offline_only` check in
        # `Translator.translate`, because the local backend is tried first. So a
        # user who asked for strict offline, with a pack installed that has no
        #.spm, had their screen text leave the machine. Offline
        # has to mean zero network calls, not "fewer network calls".
        self._load_tokenizer_only()

    def _load_tokenizer_only(self) -> None:
        """The HuggingFace-tokenizer branch of `_load`, without the model.

        Split out so it can be tested without a CTranslate2 model on disk: the
        behaviour that matters here is whether `from_pretrained` is allowed to
        reach the network, and that does not depend on the weights.
        """
        name = self.tokenizer_name or self._guess_tokenizer_name()
        if not name:
            raise RuntimeError("no tokenizer for CTranslate2 pack")
        from transformers import AutoTokenizer

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(name, local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"tokenizer {name!r} is not in the local cache "
                f"({type(exc).__name__}); refusing to download during translation"
            ) from exc

    def _guess_tokenizer_name(self) -> str | None:
        # Common OPUS-MT pairs we ship slots for.
        marker = self.model_dir.parent.name
        mapping = {
            "opus-mt-en-ru": "Helsinki-NLP/opus-mt-en-ru",
            "opus-mt-ja-ru": "Helsinki-NLP/opus-mt-ja-ru",
        }
        return mapping.get(marker)

    def _encode(self, text: str) -> list[str]:
        self._load()
        if self._sp_source is not None:
            tokens = self._sp_source.encode(text, out_type=str)
            # Marian/OPUS-MT expects an end-of-sentence marker; without it the
            # decoder often loops on longer lines.
            if not tokens or tokens[-1] != "</s>":
                tokens = list(tokens) + ["</s>"]
            return tokens
        assert self._tokenizer is not None
        ids = self._tokenizer.encode(text)
        return self._tokenizer.convert_ids_to_tokens(ids)

    def _decode(self, tokens: list[str]) -> str:
        self._load()
        if self._sp_target is not None:
            return self._sp_target.decode(tokens)
        assert self._tokenizer is not None
        ids = self._tokenizer.convert_tokens_to_ids(tokens)
        return self._tokenizer.decode(ids, skip_special_tokens=True)

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if not texts:
            return []
        with self._lock:
            self._load()
            assert self._translator is not None
            batches = [self._encode(t) for t in texts]
            results = self._translator.translate_batch(
                batches,
                beam_size=self.beam_size,
                return_scores=False,
            )
            out: list[str] = []
            for item in results:
                hyp = item.hypotheses[0] if item.hypotheses else []
                out.append(self._decode(hyp).strip())
            return out


_BACKENDS: dict[tuple[str, str, int], CTranslate2Backend | None] = {}
_BACKENDS_LOCK = threading.Lock()


def local_backend_for(
    source: str,
    target: str,
    *,
    models_dir: Path | None = None,
    beam_size: int = 1,
) -> CTranslate2Backend | None:
    """Return a CT2 backend for an installed pack, or None."""
    key = (source, target, beam_size)
    with _BACKENDS_LOCK:
        if key in _BACKENDS:
            return _BACKENDS[key]
        model = find_installed_pair(source, target, models_dir=models_dir)
        if model is None:
            _BACKENDS[key] = None
            return None
        try:
            import ctranslate2  # noqa: F401

            backend = CTranslate2Backend(model, beam_size=beam_size)
        except Exception:
            _BACKENDS[key] = None
            return None
        _BACKENDS[key] = backend
        return backend


def clear_local_backends() -> None:
    with _BACKENDS_LOCK:
        _BACKENDS.clear()
