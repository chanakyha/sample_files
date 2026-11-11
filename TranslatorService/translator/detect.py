"""Offline language detection restricted to the 24 EU languages.

Three independent detectors each give a probability for every language; the weighted
average decides. Combining them fixes most single-detector mistakes, e.g. a foreign name
("hi my name is chanakyha") pulling one detector towards Czech while the others say English.

  * lingua    (weight 1.0) - strong on short text; no Maltese
  * fastText  (weight 1.0) - Facebook lid.176 model, robust on casual text and names
  * langid    (weight 0.5) - covers all 24 languages including Maltese

lingua and langid ship their models inside their pip packages. The fastText model lives in
models/fasttext-lid/ (lid.176.bin preferred, lid.176.ftz as a smaller fallback; fetched by
download_models.py). Without it, detection falls back to lingua + langid.

On a 143-sentence test set of short phrases, names and casual text: 96% correct
(lingua + langid alone: 87%).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from langid.langid import LanguageIdentifier, model as langid_model
from lingua import IsoCode639_1, LanguageDetectorBuilder

from .engine import model_dir
from .languages import LANGUAGE_CODES

logger = logging.getLogger(__name__)

UNDETERMINED = "und"
FASTTEXT_DIR = "fasttext-lid"
FASTTEXT_FILES = ("lid.176.bin", "lid.176.ftz")  # in order of preference

_CODES = set(LANGUAGE_CODES)


@dataclass(frozen=True)
class Detection:
    lang: str  # ISO 639-1 code, or "und" when the text contains no letters
    confidence: float  # combined probability of the chosen language (0-1)


def find_fasttext_model(base=None) -> Path | None:
    folder = model_dir(base) / FASTTEXT_DIR
    return next((folder / f for f in FASTTEXT_FILES if (folder / f).is_file()), None)


def _normalise(scores: dict[str, float]) -> dict[str, float]:
    total = sum(scores.values())
    return {k: v / total for k, v in scores.items()} if total > 0 else {}


class LanguageDetector:
    def __init__(self, models_path=None) -> None:
        self._langid = LanguageIdentifier.from_modelstring(langid_model, norm_probs=True)
        self._langid.set_languages(LANGUAGE_CODES)
        self._lingua = (
            LanguageDetectorBuilder.from_iso_codes_639_1(
                *[getattr(IsoCode639_1, c.upper()) for c in LANGUAGE_CODES if c != "mt"]
            )
            .with_preloaded_language_models()
            .build()
        )
        self._fasttext = None
        self.fasttext_path = find_fasttext_model(models_path)
        if self.fasttext_path is not None:
            import fasttext  # fasttext-predict

            self._fasttext = fasttext.load_model(str(self.fasttext_path))
            self.weights = {"lingua": 1.0, "fasttext": 1.0, "langid": 0.5}
        else:
            logger.warning(
                "fastText language-ID model not found in %s; detection is less accurate. "
                "Run download_models.py.", model_dir(models_path) / FASTTEXT_DIR,
            )
            self.weights = {"lingua": 1.0, "langid": 1.0}
        self._lock = threading.Lock()  # langid is not thread-safe

    def detect(self, text: str) -> Detection:
        return self.detect_many([text])[0]

    def detect_many(self, texts: list[str]) -> list[Detection]:
        results: list[Detection | None] = [None] * len(texts)
        pending = [i for i, t in enumerate(texts) if any(ch.isalpha() for ch in t)]
        for i, t in enumerate(texts):
            if i not in pending:
                results[i] = Detection(UNDETERMINED, 0.0)
        if not pending:
            return results  # type: ignore[return-value]

        batch = [" ".join(texts[i].split()) for i in pending]  # collapse newlines/spaces
        # lingua runs the whole batch in parallel on all CPU cores
        lingua_all = self._lingua.compute_language_confidence_values_in_parallel(batch)
        with self._lock:
            langid_all = [self._langid.rank(t) for t in batch]
            fasttext_all = [self._fasttext_scores(t) for t in batch] if self._fasttext else None

        total_weight = sum(self.weights.values())
        for n, i in enumerate(pending):
            dists = {
                "lingua": _normalise(
                    {v.language.iso_code_639_1.name.lower(): v.value for v in lingua_all[n]}
                ),
                "langid": _normalise(dict(langid_all[n])),
            }
            if fasttext_all is not None:
                dists["fasttext"] = fasttext_all[n]
            combined: dict[str, float] = defaultdict(float)
            for name, weight in self.weights.items():
                for lang, p in dists[name].items():
                    combined[lang] += weight * p
            best = max(combined, key=combined.get)
            results[i] = Detection(best, round(combined[best] / total_weight, 4))
        return results  # type: ignore[return-value]

    def _fasttext_scores(self, text: str) -> dict[str, float]:
        labels, probs = self._fasttext.predict(text, k=-1, threshold=0.0)
        scores = {}
        for label, p in zip(labels, probs):
            code = label.removeprefix("__label__")
            if code in _CODES:
                scores[code] = float(p)
        return _normalise(scores)
