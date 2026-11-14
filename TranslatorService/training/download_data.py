"""Step 1 (needs internet): download the public training datasets.

Parallel text (translation), from OPUS (https://opus.nlpl.eu), Moses plain-text format:
  * Tatoeba          - short everyday sentences          (CC BY 2.0 FR)
  * Europarl v8      - European Parliament proceedings   (free, no restrictions)
  * News-Commentary  - news analysis articles            (free for research and commercial use)

Monolingual sentences (language detection), from Tatoeba (https://tatoeba.org) for the
24 official EU languages.

Usage:  python training/download_data.py
"""

from __future__ import annotations

import bz2
import sys
import urllib.request
import zipfile

try:  # verify HTTPS with the operating system's certificate store (fills in missing chains)
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "raw"

PAIRS = ["en-fr", "en-es", "es-fr"]
PARALLEL = {
    "Tatoeba": "OPUS-Tatoeba/v2026-07-08",
    "Europarl": "OPUS-Europarl/v8",
    "NewsCommentary": "OPUS-News-Commentary/v16",
}

# Tatoeba uses ISO 639-3 codes ("lvs" = Standard Latvian)
TATOEBA_ISO3 = {
    "bg": "bul", "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng",
    "es": "spa", "et": "est", "fi": "fin", "fr": "fra", "ga": "gle", "hr": "hrv",
    "hu": "hun", "it": "ita", "lt": "lit", "lv": "lvs", "mt": "mlt", "nl": "nld",
    "pl": "pol", "pt": "por", "ro": "ron", "sk": "slk", "sl": "slv", "sv": "swe",
}


# Languages with < 50k Tatoeba sentences get extra detector data from OPUS QED
# (subtitles of educational videos: short, natural sentences). Only the non-English side is used.
QED_EXTRA = ["bg", "el", "et", "ga", "hr", "lv", "mt", "ro", "sk", "sl"]
QED_PATH = "OPUS-QED/v2.0a"


def jobs() -> list[tuple[str, Path]]:
    out = []
    for name, path in PARALLEL.items():
        for pair in PAIRS:
            url = f"https://object.pouta.csc.fi/{path}/moses/{pair}.txt.zip"
            out.append((url, DATA / "parallel" / f"{name}_{pair}.zip"))
    for iso3 in TATOEBA_ISO3.values():
        url = f"https://downloads.tatoeba.org/exports/per_language/{iso3}/{iso3}_sentences.tsv.bz2"
        out.append((url, DATA / "mono" / f"{iso3}_sentences.tsv.bz2"))
    for lang in QED_EXTRA:
        pair = "-".join(sorted(["en", lang]))  # OPUS orders pairs alphabetically (bg-en, en-et)
        url = f"https://object.pouta.csc.fi/{QED_PATH}/moses/{pair}.txt.zip"
        out.append((url, DATA / "mono" / f"QED_{pair}.zip"))
    return out


def is_complete(path: Path) -> bool:
    """True if the archive decompresses fully (catches truncated downloads)."""
    try:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                return z.testzip() is None
        if path.suffix == ".bz2":
            with bz2.open(path, "rb") as f:
                while f.read(1 << 22):
                    pass
            return True
    except (OSError, EOFError, zipfile.BadZipFile):
        return False
    return path.stat().st_size > 0


def fetch(job: tuple[str, Path], attempts: int = 3) -> str:
    url, dest = job
    if dest.is_file() and is_complete(dest):
        return f"exists  {dest.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "translator-training/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        tmp.replace(dest)
        if is_complete(dest):
            return f"{dest.stat().st_size / 1e6:8.1f} MB  {dest.name}"
        print(f"incomplete download of {dest.name} (attempt {attempt}), retrying", flush=True)
    raise OSError(f"{dest.name} still incomplete after {attempts} attempts")


def main() -> None:
    failed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for job, result in zip(jobs(), pool.map(_safe_fetch, jobs())):
            print(result, flush=True)
            failed += result.startswith("FAILED")
    if failed:
        sys.exit(f"{failed} download(s) failed; re-run to retry.")


def _safe_fetch(job):
    try:
        return fetch(job)
    except Exception as e:  # report and continue with the others
        return f"FAILED  {job[0]}: {e}"


if __name__ == "__main__":
    main()
