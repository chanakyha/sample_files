"""Offline language detection for the 24 EU languages with our own model (translator/lid.py),
trained by training/train_lid.py and stored in models/lid/."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import torch

from .engine import model_dir
from .lid import LIDModel, features

UNDETERMINED = "und"
LID_FOLDER = "lid"


@dataclass(frozen=True)
class Detection:
    lang: str  # ISO 639-1 code, or "und" when the text contains no letters
    confidence: float  # model probability of the chosen language (0-1)


class LanguageDetector:
    def __init__(self, models_path=None) -> None:
        self.path = model_dir(models_path) / LID_FOLDER
        self._model: LIDModel | None = None
        self._lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return (self.path / "lid.pt").is_file() and (self.path / "config.json").is_file()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if not self.installed:
                from .engine import ModelNotFound

                raise ModelNotFound(
                    f"Language-ID model not found at {self.path}. Run training/train_lid.py."
                )
            with open(self.path / "config.json", encoding="utf-8") as f:
                cfg = json.load(f)
            self.languages: list[str] = cfg["languages"]
            self._buckets = cfg["buckets"]
            self._max_n = cfg.get("max_n", 4)
            self._min_n = cfg.get("min_n", 1)
            self._word_weight = cfg.get("word_weight", 1)
            # Log-prior for the languages the translator supports (calibrated in train_lid.py)
            self._bias = torch.zeros(len(self.languages))
            for lang, boost in cfg.get("prior", {}).items():
                self._bias[self.languages.index(lang)] = boost
            model = LIDModel(cfg["buckets"], cfg["dim"], len(self.languages))
            state = torch.load(self.path / "lid.pt", map_location="cpu", weights_only=True)
            model.load_state_dict({k: v.float() for k, v in state.items()})
            self._model = model.eval()

    def detect(self, text: str) -> Detection:
        return self.detect_many([text])[0]

    @torch.inference_mode()
    def detect_many(self, texts: list[str]) -> list[Detection]:
        self.load()
        results: list[Detection] = [Detection(UNDETERMINED, 0.0)] * len(texts)
        flat: list[int] = []
        offsets: list[int] = []
        rows: list[int] = []
        for i, text in enumerate(texts):
            ids = features(text, self._buckets, self._max_n, self._min_n, self._word_weight)
            if not ids:  # no letters at all
                continue
            rows.append(i)
            offsets.append(len(flat))
            flat.extend(ids)
        if rows:
            logits = self._model(torch.tensor(flat), torch.tensor(offsets)) + self._bias
            probs = torch.softmax(logits, dim=-1)
            conf, best = probs.max(dim=-1)
            for i, c, b in zip(rows, conf.tolist(), best.tolist()):
                results[i] = Detection(self.languages[b], round(c, 4))
        return results
