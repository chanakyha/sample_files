"""REST API for the offline translator (24 EU languages, automatic source detection).

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from enum import Enum
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from translator import (
    LANGUAGES,
    MODEL_NAME,
    UNDETERMINED,
    LanguageDetector,
    ModelNotFound,
    Translator,
)

logging.basicConfig(level=logging.INFO)

LangCode = Enum("LangCode", {c: c for c in LANGUAGES}, type=str)
MAX_SENTENCES = int(os.getenv("MAX_SENTENCES", "10000"))

translator = Translator()
detector = LanguageDetector()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the model at startup so the first request is fast (PRELOAD_MODELS=0 to skip).
    if os.getenv("PRELOAD_MODELS", "1") == "1" and translator.installed:
        await run_in_threadpool(_warm_up)
    yield


def _warm_up() -> None:
    """Load the model and run one tiny translation so the first real request is fast."""
    translator.load()
    detector.detect_many(["Warm-up sentence."])
    translator.translate_many(["Warm-up sentence."], ["en"], "fr")


app = FastAPI(title="Offline Translator API", version="3.0.0", lifespan=lifespan)


class TranslateRequest(BaseModel):
    text: list[list[str]] = Field(
        ..., min_length=1, description="Array of arrays of sentences to translate"
    )
    target_lang: LangCode = Field(..., description="Target language (ISO 639-1 code)")
    source_lang: LangCode | None = Field(
        None, description="Optional. Skip auto-detection and treat all text as this language"
    )
    detect_mode: Literal["sentence", "group"] = Field(
        "sentence",
        description="'sentence': detect each sentence separately (supports mixed languages). "
        "'group': detect once per inner array from all its sentences together; more accurate "
        "for short sentences when each inner array is a single-language document.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "text": [
                        ["The car needs a new battery.", "Please check the brakes."],
                        ["La voiture est prête.", "Merci beaucoup."],
                    ],
                    "target_lang": "de",
                }
            ]
        }
    }


class TranslateResponse(BaseModel):
    target_lang: str
    translation: list[list[str]] = Field(..., description="Same shape as the input `text`")
    detected_lang: list[list[str]] = Field(..., description="Source language of each sentence")
    confidence: list[list[float]] = Field(..., description="Detection confidence (0-1)")


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _run(groups: list[list[str]], target: str, source: str | None, mode: str):
    flat = [s for group in groups for s in group]
    if source:
        langs, confs = [source] * len(flat), [1.0] * len(flat)
    elif mode == "group":
        group_det = detector.detect_many([" ".join(group) for group in groups])
        langs, confs = [], []
        for group, det in zip(groups, group_det):
            for s in group:
                ok = _has_letters(s)
                langs.append(det.lang if ok else UNDETERMINED)
                confs.append(det.confidence if ok else 0.0)
    else:
        detections = detector.detect_many(flat)
        langs = [d.lang for d in detections]
        confs = [d.confidence for d in detections]
    translated = translator.translate_many(flat, langs, target)

    def reshape(values):
        it = iter(values)
        return [[next(it) for _ in group] for group in groups]

    return reshape(translated), reshape(langs), reshape(confs)


@app.get("/health")
def health():
    return {
        "status": "ok" if translator.installed else "model_missing",
        "model": MODEL_NAME,
        "model_path": str(translator.path),
        "model_loaded": translator.loaded,
        "device": translator.device,
        "compute_type": translator.compute_type,
        "workers": translator.inter_threads,
        "threads_per_worker": translator.intra_threads,
        "beam_size": translator.beam_size,
    }


@app.get("/languages")
def languages():
    return {"languages": {code: name for code, (name, _) in LANGUAGES.items()}}


@app.post("/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest):
    total = sum(len(group) for group in req.text)
    if total == 0:
        raise HTTPException(status_code=422, detail="No sentences to translate")
    if total > MAX_SENTENCES:
        raise HTTPException(
            status_code=413, detail=f"Too many sentences ({total}); limit is {MAX_SENTENCES}"
        )

    target = req.target_lang.value
    source = req.source_lang.value if req.source_lang else None
    try:
        translation, langs, confs = await run_in_threadpool(
            _run, req.text, target, source, req.detect_mode
        )
    except ModelNotFound as e:
        raise HTTPException(status_code=503, detail=str(e))

    return TranslateResponse(
        target_lang=target, translation=translation, detected_lang=langs, confidence=confs
    )
