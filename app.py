"""REST API for the offline translator (24 EU languages, automatic source detection).

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from enum import Enum

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

translator = Translator()
detector = LanguageDetector()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Set PRELOAD_MODELS=1 to load the translation model at startup instead of on first request.
    if os.getenv("PRELOAD_MODELS") == "1":
        await run_in_threadpool(translator.load)
    yield


app = FastAPI(title="Offline Translator API", version="2.0.0", lifespan=lifespan)


class TranslateRequest(BaseModel):
    text: str | list[str] = Field(..., description="A string or list of strings to translate")
    target_lang: LangCode = Field(..., description="Target language (ISO 639-1 code)")
    source_lang: LangCode | None = Field(
        None, description="Optional. Skip auto-detection and treat the text as this language"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"text": "Das Auto braucht eine neue Batterie.", "target_lang": "en"}]
        }
    }


class TranslateResponse(BaseModel):
    detected_lang: str | list[str] = Field(..., description="Source language of each input")
    confidence: float | list[float] = Field(..., description="Detection confidence (0-1)")
    target_lang: str
    input: str | list[str]
    translation: str | list[str]


def _translate_one(text: str, target: str, source: str | None) -> tuple[str, float, str]:
    if source:
        lang, conf = source, 1.0
    else:
        d = detector.detect(text)
        lang, conf = d.lang, d.confidence
    if lang == UNDETERMINED or lang == target:
        return lang, conf, text  # no letters, or already in the target language
    return lang, conf, translator.translate(text, lang, target)


def _translate_all(texts: list[str], target: str, source: str | None):
    return [_translate_one(t, target, source) for t in texts]


@app.get("/health")
def health():
    return {
        "status": "ok" if translator.installed else "model_missing",
        "device": translator.device,
        "model": MODEL_NAME,
        "model_path": str(translator.path),
        "model_loaded": translator.loaded,
    }


@app.get("/languages")
def languages():
    return {"languages": {code: name for code, (name, _) in LANGUAGES.items()}}


@app.post("/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest):
    is_single = isinstance(req.text, str)
    texts = [req.text] if is_single else req.text
    if not texts or any(not t.strip() for t in texts):
        raise HTTPException(status_code=422, detail="Text must not be empty")

    target = req.target_lang.value
    source = req.source_lang.value if req.source_lang else None
    try:
        results = await run_in_threadpool(_translate_all, texts, target, source)
    except ModelNotFound as e:
        raise HTTPException(status_code=503, detail=str(e))

    langs, confs, outs = (list(x) for x in zip(*results))
    if is_single:
        return TranslateResponse(
            detected_lang=langs[0], confidence=confs[0], target_lang=target,
            input=req.text, translation=outs[0],
        )
    return TranslateResponse(
        detected_lang=langs, confidence=confs, target_lang=target,
        input=req.text, translation=outs,
    )
