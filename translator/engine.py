"""Offline many-to-many translation between the 24 EU languages using NLLB-200.

One multilingual model (facebook/nllb-200-distilled-600M) handles every direction.
It is loaded from a local folder (default ./models, override with TRANSLATOR_MODEL_DIR);
no network access is ever attempted. Run `python download_models.py` once to fetch it.

NOTE: NLLB-200 is licensed CC-BY-NC 4.0 (non-commercial use only).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

# Force Hugging Face libraries into offline mode before they are imported.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import torch  # noqa: E402
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # noqa: E402

from .languages import LANGUAGES  # noqa: E402

logger = logging.getLogger(__name__)

MODEL_NAME = "facebook/nllb-200-distilled-600M"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"

# NLLB is trained on single sentences; long paragraphs get truncated or summarised.
# Split on sentence-ending punctuation followed by whitespace, keep line breaks.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:。])\s+")


def model_dir(override: str | os.PathLike | None = None) -> Path:
    return Path(override or os.getenv("TRANSLATOR_MODEL_DIR") or DEFAULT_MODEL_DIR)


def local_model_path(name: str = MODEL_NAME, base: str | os.PathLike | None = None) -> Path:
    """Folder for a model, e.g. models/nllb-200-distilled-600M."""
    return model_dir(base) / name.split("/")[-1]


class UnsupportedLanguage(ValueError):
    pass


class ModelNotFound(RuntimeError):
    pass


def _split(text: str) -> list[list[str]]:
    """Text -> lines -> sentences. Empty lines are kept as empty lists."""
    return [[s for s in _SENTENCE_SPLIT.split(line.strip()) if s] for line in text.split("\n")]


class Translator:
    def __init__(
        self,
        device: str | None = None,
        max_length: int = 512,
        num_beams: int = 4,
        batch_size: int = 16,
        models_path: str | os.PathLike | None = None,
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.max_length = max_length
        self.num_beams = num_beams
        self.batch_size = batch_size
        self.path = local_model_path(base=models_path)
        self._tokenizer = None
        self._model = None
        self._lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return (self.path / "config.json").is_file()

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
                    "Run 'python download_models.py' once on a machine with internet."
                )
            logger.info("Loading %s on %s", self.path, self.device)
            self._tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
            # Weights may be stored as fp16 to save disk; run fp32 on CPU (fp16 is slow there).
            dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
            model = AutoModelForSeq2SeqLM.from_pretrained(
                self.path, local_files_only=True, dtype=dtype
            )
            self._model = model.to(self.device).eval()

    def _generate(self, sentences: list[str], source: str, target: str) -> list[str]:
        tok, model = self._tokenizer, self._model
        tok.src_lang = LANGUAGES[source][1]
        forced_bos = tok.convert_tokens_to_ids(LANGUAGES[target][1])
        out: list[str] = []
        for i in range(0, len(sentences), self.batch_size):
            chunk = sentences[i : i + self.batch_size]
            batch = tok(
                chunk, return_tensors="pt", padding=True, truncation=True,
                max_length=self.max_length,
            ).to(self.device)
            generated = model.generate(
                **batch,
                forced_bos_token_id=forced_bos,
                max_length=self.max_length,
                num_beams=self.num_beams,
            )
            out.extend(tok.batch_decode(generated, skip_special_tokens=True))
        return out

    def translate(self, text: str, source: str, target: str) -> str:
        for code in (source, target):
            if code not in LANGUAGES:
                raise UnsupportedLanguage(f"Unsupported language '{code}'")
        if source == target:
            return text
        self.load()

        lines = _split(text)
        sentences = [s for line in lines for s in line]
        if not sentences:
            return text
        with self._lock, torch.inference_mode():
            translated = iter(self._generate(sentences, source, target))
        return "\n".join(" ".join(next(translated) for _ in line) for line in lines)
