"""Step 2: clean the parallel data, split dev/test, train the tokenizer, encode to arrays.

Input:  training/data/raw/parallel/*.zip   (from download_data.py)
Output: training/data/prepared/
          spm.model / spm.vocab        our SentencePiece tokenizer (trained here)
          train_{src,tgt}.bin + _off.npy  token ids (uint16) + offsets
          train_dir.npy                   direction id per example
          train_corpus.npy                corpus id per example (index into CORPORA)
          dev.tsv / test.tsv              held-out sentences: src_lang, tgt_lang, src, ref, corpus
                                          (test.tsv also has domain_test.tsv as corpus "Domain")

Usage:  python training/prepare_data.py [--europarl-per-pair 300000] [--reuse-tokenizer]
"""

from __future__ import annotations

import argparse
import io
import random
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import sentencepiece as spm

from domain_data import domain_triplets, name_augment

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw" / "parallel"
OUT = ROOT / "data" / "prepared"

LANGS = ["en", "es", "fr"]
PAIRS = [("en", "fr"), ("en", "es"), ("es", "fr")]
DIRECTIONS = [(s, t) for s in LANGS for t in LANGS if s != t]  # 6 directions
CORPORA = ["Tatoeba", "NewsCommentary", "Europarl", "Domain", "Names"]  # Domain/Names: domain_data.py
DOMAIN_TEST = ROOT / "domain_test.tsv"  # hand-written held-out car-service / greeting sentences
VOCAB_SIZE = 16000
MAX_TOKENS = 128
SEED = 1234

_letters = re.compile(r"[^\W\d_]", re.UNICODE)
_end_punct = re.compile(r"[\s.!?;:,]+$")


def read_pairs(corpus: str, a: str, b: str) -> list[tuple[str, str]]:
    """Read one OPUS Moses zip into (a_text, b_text) pairs."""
    path = RAW / f"{corpus}_{a}-{b}.zip"
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        fa = next(n for n in names if n.endswith(f".{a}-{b}.{a}"))
        fb = next(n for n in names if n.endswith(f".{a}-{b}.{b}"))
        with z.open(fa) as ha, z.open(fb) as hb:
            la = io.TextIOWrapper(ha, encoding="utf-8", errors="replace")
            lb = io.TextIOWrapper(hb, encoding="utf-8", errors="replace")
            return [(x.strip(), y.strip()) for x, y in zip(la, lb)]


def clean(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen = set()
    out = []
    for x, y in pairs:
        if not x or not y or x == y:
            continue
        if not _letters.search(x) or not _letters.search(y):
            continue
        lx, ly = len(x), len(y)
        if lx > 400 or ly > 400:
            continue
        if min(lx, ly) > 15 and max(lx, ly) / min(lx, ly) > 2.2:
            continue  # likely misaligned
        key = (x, y)
        if key in seen:
            continue
        seen.add(key)
        out.append((x, y))
    return out


def casual(text: str) -> str:
    """Lowercase and drop final punctuation: imitates quickly typed input."""
    return _end_punct.sub("", text.lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--europarl-per-pair", type=int, default=300_000)
    parser.add_argument("--dev-per-corpus", type=int, default=200)
    parser.add_argument("--test-per-corpus", type=int, default=500)
    parser.add_argument("--reuse-tokenizer", action="store_true",
                        help="keep the existing spm.model (so existing checkpoints stay valid)")
    args = parser.parse_args()

    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    data: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for a, b in PAIRS:
        for corpus in CORPORA[:3]:  # the OPUS corpora; Domain/Names are generated below
            pairs = clean(read_pairs(corpus, a, b))
            rng.shuffle(pairs)
            if corpus == "Europarl":
                pairs = pairs[: args.europarl_per_pair]
            data[(corpus, a, b)] = pairs
            print(f"{corpus:15} {a}-{b}: {len(pairs):>9,} clean pairs", flush=True)

    # ---- held-out dev/test from short (Tatoeba) and general (News) text ----
    dev, test = [], []
    held_out: dict[str, set[str]] = defaultdict(set)
    for a, b in PAIRS:
        for corpus in ("Tatoeba", "NewsCommentary"):
            pairs = data[(corpus, a, b)]
            n_test, n_dev = args.test_per_corpus, args.dev_per_corpus
            chunk_test, chunk_dev = pairs[:n_test], pairs[n_test : n_test + n_dev]
            data[(corpus, a, b)] = pairs[n_test + n_dev :]
            for split, chunk in ((test, chunk_test), (dev, chunk_dev)):
                for x, y in chunk:
                    split.append((a, b, x, y, corpus))
                    split.append((b, a, y, x, corpus))
                    held_out[a].add(x)
                    held_out[b].add(y)

    # hand-written domain test set: en, es, fr per line -> 6 directions
    with open(DOMAIN_TEST, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = dict(zip(LANGS, line.rstrip("\n").split("\t")))
            for a, b in DIRECTIONS:
                test.append((a, b, row[a], row[b], "Domain"))
            for lang, text in row.items():
                held_out[lang].add(text)
    held_casual = {casual(x) for v in held_out.values() for x in v}

    # ---- training examples in both directions, excluding held-out sentences ----
    # (a separate rng for the new data, so the original examples are unchanged)
    aug_rng = random.Random(SEED + 1)
    examples: list[tuple[str, str, str, str, int]] = []  # (src_lang, tgt_lang, src, tgt, corpus)
    names_ci = CORPORA.index("Names")
    for (corpus, a, b), pairs in data.items():
        ci = CORPORA.index(corpus)
        for x, y in pairs:
            if x in held_out[a] or y in held_out[b]:
                continue
            examples.append((a, b, x, y, ci))
            examples.append((b, a, y, x, ci))
            if corpus == "Tatoeba" and rng.random() < 0.15:
                examples.append((a, b, casual(x), y, ci))
                examples.append((b, a, casual(y), x, ci))
            if corpus == "Tatoeba" and aug_rng.random() < 0.5 and (swapped := name_augment(x, y, aug_rng)):
                nx, ny = swapped
                examples.append((a, b, nx if aug_rng.random() < 0.7 else casual(nx), ny, names_ci))
                examples.append((b, a, ny if aug_rng.random() < 0.7 else casual(ny), nx, names_ci))
    domain_ci = CORPORA.index("Domain")
    n_domain_skipped = 0
    for triplet in domain_triplets(aug_rng):
        row = dict(zip(LANGS, triplet))
        if any(casual(t) in held_casual for t in triplet):
            n_domain_skipped += 1
            continue
        for a, b in DIRECTIONS:
            src = row[a] if aug_rng.random() < 0.7 else casual(row[a])
            examples.append((a, b, src, row[b], domain_ci))
    print(f"domain sentences overlapping the test set, skipped: {n_domain_skipped}", flush=True)
    rng.shuffle(examples)
    for ci, corpus in enumerate(CORPORA):
        print(f"  {corpus:15} {sum(e[4] == ci for e in examples):>10,} examples", flush=True)
    print(f"training examples (both directions): {len(examples):,}", flush=True)

    for name, split in (("dev", dev), ("test", test)):
        with open(OUT / f"{name}.tsv", "w", encoding="utf-8") as f:
            for s, t, x, y, corpus in split:
                f.write(f"{s}\t{t}\t{x}\t{y}\t{corpus}\n")
        print(f"{name}: {len(split):,} sentences", flush=True)

    # ---- our own SentencePiece tokenizer, shared by all three languages ----
    if args.reuse_tokenizer and (OUT / "spm.model").is_file():
        print("reusing existing tokenizer", flush=True)
    else:
        train_tokenizer(examples, rng)
    sp = spm.SentencePieceProcessor(model_file=str(OUT / "spm.model"))
    print("tokenizer:", sp.get_piece_size(), "pieces", flush=True)
    encode_and_save(examples, sp)


def train_tokenizer(examples, rng: random.Random) -> None:
    sample_path = OUT / "spm_sample.txt"
    per_lang: dict[str, list[str]] = defaultdict(list)
    for e in examples:
        per_lang[e[0]].append(e[2])
    with open(sample_path, "w", encoding="utf-8") as f:
        for lang in LANGS:
            sents = per_lang[lang]
            for line in rng.sample(sents, min(400_000, len(sents))):
                f.write(line.replace("\n", " ") + "\n")
    spm.SentencePieceTrainer.train(
        input=str(sample_path),
        model_prefix=str(OUT / "spm"),
        vocab_size=VOCAB_SIZE,
        model_type="unigram",
        character_coverage=1.0,
        byte_fallback=True,
        pad_id=0, unk_id=1, bos_id=2, eos_id=3,
        user_defined_symbols=[f"<2{l}>" for l in LANGS],
        input_sentence_size=1_200_000,
        shuffle_input_sentence=True,
        num_threads=4,
    )
    sample_path.unlink()


def encode_and_save(examples, sp: spm.SentencePieceProcessor) -> None:
    dir_index = {d: i for i, d in enumerate(DIRECTIONS)}
    src_ids = sp.encode([e[2] for e in examples])
    tgt_ids = sp.encode([e[3] for e in examples])
    keep = [
        i for i in range(len(examples))
        if 0 < len(src_ids[i]) <= MAX_TOKENS and 0 < len(tgt_ids[i]) <= MAX_TOKENS
    ]
    print(f"kept {len(keep):,} of {len(examples):,} within {MAX_TOKENS} tokens", flush=True)

    def save(name: str, seqs) -> None:
        lengths = np.fromiter((len(seqs[i]) for i in keep), dtype=np.int64, count=len(keep))
        offsets = np.zeros(len(keep) + 1, dtype=np.int64)
        np.cumsum(lengths, out=offsets[1:])
        flat = np.fromiter((tok for i in keep for tok in seqs[i]), dtype=np.uint16, count=int(offsets[-1]))
        flat.tofile(OUT / f"train_{name}.bin")
        np.save(OUT / f"train_{name}_off.npy", offsets)

    save("src", src_ids)
    save("tgt", tgt_ids)
    np.save(OUT / "train_dir.npy", np.array([dir_index[examples[i][:2]] for i in keep], dtype=np.uint8))
    np.save(OUT / "train_corpus.npy", np.array([examples[i][4] for i in keep], dtype=np.uint8))
    total = sum(len(tgt_ids[i]) for i in keep)
    print(f"target tokens per epoch: {total:,}", flush=True)
    for ci, corpus in enumerate(CORPORA):
        n = sum(len(tgt_ids[i]) for i in keep if examples[i][4] == ci)
        print(f"  {corpus:15} {n:>12,} target tokens", flush=True)


if __name__ == "__main__":
    main()
