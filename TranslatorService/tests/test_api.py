"""End-to-end API tests (fully offline once ./models is populated)."""

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


def post(**body):
    return client.post("/translate", json=body)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_languages():
    assert len(client.get("/languages").json()["languages"]) == 24


def test_all_24_languages_in_one_request():
    langs = list(SAMPLES)
    # 4 inner arrays of 6 sentences each
    groups = [[SAMPLES[c] for c in langs[i : i + 6]] for i in range(0, 24, 6)]
    r = post(text=groups, target_lang="en")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [len(g) for g in body["translation"]] == [6, 6, 6, 6]
    flat_langs = [c for g in body["detected_lang"] for c in g]
    flat_out = [t for g in body["translation"] for t in g]
    assert flat_langs == langs
    for code, out in zip(langs, flat_out):
        if code == "en":
            assert out == SAMPLES["en"]  # same language -> unchanged
        elif code == "ga":
            assert "car" in out.lower()  # NLLB-600M renders Irish "ceallra" as "cells"
        else:
            assert "batter" in out.lower(), (code, out)


@pytest.mark.parametrize("target", ["de", "it", "pl", "el", "mt", "ga", "sv", "ro"])
def test_english_to_many(target):
    body = post(text=[["The car needs a new battery."]], target_lang=target).json()
    assert body["detected_lang"] == [["en"]]
    out = body["translation"][0][0]
    assert out and out != "The car needs a new battery."


def test_shape_preserved_including_empty_groups_and_strings():
    body = post(text=[["Good morning.", ""], [], ["Thank you."]], target_lang="fr").json()
    assert [len(g) for g in body["translation"]] == [2, 0, 1]
    assert body["translation"][0][1] == ""
    assert "merci" in body["translation"][2][0].lower()


def test_same_language_returns_input_unchanged():
    text = "Das Auto braucht eine neue Batterie."
    body = post(text=[[text]], target_lang="de").json()
    assert body["detected_lang"] == [["de"]]
    assert body["translation"] == [[text]]


def test_source_lang_override():
    body = post(text=[["Hello"]], source_lang="en", target_lang="es").json()
    assert body["detected_lang"] == [["en"]]
    assert body["confidence"] == [[1.0]]
    assert "hola" in body["translation"][0][0].lower()


def test_multi_sentence_string_and_lines_preserved():
    text = "The engine is overheating. Please check the coolant.\nCall me tomorrow."
    out = post(text=[[text]], target_lang="de").json()["translation"][0][0]
    assert out.count("\n") == 1
    assert "Motor" in out


def test_duplicates_and_cache_are_fast():
    sentence = "The windscreen wipers are not working properly today."
    groups = [[sentence] * 50]
    start = time.perf_counter()
    first = post(text=groups, target_lang="nl").json()["translation"][0]
    assert len(set(first)) == 1  # 50 duplicates translated once
    t1 = time.perf_counter() - start
    start = time.perf_counter()
    second = post(text=groups, target_lang="nl").json()["translation"][0]
    t2 = time.perf_counter() - start
    assert second == first
    assert t2 < max(t1, 0.5)  # served from cache


@pytest.mark.parametrize(
    "lang,text",
    [
        ("de", "Das ist gut."),
        ("en", "hi my name is chanakyha"),
        ("en", "Hi How are you"),
        ("en", "hello, I am Chanakyha from Chennai"),
        ("en", "Chanakyha will join the TCS team on Monday"),
        ("en", "call vetri at 5 pm"),
        ("en", "no problem"),
        ("es", "Me llamo Chanakyha y vivo en Chennai."),
        ("nl", "Ik heet Chanakyha en ik woon in Chennai."),
        ("hu", "A nevem Chanakyha, és Chennaiban élek."),
        ("mt", "Jisimni Chanakyha u ngħix f'Chennai."),
        ("it", "Va bene."),
        ("es", "Está listo."),
    ],
)
def test_detection_short_casual_and_names(lang, text):
    body = post(text=[[text]], target_lang="fr").json()
    assert body["detected_lang"] == [[lang]], text


def test_group_detect_mode_for_short_sentences():
    groups = [
        ["Va bene.", "Grazie mille.", "Ci vediamo domani alle nove."],
        ["Está listo.", "El coche está en el taller.", "123"],
    ]
    body = post(text=groups, target_lang="en", detect_mode="group").json()
    assert body["detected_lang"] == [["it", "it", "it"], ["es", "es", "und"]]
    assert body["translation"][1][2] == "123"


def test_no_letters_passthrough():
    body = post(text=[["12345 !!"]], target_lang="fr").json()
    assert body["detected_lang"] == [["und"]]
    assert body["translation"] == [["12345 !!"]]


@pytest.mark.parametrize("bad", ["Hello", ["Hello"], [[]], []])
def test_rejects_non_nested_or_empty_input(bad):
    assert post(text=bad, target_lang="fr").status_code == 422


def test_invalid_target():
    assert post(text=[["Hi"]], target_lang="zh").status_code == 422
