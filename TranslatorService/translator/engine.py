"""Fast offline many-to-many translation between the 24 EU languages.

Model: facebook/nllb-200-distilled-600M converted to CTranslate2 with int8 weights
(~600 MB). CTranslate2 is a C++ inference engine that runs several batches in parallel
on separate CPU threads (inter_threads), each using intra_threads cores, without the
Python GIL. Compared with plain PyTorch on CPU this is roughly 5-13x faster.

Speed-ups applied to every request:
  * all sentences of a request are translated in one batched call, sorted by length
    to minimise padding, and split across parallel worker threads
  * duplicate sentences are translated only once
  * an in-memory LRU cache returns repeated sentences instantly

The model is loaded only from a local folder (default ./models, override with
TRANSLATOR_MODEL_DIR); no network access is attempted. Run `python download_models.py`
once to create it.

NOTE: NLLB-200 is licensed CC-BY-NC 4.0 (non-commercial use only).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

# Force Hugging Face libraries into offline mode before they are imported.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import ctranslate2  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402
from transformers import logging as hf_logging  # noqa: E402

from .languages import LANGUAGES  # noqa: E402

hf_logging.set_verbosity_error()
logger = logging.getLogger(__name__)

MODEL_NAME = "facebook/nllb-200-distilled-600M"
MODEL_FOLDER = "nllb-200-distilled-600M-ct2"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"

# NLLB is trained on single sentences; long paragraphs get truncated or summarised.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+")


def model_dir(override: str | os.PathLike | None = None) -> Path:
    return Path(override or os.getenv("TRANSLATOR_MODEL_DIR") or DEFAULT_MODEL_DIR)


def local_model_path(base: str | os.PathLike | None = None) -> Path:
    return model_dir(base) / MODEL_FOLDER


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


_GPU_FAST_TYPES = ("int8_float16", "int8_bfloat16", "float16")


def _gpu_types() -> set[str]:
    try:
        if ctranslate2.get_cuda_device_count() > 0:
            return set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:  # no CUDA runtime
        pass
    return set()


def _best_gpu_compute_type() -> str:
    types = _gpu_types()
    return next((t for t in _GPU_FAST_TYPES if t in types), "float32")


def _best_device() -> str:
    # Use the GPU only if it supports fast reduced precision; old GPUs that only do
    # float32 (e.g. compute capability < 6.1) are slower than int8 on the CPU.
    return "cuda" if _gpu_types() & set(_GPU_FAST_TYPES) else "cpu"


class UnsupportedLanguage(ValueError):
    pass


class ModelNotFound(RuntimeError):
    pass


def _split(text: str) -> list[list[str]]:
    """Text -> lines -> sentences. Empty lines are kept as empty lists."""
    return [[s for s in _SENTENCE_SPLIT.split(line.strip()) if s] for line in text.split("\n")]


class _LRUCache:
    def __init__(self, size: int):
        self.size = size
        self._data: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key, value) -> None:
        if self.size <= 0:
            return
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)


class Translator:
    """Thread-safe: concurrent requests share the CTranslate2 worker pool."""

    def __init__(
        self,
        models_path: str | os.PathLike | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        inter_threads: int | None = None,
        intra_threads: int | None = None,
        beam_size: int | None = None,
        max_batch_size: int | None = None,
        cache_size: int | None = None,
        max_length: int = 512,
    ):
        cpus = os.cpu_count() or 4
        self.device = device or os.getenv("TRANSLATOR_DEVICE") or _best_device()
        on_gpu = self.device == "cuda"
        self.compute_type = compute_type or os.getenv("TRANSLATOR_COMPUTE_TYPE") or (
            _best_gpu_compute_type() if on_gpu else "int8"
        )
        # Default CPU layout: parallel workers of 2 cores each (fastest in benchmarks).
        self.intra_threads = intra_threads or _env_int("TRANSLATOR_INTRA_THREADS", 2)
        self.inter_threads = inter_threads or _env_int(
            "TRANSLATOR_INTER_THREADS", 1 if on_gpu else max(1, cpus // self.intra_threads)
        )
        self.beam_size = beam_size or _env_int("TRANSLATOR_BEAM_SIZE", 2)
        self.max_batch_size = max_batch_size or _env_int("TRANSLATOR_MAX_BATCH_SIZE", 16)
        self.max_length = max_length
        self.path = local_model_path(models_path)
        self._cache = _LRUCache(
            cache_size if cache_size is not None else _env_int("TRANSLATOR_CACHE_SIZE", 20000)
        )
        self._model: ctranslate2.Translator | None = None
        self._tokenizer = None
        self._load_lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return (self.path / "model.bin").is_file()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if not self.installed:
                raise ModelNotFound(
                    f"Translation model not found at {self.path}. "
                    "Run 'python download_models.py' once on a machine with internet."
                )
            logger.info(
                "Loading %s on %s (%s, %d workers x %d threads)",
                self.path, self.device, self.compute_type, self.inter_threads, self.intra_threads,
            )
            self._tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
            self._model = ctranslate2.Translator(
                str(self.path),
                device=self.device,
                compute_type=self.compute_type,
                inter_threads=self.inter_threads,
                intra_threads=self.intra_threads,
            )

    def _encode(self, sentence: str, source: str) -> list[str]:
        # NLLB source format: [src_lang] tokens </s>  (built manually so the shared
        # tokenizer is never mutated and stays safe across threads)
        tokens = self._tokenizer.tokenize(sentence)[: self.max_length - 2]
        return [LANGUAGES[source][1], *tokens, "</s>"]

    def _translate_sentences(self, pairs: list[tuple[str, str]], target: str) -> dict:
        """pairs: unique (source_lang, sentence). Returns {(source, sentence): translation}."""
        tgt_code = LANGUAGES[target][1]
        results = self._model.translate_batch(
            [self._encode(sentence, src) for src, sentence in pairs],
            target_prefix=[[tgt_code]] * len(pairs),
            beam_size=self.beam_size,
            max_batch_size=self.max_batch_size,
            max_input_length=self.max_length,
            max_decoding_length=self.max_length,
        )
        tok = self._tokenizer
        out = {}
        for pair, res in zip(pairs, results):
            hyp = res.hypotheses[0]
            if hyp and hyp[0] == tgt_code:
                hyp = hyp[1:]
            out[pair] = tok.decode(tok.convert_tokens_to_ids(hyp), skip_special_tokens=True)
        return out

    def translate_many(self, texts: list[str], sources: list[str], target: str) -> list[str]:
        """Translate texts[i] from sources[i] into target, all in one batched pass.

        A text whose source equals the target (or is "und") is returned unchanged.
        """
        if target not in LANGUAGES:
            raise UnsupportedLanguage(f"Unsupported language '{target}'")

        plans: list[tuple[str, list[list[str]]] | None] = []
        todo: dict[tuple[str, str], None] = {}  # ordered set of uncached pairs
        done: dict[tuple[str, str], str] = {}
        for text, src in zip(texts, sources):
            if src == target or src not in LANGUAGES or not text.strip():
                plans.append(None)
                continue
            lines = _split(text)
            plans.append((src, lines))
            for line in lines:
                for sentence in line:
                    key = (src, sentence)
                    if key in done or key in todo:
                        continue
                    cached = self._cache.get((src, target, sentence))
                    if cached is not None:
                        done[key] = cached
                    else:
                        todo[key] = None

        if todo:
            self.load()
            fresh = self._translate_sentences(list(todo), target)
            for (src, sentence), translation in fresh.items():
                self._cache.put((src, target, sentence), translation)
            done.update(fresh)

        out = []
        for text, plan in zip(texts, plans):
            if plan is None:
                out.append(text)
                continue
            src, lines = plan
            out.append("\n".join(" ".join(done[(src, s)] for s in line) for line in lines))
        return out

    def translate(self, text: str, source: str, target: str) -> str:
        if source not in LANGUAGES:
            raise UnsupportedLanguage(f"Unsupported language '{source}'")
        return self.translate_many([text], [source], target)[0]
