"""REST API for the offline translator: our own Transformer translation model (en/es/fr)
and our own language detector (24 EU languages). Both are trained with the scripts in
../TranslatorTraining/ and loaded from models/; nothing is downloaded at runtime.

Run one instance:   uvicorn app:app --host 127.0.0.1 --port 8000   (or start_api.bat)
Run several behind the load balancer:  python cluster.py --instances 3   (or start_cluster.bat)
Docs: http://localhost:8000/docs

Logs: logs/<instance>.log, one JSON object per line (see logsetup.py). Every request is
logged with its request id (X-Request-ID, set by the load balancer or generated here),
status, duration, and for /translate the sentence count, target and detected languages.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from enum import Enum
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from translator import (
    LANGUAGES,
    UNDETERMINED,
    LanguageDetector,
    ModelNotFound,
    Translator,
)
from logsetup import setup_logging

# Name of this instance in the logs (the cluster launcher sets api-<port>)
INSTANCE = os.getenv("TRANSLATOR_INSTANCE", "api")
setup_logging(INSTANCE)
logger = logging.getLogger("translator.api")
access_logger = logging.getLogger("translator.access")

MAX_SENTENCES = int(os.getenv("MAX_SENTENCES", "10000"))

translator = Translator()
detector = LanguageDetector()

# Languages our translation model was trained on (from its config.json)
LangCode = Enum("LangCode", {c: c for c in translator.languages}, type=str)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the models at startup so the first request is fast (PRELOAD_MODELS=0 to skip).
    logger.info("instance %s starting (pid %d)", INSTANCE, os.getpid())
    if os.getenv("PRELOAD_MODELS", "1") == "1" and translator.installed and detector.installed:
        start = time.perf_counter()
        await run_in_threadpool(_warm_up)
        logger.info("models loaded in %.1fs", time.perf_counter() - start,
                    extra={"fields": {"model": translator.info, "threads": translator.threads}})
    elif not (translator.installed and detector.installed):
        logger.warning("models missing in %s", translator.path.parent)
    yield
    logger.info("instance %s stopping", INSTANCE)


def _warm_up() -> None:
    """Load both models and run one tiny translation so the first real request is fast."""
    detector.detect_many(["Warm-up sentence."])
    langs = translator.languages
    translator.translate_many(["Warm-up sentence."], [langs[0]], langs[-1])


app = FastAPI(title="Offline Translator API", version="4.1.0", lifespan=lifespan)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """One log line per request; health checks (every few seconds from the load balancer)
    are logged at DEBUG level only."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    start = time.perf_counter()
    fields = {"request_id": request_id, "method": request.method, "path": request.url.path,
              "client": request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)}
    try:
        response = await call_next(request)
    except Exception:
        fields["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        access_logger.exception("%s %s failed", request.method, request.url.path, extra={"fields": fields})
        raise
    fields["status"] = response.status_code
    fields["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
    level = logging.DEBUG if request.url.path == "/health" else (
        logging.WARNING if response.status_code >= 400 else logging.INFO)
    access_logger.log(level, "%s %s %d %.0fms", request.method, request.url.path,
                      response.status_code, fields["duration_ms"], extra={"fields": fields})
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Instance"] = INSTANCE
    return response


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
                        ["La voiture est prête.", "Muchas gracias."],
                    ],
                    "target_lang": "fr",
                }
            ]
        }
    }


class TranslateResponse(BaseModel):
    target_lang: str
    translation: list[list[str]] = Field(..., description="Same shape as the input `text`")
    detected_lang: list[list[str]] = Field(..., description="Source language of each sentence")
    confidence: list[list[float]] = Field(..., description="Detection confidence (0-1)")
    translated: list[list[bool]] = Field(
        ..., description="False when the text was returned unchanged: already in the target "
        "language, no letters, or a detected language the model cannot translate"
    )


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
    out = translator.translate_many(flat, langs, target)
    supported = set(translator.languages)
    done = [l in supported and l != target and bool(s.strip()) for s, l in zip(flat, langs)]

    def reshape(values):
        it = iter(values)
        return [[next(it) for _ in group] for group in groups]

    return reshape(out), reshape(langs), reshape(confs), reshape(done)


@app.get("/health")
def health():
    ready = translator.installed and detector.installed
    return {
        "status": "ok" if ready else "model_missing",
        "instance": INSTANCE,
        "model": "own Transformer (TranslatorTraining/train_nmt.py)",
        "model_path": str(translator.path),
        "model_loaded": translator.loaded,
        "training": translator.info,
        "device": translator.device,
        "workers": translator.threads,
        "beam_size": translator.beam_size,
        "translation_languages": translator.languages,
    }


@app.get("/languages")
def languages():
    return {
        "languages": {c: LANGUAGES[c] for c in translator.languages},
        "detectable": dict(LANGUAGES),
    }


@app.post("/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest, request: Request):
    total = sum(len(group) for group in req.text)
    if total == 0:
        raise HTTPException(status_code=422, detail="No sentences to translate")
    if total > MAX_SENTENCES:
        raise HTTPException(
            status_code=413, detail=f"Too many sentences ({total}); limit is {MAX_SENTENCES}"
        )

    target = req.target_lang.value
    source = req.source_lang.value if req.source_lang else None
    start = time.perf_counter()
    try:
        translation, langs, confs, done = await run_in_threadpool(
            _run, req.text, target, source, req.detect_mode
        )
    except ModelNotFound as e:
        logger.error("model missing: %s", e, extra={"fields": {"request_id": request.state.request_id}})
        raise HTTPException(status_code=503, detail=str(e))
    logger.info(
        "translated %d sentence(s) to %s", total, target,
        extra={"fields": {
            "request_id": request.state.request_id,
            "sentences": total,
            "groups": len(req.text),
            "characters": sum(len(s) for g in req.text for s in g),
            "target": target,
            "source": source,
            "detect_mode": req.detect_mode,
            "detected": dict(Counter(l for g in langs for l in g)),
            "translated": sum(d for g in done for d in g),
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        }},
    )

    return TranslateResponse(
        target_lang=target, translation=translation, detected_lang=langs,
        confidence=confs, translated=done,
    )


class DetectRequest(BaseModel):
    text: list[str] = Field(..., min_length=1, description="Texts whose language to detect")


class DetectResponse(BaseModel):
    detected_lang: list[str] = Field(..., description="ISO 639-1 code per text ('und' = no letters)")
    language: list[str] = Field(..., description="English name of the detected language")
    confidence: list[float] = Field(..., description="Detection confidence (0-1)")
    translatable: list[bool] = Field(..., description="True when the model can translate from it")


@app.post("/detect", response_model=DetectResponse)
async def detect(req: DetectRequest, request: Request):
    """Language detection only (no translation), e.g. to show the language of a text as soon
    as it is entered."""
    if len(req.text) > MAX_SENTENCES:
        raise HTTPException(
            status_code=413, detail=f"Too many texts ({len(req.text)}); limit is {MAX_SENTENCES}"
        )
    try:
        detections = await run_in_threadpool(detector.detect_many, req.text)
    except ModelNotFound as e:
        raise HTTPException(status_code=503, detail=str(e))
    langs = [d.lang if _has_letters(t) else UNDETERMINED for t, d in zip(req.text, detections)]
    logger.info("detected %d text(s)", len(req.text), extra={"fields": {
        "request_id": request.state.request_id, "texts": len(req.text),
        "detected": dict(Counter(langs))}})
    supported = set(translator.languages)
    return DetectResponse(
        detected_lang=langs,
        language=[LANGUAGES.get(l, "unknown") for l in langs],
        confidence=[d.confidence if l != UNDETERMINED else 0.0 for l, d in zip(langs, detections)],
        translatable=[l in supported for l in langs],
    )
