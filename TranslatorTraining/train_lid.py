"""Step 4: train our own language detector for the 24 EU languages.

Data (data/raw/mono/<lang>.txt from download_data.py, all from GitHub): Tatoeba
sentences, Universal Dependencies treebank sentences and, for de/it/nl/pt, Europarl sentences.
Balanced to --per-lang sentences per language (small languages are oversampled).

Robustness augmentation (applied to a copy of part of the data):
  * random person / place / brand names inserted (names are not evidence of a language)
  * punctuation removed
  * cut down to the first 1-4 words (very short input)

Plus lid_phrases.tsv: greetings and short chat phrases in all 24 languages (Tatoeba has
few of them, and treebank / parliament text has almost none), repeated with
names, casing and punctuation variants. Phrases that also occur in an evaluation set are
dropped, so the evaluation stays blind.

Output: models/lid/lid.pt + config.json. Prints accuracy on held-out sentences and on the
challenge set lid_challenge.tsv (short phrases, names, casual text).

Usage:  python train_lid.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent
SERVICE = ROOT.parent / "TranslatorServer"  # the API folder: model code (translator/) and models/
sys.path.insert(0, str(SERVICE))
from translator.languages import LANGUAGE_CODES  # noqa: E402
from translator.lid import LIDModel, features  # noqa: E402

MONO = ROOT / "data" / "raw" / "mono"
EXPORT = SERVICE / "models" / "lid"
CHALLENGE = ROOT / "lid_challenge.tsv"
EVERYDAY = ROOT / "lid_everyday.tsv"  # everyday en/es/fr phrases (evaluation only)
EVERYDAY2 = ROOT / "lid_everyday2.tsv"  # second blind set of casual phrases (evaluation only)
EVAL_SETS = (CHALLENGE, EVERYDAY, EVERYDAY2)
PHRASES = ROOT / "lid_phrases.tsv"  # hand-written greetings / short chat phrases (training)
SEED = 7


# Person and place names from many cultures, inserted into every language's sentences so the
# model learns that foreign names are not evidence of a language. No English-word brand names
# (e.g. "YouTube" shares n-grams with "you", which would teach the model that English words
# appear in every language). Deliberately excludes the names in the challenge set.
NAMES = """Ravi Priya Arjun Lakshmi Suresh Anand Kavya Vetrivel Krishnan Deepa Karthik Meena
Mohammed Fatima Omar Aisha Wei Mei Hiroshi Yuki Kenji Olga Dmitri Kwame Amara Chidi Ngozi
Siddharth Thandiwe Mehmet Ayşe Mumbai Delhi Bangalore Hyderabad Kolkata Pune Tokyo Osaka
Lagos Nairobi Shanghai Seoul Peugeot Citroën Stellantis TCS Infosys BMW Toyota""".split()

_PUNCT = re.compile(r"[^\w\s'’-]", re.UNICODE)


def read_sentences(lang: str) -> list[str]:
    """mono/<lang>.txt from download_data.py: "<source> TAB <sentence>" per line
    (sources: tatoeba, ud, europarl)."""
    path = MONO / f"{lang}.txt"
    if not path.is_file():
        sys.exit(f"{path} not found: run download_data.py first")
    with open(path, encoding="utf-8", newline="\n") as f:
        return [line.rstrip("\n").partition("\t")[2].strip() for line in f]


def usable(s: str) -> bool:
    return 2 <= len(s) <= 300 and sum(ch.isalpha() for ch in s) >= 2


_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def words_of(s: str) -> list[str]:
    return _TOKEN.findall(s.lower())


def top_words(sentences: list[str], n: int) -> set[str]:
    counts = Counter(w for s in sentences for w in words_of(s))
    return {w for w, _ in counts.most_common(n)}


def english_like(s: str, english: set[str], native: set[str], clean_native: set[str]) -> bool:
    """Untranslated English left in another language's data (e.g. English titles, quotes or names of
    organisations kept untranslated in treebank or parliament text): mostly common English words that
    are not common words of the language itself.

    `native` = frequent words of the (possibly contaminated) training pool;
    `clean_native` = frequent words of the language's human-written Tatoeba sentences, used for
    short lines because a contaminated pool can contain so much English that English words are
    themselves frequent there."""
    words = words_of(s)
    if not words:
        return False
    if len(words) <= 3 and all(w in english and w not in clean_native for w in words):
        return True
    foreign = sum(w in english and w not in native for w in words)
    return foreign / len(words) >= 0.6


def augment(s: str, rng: random.Random) -> str:
    words = s.split()
    r = rng.random()
    if r < 0.25 and words:
        words.insert(rng.randint(0, len(words)), rng.choice(NAMES))
        return " ".join(words)
    if r < 0.6:
        return _PUNCT.sub(" ", s)
    return " ".join(words[: rng.randint(1, 4)])


def norm(s: str) -> str:
    return " ".join(_PUNCT.sub(" ", s.lower()).split())


def phrase_examples(langs: list[str], rng: random.Random, repeat: int) -> list[tuple[str, int]]:
    eval_texts = {norm(t) for path in EVAL_SETS if path.is_file() for _, _, t in read_eval(path)}
    out, dropped = [], 0
    for line in open(PHRASES, encoding="utf-8"):
        if not line.strip():
            continue
        lang, text = line.rstrip("\n").split("\t")
        if norm(text) in eval_texts:
            dropped += 1
            continue
        li = langs.index(lang)
        for _ in range(repeat):
            r = rng.random()
            if r < 0.3:
                t = text
            elif r < 0.55:
                t = norm(text)
            elif r < 0.7:
                t = text.capitalize() + rng.choice(["", ".", "!", "?"])
            elif r < 0.85:
                t = f"{text} {rng.choice(NAMES)}"
            else:
                t = f"{rng.choice(NAMES)}, {norm(text)}"
            out.append((t, li))
    print(f"phrases: {len(out):,} examples ({dropped} phrases dropped because they are in an evaluation set)")
    return out


def encode(texts: list[str], buckets: int) -> tuple[torch.Tensor, torch.Tensor]:
    flat, offsets = [], []
    for t in texts:
        offsets.append(len(flat))
        flat.extend(features(t, buckets) or [0])
    return torch.tensor(flat, dtype=torch.long), torch.tensor(offsets, dtype=torch.long)


@torch.no_grad()
def logits_of(model, texts: list[str], buckets: int, batch: int = 4096) -> torch.Tensor:
    model.eval()
    out = []
    for i in range(0, len(texts), batch):
        flat, offsets = encode(texts[i : i + batch], buckets)
        out.append(model(flat, offsets))
    model.train()
    return torch.cat(out)


def predict(model, texts: list[str], buckets: int, bias: torch.Tensor | None = None) -> list[int]:
    logits = logits_of(model, texts, buckets)
    if bias is not None:
        logits = logits + bias
    return logits.argmax(-1).tolist()


def read_eval(path: Path) -> list[tuple[str, str, str]]:
    """(category, lang, text) rows; files without a category column get the file name."""
    rows = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            parts = line.rstrip("\n").split("\t")
            rows.append((parts[0], parts[1], parts[2]) if len(parts) == 3 else (path.stem, parts[0], parts[1]))
    return rows


def calibrate_prior(model, test, langs, main_langs, buckets, rng, max_cost) -> tuple[float, torch.Tensor]:
    """Choose the largest log-prior boost for the main languages that costs the other
    languages at most `max_cost` held-out accuracy (on full sentences and on 1-3 word
    fragments of them, where the prior matters most)."""
    texts, labels = [], []
    for s, y in test:
        texts.append(s)
        labels.append(y)
        words = s.split()
        texts.append(" ".join(words[: rng.randint(1, 3)]))
        labels.append(y)
    logits = logits_of(model, texts, buckets)
    labels_t = torch.tensor(labels)
    main_idx = torch.tensor([langs.index(l) for l in main_langs])
    is_main = torch.isin(labels_t, main_idx)

    def accuracy(boost: float):
        bias = torch.zeros(len(langs))
        bias[main_idx] = boost
        ok = (logits + bias).argmax(-1) == labels_t
        return ok[~is_main].float().mean().item(), ok[is_main].float().mean().item(), bias

    base_other, base_main, _ = accuracy(0.0)
    best = (0.0, torch.zeros(len(langs)))
    print(f"prior calibration (held-out full + short fragments): boost 0.0 -> other "
          f"{base_other:.2%}, main {base_main:.2%}")
    for boost in [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]:
        other, main, bias = accuracy(boost)
        print(f"   boost {boost:.2f} -> other {other:.2%}, main {main:.2%}")
        if base_other - other <= max_cost:
            best = (boost, bias)
    print(f"chosen prior boost: {best[0]}")
    return best


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--per-lang", type=int, default=40_000)
    p.add_argument("--main-langs", default="en,es,fr",
                   help="languages the translator supports: more training data and a calibrated prior")
    p.add_argument("--main-per-lang", type=int, default=120_000)
    p.add_argument("--max-prior-cost", type=float, default=0.005,
                   help="largest allowed drop in held-out accuracy of the other languages from the prior")
    p.add_argument("--test-per-lang", type=int, default=500)
    p.add_argument("--phrase-repeat", type=int, default=40, help="copies of each lid_phrases.tsv line")
    p.add_argument("--buckets", type=int, default=2**20)
    p.add_argument("--dim", type=int, default=32)
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--lr", type=float, default=0.05)
    p.add_argument("--threads", type=int, default=2)
    args = p.parse_args()
    torch.set_num_threads(args.threads)
    torch.manual_seed(SEED)
    rng = random.Random(SEED)
    langs = LANGUAGE_CODES
    main_langs = [l for l in args.main_langs.split(",") if l]

    t0 = time.time()
    train, test = [], []
    english_top = top_words(read_sentences("en")[:300_000], 5000)
    dropped = Counter()
    for li, lang in enumerate(langs):
        per_lang = args.main_per_lang if lang in main_langs else args.per_lang
        sentences = list(dict.fromkeys(s for s in read_sentences(lang) if usable(s)))
        rng.shuffle(sentences)
        n_test = min(args.test_per_lang, len(sentences) // 5)  # small languages: at most 20%
        test += [(s, li) for s in sentences[:n_test]]
        pool = sentences[n_test:]
        clean_native = top_words(pool, 1000)
        if lang != "en":
            native_top = top_words(pool, 3000)
            before = len(pool)
            pool = [s for s in pool if not english_like(s, english_top, native_top, clean_native)]
            dropped[lang] = before - len(pool)
        pool = pool[:per_lang]
        # oversample small languages so every language has a similar weight
        while len(pool) < per_lang // 2:
            pool += pool
        train += [(s, li) for s in pool]
        train += [(augment(s, rng), li) for s in rng.sample(pool, len(pool) // 2)]
        print(f"{lang}: {len(pool):>6,} sentences", flush=True)
    print("dropped English-like lines:", {l: n for l, n in dropped.items() if n})
    train += phrase_examples(langs, rng, args.phrase_repeat)
    rng.shuffle(train)
    print(f"{len(train):,} training examples, {len(test):,} test; loading took {time.time() - t0:.0f}s", flush=True)

    t0 = time.time()
    texts = [s for s, _ in train]
    labels = torch.tensor([y for _, y in train])
    feats = [features(s, args.buckets) or [0] for s in texts]
    print(f"features extracted in {time.time() - t0:.0f}s", flush=True)

    model = LIDModel(args.buckets, args.dim, len(langs))
    opt_sparse = torch.optim.SparseAdam(list(model.embed.parameters()), lr=args.lr / 5)
    opt_dense = torch.optim.Adam(model.out.parameters(), lr=args.lr / 5)
    batch = 256
    for epoch in range(args.epochs):
        order = list(range(len(feats)))
        rng.shuffle(order)
        total, t0 = 0.0, time.time()
        for i in range(0, len(order), batch):
            idx = order[i : i + batch]
            flat, offsets = [], []
            for j in idx:
                offsets.append(len(flat))
                flat.extend(feats[j])
            logits = model(torch.tensor(flat), torch.tensor(offsets))
            loss = F.cross_entropy(logits, labels[idx])
            opt_sparse.zero_grad()
            opt_dense.zero_grad()
            loss.backward()
            opt_sparse.step()
            opt_dense.step()
            total += loss.item() * len(idx)
        preds = predict(model, [s for s, _ in test], args.buckets)
        acc = sum(p == y for p, (_, y) in zip(preds, test)) / len(test)
        print(f"epoch {epoch + 1}: loss {total / len(order):.4f}, held-out accuracy {acc:.2%} "
              f"({time.time() - t0:.0f}s)", flush=True)

    # per-language held-out accuracy and the challenge set
    per_lang = defaultdict(lambda: [0, 0])
    for pr, (_, y) in zip(preds, test):
        per_lang[langs[y]][0] += pr == y
        per_lang[langs[y]][1] += 1
    print("held-out per language:", {l: f"{c / n:.0%}" for l, (c, n) in sorted(per_lang.items())})
    boost, bias = calibrate_prior(model, test, langs, main_langs, args.buckets, rng, args.max_prior_cost)
    acc = sum(p == y for p, (_, y) in zip(predict(model, [s for s, _ in test], args.buckets, bias), test)) / len(test)
    print(f"held-out accuracy with prior: {acc:.2%}")

    # Evaluation sets that are never used for training or calibration
    results = {}
    for path in EVAL_SETS:
        if not path.is_file():
            continue
        rows = read_eval(path)
        preds_c = predict(model, [t for _, _, t in rows], args.buckets, bias)
        ok = [langs[p] == lang for p, (_, lang, _) in zip(preds_c, rows)]
        results[path.stem] = round(sum(ok) / len(ok), 4)
        by_cat, tot_cat = Counter(), Counter()
        for good, (cat, _, _) in zip(ok, rows):
            by_cat[cat] += good
            tot_cat[cat] += 1
        print(f"{path.name}: {sum(ok)}/{len(ok)} ({sum(ok) / len(ok):.1%}) "
              + ", ".join(f"{c} {by_cat[c]}/{tot_cat[c]}" for c in tot_cat))
        for good, (cat, lang, text), pr in zip(ok, rows, preds_c):
            if not good:
                print(f"   miss: {lang}->{langs[pr]}  {text}")

    EXPORT.mkdir(parents=True, exist_ok=True)
    state = {k: v.half() for k, v in model.state_dict().items()}
    torch.save(state, EXPORT / "lid.pt")
    with open(EXPORT / "config.json", "w", encoding="utf-8") as f:
        json.dump({"languages": langs, "buckets": args.buckets, "dim": args.dim,
                   "max_n": 4, "min_n": 2, "word_weight": 2,
                   "prior": {l: boost for l in main_langs if boost},
                   "held_out_accuracy": round(acc, 4),
                   "eval_accuracy": results}, f, indent=2)
    print(f"saved {EXPORT / 'lid.pt'}")


if __name__ == "__main__":
    main()
