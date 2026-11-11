"""Offline language detection restricted to the 24 EU languages.

Uses two detectors, both of which ship their models inside the pip package:
  * lingua  - accurate on short text, covers 23 of the 24 languages (no Maltese)
  * langid  - covers all 24; used here to recognise Maltese
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from langid.langid import LanguageIdentifier, model as langid_model
from lingua import IsoCode639_1, LanguageDetectorBuilder

from .languages import LANGUAGE_CODES

UNDETERMINED = "und"


@dataclass(frozen=True)
class Detection:
    lang: str  # ISO 639-1 code, or "und" when the text contains no letters
    confidence: float


class LanguageDetector:
    def __init__(self) -> None:
        self._langid = LanguageIdentifier.from_modelstring(langid_model, norm_probs=True)
        self._langid.set_languages(LANGUAGE_CODES)
        self._lingua = (
            LanguageDetectorBuilder.from_iso_codes_639_1(
                *[getattr(IsoCode639_1, c.upper()) for c in LANGUAGE_CODES if c != "mt"]
            )
            .with_preloaded_language_models()
            .build()
        )
        self._lock = threading.Lock()  # langid is not thread-safe

    def detect(self, text: str) -> Detection:
        if not any(ch.isalpha() for ch in text):
            return Detection(UNDETERMINED, 0.0)

        with self._lock:
            lid_lang, lid_prob = self._langid.classify(text)
        if lid_lang == "mt":
            return Detection("mt", round(float(lid_prob), 4))

        values = self._lingua.compute_language_confidence_values(text)
        if not values:
            return Detection(lid_lang, round(float(lid_prob), 4))
        top = values[0]
        return Detection(top.language.iso_code_639_1.name.lower(), round(top.value, 4))
