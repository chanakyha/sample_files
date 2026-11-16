"""Step 1 (needs access to github.com): download the training datasets from GitHub and convert
them to plain text files for the later steps.

Every file comes from a public GitHub repository, through github.com / codeload.github.com /
raw.githubusercontent.com only (see SOURCES below). No other servers are contacted.

  Translation (en / es / fr parallel text)
    * Tatoeba    short everyday sentences; three GitHub copies of Tatoeba data, merged and
                 deduplicated: Helsinki-NLP/Tatoeba-Challenge (eng-fra, eng-spa, fra-spa),
                 fra.txt (en-fr, manythings.org export) and doozan/spanish_data (en-es).
                 Extra es-fr pairs through a shared English sentence.
    * Europarl   European Parliament proceedings (UDS-SFB-B6-Datasets/Multilingual-Parallel-
                 Direct-Europarl, XML per session and language). Speeches are matched by id,
                 then paragraphs, then sentences.
  Language detection (24 EU languages)
    * Universal Dependencies treebanks (one or two per language)
    * the non-English side of Tatoeba-Challenge eng-xx files
    * Europarl sentences for de, it, nl, pt

Downloads are cached in training/data/raw/github/ and skipped when already there. If a
network only allows the github.com website, download the same files in a browser (the URLs are
printed by --list) into training/data/raw/github/ under the names shown, then run this script.

Output (training/data/raw/):
    github/...                          download cache (original files)
    parallel/<Corpus>_<a>-<b>.tsv       one sentence pair per line: <a text> TAB <b text>
    mono/<lang>.txt                     detector sentences, one per line, "<source>\t<text>"

Usage:  python training/download_data.py            (download + convert)
        python training/download_data.py --list     (print every URL and exit)
        python training/download_data.py --proxy http://proxy.corp:8080   (through a proxy)

Downloads use requests with the proxy for http and https, verify=False (no certificate check,
so corporate HTTPS inspection does not break them) and timeout=30 seconds per connect / read.
"""

from __future__ import annotations

import argparse
import io
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # because of verify=False

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
CACHE = RAW / "github"
PARALLEL = RAW / "parallel"
MONO = RAW / "mono"
SEED = 42

RAWGH = "https://raw.githubusercontent.com"
CODELOAD = "https://codeload.github.com"

# ---------------------------------------------------------------- sources (all on GitHub)
TC = f"{RAWGH}/Helsinki-NLP/Tatoeba-Challenge/master/data"
TATOEBA_FILES = {  # cache name -> URL
    "tc_eng-fra_dev.txt": f"{TC}/dev/eng-fra/dev.txt",
    "tc_eng-spa_dev.txt": f"{TC}/dev/eng-spa/dev.txt",
    "tc_fra-spa_dev.txt": f"{TC}/dev/fra-spa/dev.txt",
    "tc_eng-fra_test.txt": f"{TC}/devtest/eng-fra/test-v2023-04-12.txt",
    "tc_eng-spa_test.txt": f"{TC}/devtest/eng-spa/test-v2023-04-12.txt",
    "tc_fra-spa_test.txt": f"{TC}/devtest/fra-spa/test-v2023-04-12.txt",
    "manythings_fra.txt": f"{RAWGH}/viljarpuusepp/Attention/main/data/fra.txt",
    "doozan_sentences.tsv": f"{RAWGH}/doozan/spanish_data/master/sentences.tsv",
}

EUROPARL_ZIP = ("europarl_xml.zip",
                f"{CODELOAD}/UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl/zip/refs/heads/main")

# Universal Dependencies treebanks used for language detection (repo names on GitHub)
UD = {
    "bg": ["UD_Bulgarian-BTB"], "cs": ["UD_Czech-CAC"], "da": ["UD_Danish-DDT"],
    "de": ["UD_German-GSD"], "el": ["UD_Greek-GDT", "UD_Greek-GUD"], "en": ["UD_English-LinES"],
    "es": ["UD_Spanish-GSD"], "et": ["UD_Estonian-EDT"], "fi": ["UD_Finnish-TDT"],
    "fr": ["UD_French-GSD"], "ga": ["UD_Irish-IDT", "UD_Irish-TwittIrish"], "hr": ["UD_Croatian-SET"],
    "hu": ["UD_Hungarian-Szeged"], "it": ["UD_Italian-ISDT"], "lt": ["UD_Lithuanian-ALKSNIS"],
    "lv": ["UD_Latvian-LVTB"], "mt": ["UD_Maltese-MUDT"], "nl": ["UD_Dutch-Alpino", "UD_Dutch-LassySmall"],
    "pl": ["UD_Polish-PDB"], "pt": ["UD_Portuguese-GSD"], "ro": ["UD_Romanian-RRT"],
    "sk": ["UD_Slovak-SNK"], "sl": ["UD_Slovenian-SSJ", "UD_Slovenian-SST"], "sv": ["UD_Swedish-Talbanken"],
}

# ISO 639-3 codes used by Tatoeba-Challenge
ISO3 = {
    "bg": "bul", "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng",
    "es": "spa", "et": "est", "fi": "fin", "fr": "fra", "ga": "gle", "hr": "hrv",
    "hu": "hun", "it": "ita", "lt": "lit", "lv": "lav", "mt": "mlt", "nl": "nld",
    "pl": "pol", "pt": "por", "ro": "ron", "sk": "slk", "sl": "slv", "sv": "swe",
}
LID_EUROPARL = ["de", "it", "nl", "pt"]  # the other XML languages of the Europarl repo
PAIRS = [("en", "fr"), ("en", "es"), ("es", "fr")]


def tc_mono_files() -> dict[str, str]:
    """Tatoeba-Challenge dev/test files pairing English with each other EU language. The pair
    directory is alphabetical (bul-eng, eng-fra)."""
    out = {}
    for lang, iso in ISO3.items():
        if lang in ("en", "es", "fr"):
            continue
        pair = "-".join(sorted(["eng", iso]))
        out[f"tc_{pair}_dev.txt"] = f"{TC}/dev/{pair}/dev.txt"
        out[f"tc_{pair}_test.txt"] = f"{TC}/devtest/{pair}/test-v2023-04-12.txt"
    return out


def all_downloads() -> dict[str, str]:
    files = dict(TATOEBA_FILES)
    files.update(tc_mono_files())
    files[EUROPARL_ZIP[0]] = EUROPARL_ZIP[1]
    for repos in UD.values():
        for repo in repos:
            files[f"{repo}.zip"] = f"{CODELOAD}/UniversalDependencies/{repo}/zip/refs/heads/master"
    return files


# ---------------------------------------------------------------- download

OPTIONAL = re.compile(r"^tc_.*_(dev|test)\.txt$")  # not every language has both files
HEADERS = {"User-Agent": "translator-training/2.0"}
TIMEOUT = 30  # seconds to connect and between received data
proxies = None  # {"http": url, "https": url} from --proxy; None = HTTPS_PROXY / HTTP_PROXY


def fetch(name: str, url: str, attempts: int = 3) -> str:
    dest = CACHE / name
    if dest.is_file() and dest.stat().st_size > 0:
        return f"cached   {name}"
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, attempts + 1):
        try:
            with requests.get(url, headers=HEADERS, proxies=proxies, verify=False,
                              timeout=TIMEOUT, stream=True) as resp:
                if resp.status_code == 404 and OPTIONAL.match(name):
                    return f"absent   {name} (not published for this language)"
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        f.write(chunk)
            if name.endswith(".zip"):
                zipfile.ZipFile(tmp).testzip()
            tmp.replace(dest)
            return f"{dest.stat().st_size / 1e6:7.1f} MB {name}"
        except Exception as e:  # network error, truncated zip
            err = e
        time.sleep(3 * attempt)
    return f"FAILED   {name}: {err}  <- {url}"


# ---------------------------------------------------------------- helpers

BAD = chr(0xFFFD)  # replacement character: bytes that were not valid UTF-8 in the source


def one_line(s: str) -> str:
    return " ".join(s.split())  # no tabs / newlines inside a field


def write_pairs(name: str, a: str, b: str, pairs) -> int:
    PARALLEL.mkdir(parents=True, exist_ok=True)
    seen = set()
    with open(PARALLEL / f"{name}_{a}-{b}.tsv", "w", encoding="utf-8") as f:
        for x, y in pairs:
            x, y = one_line(x), one_line(y)
            if x and y and BAD not in x + y and (x, y) not in seen:
                seen.add((x, y))
                f.write(f"{x}\t{y}\n")
    print(f"  parallel/{name}_{a}-{b}.tsv: {len(seen):,} pairs", flush=True)
    return len(seen)


def read_tsv(name: str) -> list[list[str]]:
    path = CACHE / name
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", errors="replace", newline="\n") as f:
        return [line.rstrip("\n").split("\t") for line in f]


# ---------------------------------------------------------------- Tatoeba (en/es/fr)

def convert_tatoeba() -> None:
    print("Tatoeba", flush=True)
    pairs: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    code = {"eng": "en", "spa": "es", "fra": "fr"}
    for kind in ("dev", "test"):  # Tatoeba-Challenge: src_iso TAB tgt_iso TAB src TAB tgt
        for pair in ("eng-fra", "eng-spa", "fra-spa"):
            for row in read_tsv(f"tc_{pair}_{kind}.txt"):
                if len(row) >= 4 and row[0] in code and row[1] in code:
                    pairs[(code[row[0]], code[row[1]])].add((row[2].strip(), row[3].strip()))
    for row in read_tsv("manythings_fra.txt"):  # en TAB fr TAB attribution
        if len(row) >= 2:
            pairs[("en", "fr")].add((row[0].strip(), row[1].strip()))
    for row in read_tsv("doozan_sentences.tsv"):  # en TAB es TAB attribution ...
        if len(row) >= 2:
            pairs[("en", "es")].add((row[0].strip(), row[1].strip()))
    # normalise direction to PAIRS (fra-spa files are fr -> es)
    data = {p: set() for p in PAIRS}
    for (a, b), ps in pairs.items():
        if (a, b) in data:
            data[(a, b)] |= ps
        elif (b, a) in data:
            data[(b, a)] |= {(y, x) for x, y in ps}
    # es-fr through English: sentences with exactly one translation into each language
    fr_of, es_of = defaultdict(set), defaultdict(set)
    for en, fr in data[("en", "fr")]:
        fr_of[en].add(fr)
    for en, es in data[("en", "es")]:
        es_of[en].add(es)
    pivot = {(next(iter(es_of[en])), next(iter(fr_of[en])))
             for en in es_of.keys() & fr_of.keys() if len(es_of[en]) == 1 and len(fr_of[en]) == 1}
    n_direct = len(data[("es", "fr")])
    data[("es", "fr")] |= pivot
    print(f"  es-fr: {n_direct:,} direct + {len(data[('es', 'fr')]) - n_direct:,} via English", flush=True)
    for a, b in PAIRS:
        write_pairs("Tatoeba", a, b, sorted(data[(a, b)]))


# ---------------------------------------------------------------- Europarl XML

_SENT_END = re.compile(r"(?<=[.!?;])[\"'»”)]*\s+(?=[\"'«“¿¡(]*[A-ZÀ-ÖØ-Þ0-9])")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_END.split(text) if s.strip()]


def read_session(z: zipfile.ZipFile, name: str) -> dict[str, list[str]]:
    """intervention id -> its paragraphs"""
    root = ET.fromstring(z.read(name))
    out = {}
    for iv in root.iter("intervention"):
        paras = ["".join(p.itertext()).strip() for p in iv.iter("p")]
        if iv.get("id") and paras:
            out[iv.get("id")] = paras
    return out


# Frequent function words that are distinctive for one language of the Europarl XML. Some
# speeches are left untranslated in other languages' files (e.g. written explanations of vote:
# "per iscritto . -" inside the French file); paragraphs in the wrong language are dropped.
STOPWORDS = {
    "en": "the of and to is that this we are with which have for not be by it".split(),
    "es": "el los las del por para una con es se al lo como más pero y su".split(),
    "fr": "le les des et est une pour dans qui pas nous sur du au ce sont".split(),
    "it": "di che è per sono della gli nel questo anche alla delle".split(),
    "pt": "o os em é uma não com do dos das na um ao".split(),
    "de": "der die und das ist nicht ich wir zu den mit von sich auch ein eine für dem".split(),
    "nl": "het een van dat niet op voor met zijn wij ook aan worden deze".split(),
}
_STOP = {lang: set(words) for lang, words in STOPWORDS.items()}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def looks_like(text: str, lang: str) -> bool:
    """False if the function words clearly point to another language."""
    words = _WORD.findall(text.lower())
    hits = {l: sum(w in s for w in words) for l, s in _STOP.items()}
    best = max(hits, key=hits.get)
    if hits[best] < 3:  # too short to tell
        return True
    return best == lang or hits[lang] >= hits[best] * 0.8


def align(pa: list[str], pb: list[str], a: str, b: str) -> list[tuple[str, str]]:
    """Pair the paragraphs of one speech, then their sentences. Only 1:1 matches are kept."""
    out = []
    if len(pa) != len(pb):
        return out
    for x, y in zip(pa, pb):
        if not (looks_like(x, a) and looks_like(y, b)):
            continue
        sx, sy = split_sentences(x), split_sentences(y)
        if len(sx) == len(sy):
            out += list(zip(sx, sy))
        elif len(x) <= 400 and len(y) <= 400:
            out.append((x, y))
    return out


def convert_europarl(rng: random.Random) -> None:
    print("Europarl", flush=True)
    langs = ["en", "es", "fr", *LID_EUROPARL]
    with zipfile.ZipFile(CACHE / EUROPARL_ZIP[0]) as z:
        files: dict[str, dict[str, str]] = defaultdict(dict)  # session -> lang -> member
        for n in z.namelist():
            m = re.search(r"/proceedings/xml/([a-z]{2})/(\d{8})\.[A-Z]{2}\.xml$", n)
            if m and m.group(1) in langs:
                files[m.group(2)][m.group(1)] = n
        pairs = {p: [] for p in PAIRS}
        mono = defaultdict(list)
        for session in sorted(files):
            texts = {l: read_session(z, n) for l, n in files[session].items()}
            for a, b in PAIRS:
                if a in texts and b in texts:
                    for iid in texts[a].keys() & texts[b].keys():
                        pairs[(a, b)] += align(texts[a][iid], texts[b][iid], a, b)
            for l in LID_EUROPARL:
                for paras in texts.get(l, {}).values():
                    for p in paras:
                        if looks_like(p, l):
                            mono[l] += split_sentences(p)
    print(f"  {len(files):,} sessions", flush=True)
    for a, b in PAIRS:
        write_pairs("Europarl", a, b, pairs[(a, b)])
    for l in LID_EUROPARL:
        sents = list(dict.fromkeys(mono[l]))
        rng.shuffle(sents)
        add_mono(l, "europarl", sents[:100_000])


# ---------------------------------------------------------------- language detection

_mono: dict[str, list[tuple[str, str]]] = defaultdict(list)


def add_mono(lang: str, source: str, sentences) -> None:
    for s in sentences:
        s = one_line(s)
        if s and BAD not in s:
            _mono[lang].append((source, s))


def convert_ud() -> None:
    print("Universal Dependencies", flush=True)
    for lang, repos in UD.items():
        for repo in repos:
            path = CACHE / f"{repo}.zip"
            if not path.is_file():
                continue
            sents = []
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    if n.endswith(".conllu"):
                        for line in io.TextIOWrapper(z.open(n), encoding="utf-8", errors="replace"):
                            if line.startswith("# text = "):
                                sents.append(line[9:])
            add_mono(lang, "ud", sents)


def convert_tc_mono() -> None:
    print("Tatoeba-Challenge sentences", flush=True)
    for lang, iso in ISO3.items():
        if lang in ("en", "es", "fr"):
            continue
        pair = "-".join(sorted(["eng", iso]))
        for kind in ("dev", "test"):
            rows = read_tsv(f"tc_{pair}_{kind}.txt")
            # src_iso TAB tgt_iso TAB src TAB tgt; ISO codes may be variants (e.g. "lvs")
            add_mono(lang, "tatoeba", [r[3] if r[0] == "eng" else r[2] for r in rows if len(r) >= 4])


def write_mono() -> None:
    # en/es/fr: plus the Tatoeba side of the translation data
    for lang in ("en", "es", "fr"):
        seen = set()
        for a, b in PAIRS:
            path = PARALLEL / f"Tatoeba_{a}-{b}.tsv"
            col = 0 if lang == a else 1 if lang == b else None
            if col is None:
                continue
            for line in open(path, encoding="utf-8", newline="\n"):
                s = line.rstrip("\n").split("\t")[col]
                if s not in seen:
                    seen.add(s)
                    _mono[lang].append(("tatoeba", s))
    MONO.mkdir(parents=True, exist_ok=True)
    for lang in ISO3:
        rows = list(dict.fromkeys(_mono[lang]))
        with open(MONO / f"{lang}.txt", "w", encoding="utf-8") as f:
            for src, s in rows:
                f.write(f"{src}\t{s}\n")
        by_src = defaultdict(int)
        for src, _ in rows:
            by_src[src] += 1
        print(f"  mono/{lang}.txt: {len(rows):>8,} sentences {dict(by_src)}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="print every download URL and exit")
    p.add_argument("--proxy", default="",
                   help="proxy for all downloads, e.g. http://proxy.corp:8080 or "
                        "http://user:password@proxy.corp:8080 (default: HTTPS_PROXY / HTTP_PROXY)")
    args = p.parse_args()
    if args.proxy:
        global proxies
        proxy = args.proxy if "://" in args.proxy else "http://" + args.proxy
        proxies = {"http": proxy, "https": proxy}
        print(f"proxy: {re.sub(r'//[^/@]*@', '//***@', proxy)}", flush=True)
    files = all_downloads()
    if args.list:
        for name, url in files.items():
            print(f"{url}\n    -> training/data/raw/github/{name}")
        return

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda kv: fetch(*kv), files.items()))
    for r in results:
        print(r, flush=True)
    failed = [r for r in results if r.startswith("FAILED")]
    if failed:
        sys.exit(f"{len(failed)} download(s) failed; re-run to retry (cached files are kept).")

    rng = random.Random(SEED)
    convert_tatoeba()
    convert_europarl(rng)
    convert_ud()
    convert_tc_mono()
    write_mono()
    print("done", flush=True)


if __name__ == "__main__":
    main()
