"""End-to-end API tests (fully offline once ./models is populated)."""

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


def post(**body):
    return client.post("/translate", json=body)


def test_health():
    body = client.get("/health").json()
    assert body["status"] == "ok"


def test_languages():
    assert len(client.get("/languages").json()["languages"]) == 24


@pytest.mark.parametrize("lang", list(SAMPLES))
def test_detect_and_translate_to_english(lang):
    target = "fr" if lang == "en" else "en"
    r = post(text=SAMPLES[lang], target_lang=target)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected_lang"] == lang
    out = body["translation"].lower()
    if lang == "ga":
        # NLLB-600M renders the Irish word for battery ("ceallra") as "cells".
        assert "car" in out
    else:
        assert ("batter" in out) if target == "en" else ("voiture" in out)


@pytest.mark.parametrize("target", ["de", "it", "pl", "el", "mt", "ga", "sv", "ro"])
def test_english_to_many(target):
    r = post(text="The car needs a new battery.", target_lang=target)
    body = r.json()
    assert r.status_code == 200
    assert body["detected_lang"] == "en"
    assert body["translation"] and body["translation"] != body["input"]


def test_same_language_returns_input_unchanged():
    text = "Das Auto braucht eine neue Batterie."
    body = post(text=text, target_lang="de").json()
    assert body["detected_lang"] == "de"
    assert body["translation"] == text


def test_mixed_language_batch():
    texts = [SAMPLES["fr"], SAMPLES["es"], SAMPLES["en"]]
    body = post(text=texts, target_lang="en").json()
    assert body["detected_lang"] == ["fr", "es", "en"]
    assert body["translation"][2] == SAMPLES["en"]
    assert all("battery" in t.lower() for t in body["translation"])


def test_source_lang_override():
    body = post(text="Hello", source_lang="en", target_lang="es").json()
    assert body["detected_lang"] == "en"
    assert body["confidence"] == 1.0
    assert "hola" in body["translation"].lower()


def test_multi_sentence_and_lines_preserved():
    text = "The engine is overheating. Please check the coolant.\nCall me tomorrow."
    out = post(text=text, target_lang="de").json()["translation"]
    assert out.count("\n") == 1
    assert "Motor" in out


def test_no_letters_passthrough():
    body = post(text="12345 !!", target_lang="fr").json()
    assert body["detected_lang"] == "und"
    assert body["translation"] == "12345 !!"


def test_invalid_target():
    assert post(text="Hi", target_lang="zh").status_code == 422


def test_empty_text():
    assert post(text="  ", target_lang="es").status_code == 422
