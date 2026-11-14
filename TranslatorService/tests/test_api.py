"""End-to-end API tests with our own trained models (fully offline).

Requires models/nmt-en-es-fr and models/lid (see README: training).
"""

import time

import pytest
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

SAMPLES = {
    "bg": "Колата има нужда от нова батерия.",
    "cs": "Auto potřebuje novou baterii.",
    "da": "Bilen har brug for et nyt batteri.",
    "de": "Das Auto braucht eine neue Batterie.",
    "el": "Το αυτοκίνητο χρειάζεται νέα μπαταρία.",
    "en": "The car needs a new battery.",
    "es": "El coche necesita una batería nueva.",
    "et": "Auto vajab uut akut.",
    "fi": "Auto tarvitsee uuden akun.",
    "fr": "La voiture a besoin d'une nouvelle batterie.",
    "ga": "Teastaíonn ceallra nua ón gcarr.",
    "hr": "Automobilu treba novi akumulator.",
    "hu": "Az autónak új akkumulátorra van szüksége.",
    "it": "La macchina ha bisogno di una batteria nuova.",
    "lt": "Automobiliui reikia naujo akumuliatoriaus.",
    "lv": "Automašīnai vajag jaunu akumulatoru.",
    "mt": "Il-karozza għandha bżonn batterija ġdida.",
    "nl": "De auto heeft een nieuwe accu nodig.",
    "pl": "Samochód potrzebuje nowego akumulatora.",
    "pt": "O carro precisa de uma bateria nova.",
    "ro": "Mașina are nevoie de o baterie nouă.",
    "sk": "Auto potrebuje novú batériu.",
    "sl": "Avto potrebuje nov akumulator.",
    "sv": "Bilen behöver ett nytt batteri.",
}
TRANSLATABLE = {"en", "es", "fr"}


def post(**body):
    return client.post("/translate", json=body)


def test_health():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["translation_languages"] == ["en", "es", "fr"]


def test_languages():
    body = client.get("/languages").json()
    assert set(body["languages"]) == TRANSLATABLE
    assert len(body["detectable"]) == 24


def test_detects_all_24_and_translates_only_supported():
    langs = list(SAMPLES)
    groups = [[SAMPLES[c] for c in langs[i : i + 6]] for i in range(0, 24, 6)]
    body = post(text=groups, target_lang="en").json()
    flat_langs = [c for g in body["detected_lang"] for c in g]
    flat_out = [t for g in body["translation"] for t in g]
    flat_done = [d for g in body["translated"] for d in g]
    assert flat_langs == langs
    for code, out, done in zip(langs, flat_out, flat_done):
        if code in ("es", "fr"):
            assert done and "car" in out.lower(), (code, out)
        else:  # English (already target) or a language the model cannot translate
            assert not done and out == SAMPLES[code]


@pytest.mark.parametrize(
    "src,tgt,text,expected",
    [
        ("en", "fr", "The car needs a new battery.", "voiture"),
        ("en", "es", "The car needs a new battery.", "coche"),
        ("fr", "en", "Je voudrais un café, s'il vous plaît.", "coffee"),
        ("es", "en", "¿Dónde está la estación de tren?", "station"),
        ("es", "fr", "Muchas gracias por tu ayuda.", "merci"),
        ("fr", "es", "Je suis très content de te voir.", "contento"),
    ],
)
def test_all_six_directions(src, tgt, text, expected):
    body = post(text=[[text]], target_lang=tgt).json()
    assert body["detected_lang"] == [[src]]
    assert body["translated"] == [[True]]
    assert expected in body["translation"][0][0].lower(), body["translation"]


def test_shape_preserved_including_empty_groups_and_strings():
    body = post(text=[["Good morning.", ""], [], ["Thank you very much."]], target_lang="fr").json()
    assert [len(g) for g in body["translation"]] == [2, 0, 1]
    assert body["translation"][0][1] == ""
    assert body["translated"][0][1] is False


def test_same_language_returns_input_unchanged():
    text = "La voiture a besoin d'une nouvelle batterie."
    body = post(text=[[text]], target_lang="fr").json()
    assert body["detected_lang"] == [["fr"]]
    assert body["translation"] == [[text]]


def test_source_lang_override():
    body = post(text=[["Hello"]], source_lang="en", target_lang="es").json()
    assert body["detected_lang"] == [["en"]]
    assert body["confidence"] == [[1.0]]
    assert body["translated"] == [[True]]


def test_multi_sentence_string_and_lines_preserved():
    text = "The engine is very hot. Please call me.\nSee you tomorrow."
    out = post(text=[[text]], target_lang="fr").json()["translation"][0][0]
    assert out.count("\n") == 1


def test_duplicates_and_cache_are_fast():
    sentence = "The weather is very nice today."
    groups = [[sentence] * 30]
    start = time.perf_counter()
    first = post(text=groups, target_lang="es").json()["translation"][0]
    t1 = time.perf_counter() - start
    assert len(set(first)) == 1
    start = time.perf_counter()
    second = post(text=groups, target_lang="es").json()["translation"][0]
    assert second == first
    assert time.perf_counter() - start < max(t1, 0.5)


@pytest.mark.parametrize(
    "lang,text",
    [
        ("en", "hi my name is chanakyha"),
        ("en", "Hi How are you"),
        ("en", "hello, I am Chanakyha from Chennai"),
        ("en", "Chanakyha will join the TCS team on Monday"),
        ("es", "Me llamo Chanakyha y vivo en Chennai."),
        ("fr", "Je m'appelle Chanakyha et j'habite à Chennai."),
        ("de", "Ich heiße Chanakyha und wohne in Chennai."),
        ("mt", "Jisimni Chanakyha u ngħix f'Chennai."),
        ("it", "Va bene."),
        ("es", "Está listo."),
    ],
)
def test_detection_short_casual_and_names(lang, text):
    body = post(text=[[text]], target_lang="fr").json()
    assert body["detected_lang"] == [[lang]], text


def test_no_letters_passthrough():
    body = post(text=[["12345 !!"]], target_lang="fr").json()
    assert body["detected_lang"] == [["und"]]
    assert body["translation"] == [["12345 !!"]]


@pytest.mark.parametrize("bad", ["Hello", ["Hello"], [[]], []])
def test_rejects_non_nested_or_empty_input(bad):
    assert post(text=bad, target_lang="fr").status_code == 422


@pytest.mark.parametrize("target", ["de", "zh"])
def test_rejects_unsupported_target(target):
    assert post(text=[["Hi"]], target_lang=target).status_code == 422


def test_request_id_and_log_entry():
    """Each response carries X-Request-ID / X-Instance, and the request is logged as JSON
    (sentence count and languages, never the text itself)."""
    import json
    import logging

    from logsetup import log_dir

    r = client.post("/translate", json={"text": [["Muchas gracias."]], "target_lang": "en"},
                    headers={"X-Request-ID": "test-req-42"})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-req-42"
    assert r.headers["x-instance"]
    for h in logging.getLogger().handlers:
        h.flush()
    entries = [json.loads(l) for l in open(log_dir() / f"{r.headers['x-instance']}.log", encoding="utf-8")]
    mine = [e for e in entries if e.get("request_id") == "test-req-42"]
    assert {e["logger"] for e in mine} == {"translator.api", "translator.access"}
    detail = next(e for e in mine if e["logger"] == "translator.api")
    assert detail["sentences"] == 1 and detail["detected"] == {"es": 1} and detail["target"] == "en"
    assert "Muchas" not in json.dumps(mine)
