"""Offline translation with our own Transformer model (trained by training/train_nmt.py).

The model lives in models/nmt-en-es-fr/ (model.pt, spm.model, config.json); override the
base folder with TRANSLATOR_MODEL_DIR. Nothing is downloaded at runtime.

Speed-ups:
  * all sentences of a request are sorted by length and translated in batches
  * duplicate sentences are translated once; an LRU cache returns repeats instantly
  * incremental decoding with a key/value cache (translator/nmt.py)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

import sentencepiece as spm
import torch

from .nmt import NMTConfig, TransformerNMT

logger = logging.getLogger(__name__)

MODEL_FOLDER = "nmt-en-es-fr"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"

# The model is trained on single sentences; split paragraphs before translating.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+")


def model_dir(override: str | os.PathLike | None = None) -> Path:
    return Path(override or os.getenv("TRANSLATOR_MODEL_DIR") or DEFAULT_MODEL_DIR)


def local_model_path(base: str | os.PathLike | None = None) -> Path:
    return model_dir(base) / MODEL_FOLDER


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


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
    def __init__(
        self,
        models_path: str | os.PathLike | None = None,
        beam_size: int | None = None,
        batch_size: int | None = None,
        threads: int | None = None,
        cache_size: int | None = None,
    ):
        self.path = local_model_path(models_path)
        self.device = "cpu"
        self.beam_size = beam_size or _env_int("TRANSLATOR_BEAM_SIZE", 4)
        self.batch_size = batch_size or _env_int("TRANSLATOR_BATCH_SIZE", 32)
        self.threads = threads or _env_int("TRANSLATOR_THREADS", max(1, (os.cpu_count() or 4) // 2))
        self._cache = _LRUCache(
            cache_size if cache_size is not None else _env_int("TRANSLATOR_CACHE_SIZE", 20000)
        )
        self._model: TransformerNMT | None = None
        self._sp: spm.SentencePieceProcessor | None = None
        self._lock = threading.Lock()
        self.info: dict = {}
        self.languages: list[str] = self._read_languages()

    def _read_languages(self) -> list[str]:
        try:
            with open(self.path / "config.json", encoding="utf-8") as f:
                return json.load(f)["languages"]
        except (OSError, KeyError, ValueError):
            return ["en", "es", "fr"]

    @property
    def installed(self) -> bool:
        return all((self.path / f).is_file() for f in ("model.pt", "spm.model", "config.json"))

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if not self.installed:
                raise ModelNotFound(
                    f"Translation model not found at {self.path}. "
                    "Train it with the scripts in training/ (see README)."
                )
            torch.set_num_threads(self.threads)
            with open(self.path / "config.json", encoding="utf-8") as f:
                cfg = json.load(f)
            self.info = cfg.get("training", {})
            self.languages = cfg["languages"]
            model = TransformerNMT(NMTConfig(**cfg["model"]))
            state = torch.load(self.path / "model.pt", map_location="cpu", weights_only=True)
            model.load_state_dict({k: v.float() for k, v in state.items()})
            model.eval()
            self._sp = spm.SentencePieceProcessor(model_file=str(self.path / "spm.model"))
            self._tags = {l: self._sp.piece_to_id(f"<2{l}>") for l in self.languages}
            self._model = model
            logger.info("Loaded %s (%s, beam %d, %d threads)",
                        self.path, self.info, self.beam_size, self.threads)

    def _translate_sentences(self, pairs: list[tuple[str, str]], target: str) -> dict:
        """pairs: unique (source_lang, sentence). Returns {(source, sentence): translation}."""
        c = self._model.config
        tag = self._tags[target]
        encoded = self._sp.encode([s for _, s in pairs])
        rows = [[tag] + ids[: c.max_positions // 2 - 2] + [c.eos_id] for ids in encoded]
        order = sorted(range(len(rows)), key=lambda i: len(rows[i]))  # less padding
        banned = list(self._tags.values())
        out: dict = {}
        with self._lock:
            for i in range(0, len(order), self.batch_size):
                chunk = order[i : i + self.batch_size]
                width = max(len(rows[j]) for j in chunk)
                src = torch.tensor([rows[j] + [c.pad_id] * (width - len(rows[j])) for j in chunk])
                hyps = self._model.generate(src, beam=self.beam_size, banned_ids=banned)
                for j, ids in zip(chunk, hyps):
                    out[pairs[j]] = self._sp.decode(ids)
        return out

    def translate_many(self, texts: list[str], sources: list[str], target: str) -> list[str]:
        """Translate texts[i] from sources[i] into target in one batched pass.

        Returned unchanged: text already in the target language, undetermined text ("und"),
        and text in a language this model cannot translate.
        """
        if target not in self.languages:
            raise UnsupportedLanguage(f"Unsupported target language '{target}'")

        plans: list[tuple[str, list[list[str]]] | None] = []
        todo: dict[tuple[str, str], None] = {}
        done: dict[tuple[str, str], str] = {}
        for text, src in zip(texts, sources):
            if src == target or src not in self.languages or not text.strip():
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
        return self.translate_many([text], [source], target)[0]
