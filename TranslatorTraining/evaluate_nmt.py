"""Step 5: score the exported translation model on the held-out test set.

Reports BLEU and chrF per direction, separately for short everyday sentences (Tatoeba), formal
parliament text (Europarl) and the hand-written car-service / greeting set (Domain,
domain_test.tsv). Test sentences were excluded from training by prepare_data.py.

Usage:  python evaluate_nmt.py [--beam 4] [--per-direction 500] [--corpus Domain]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

import sacrebleu

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "TranslatorServer"))  # model code of the API
from translator.engine import Translator  # noqa: E402

TEST = ROOT / "data" / "prepared" / "test.tsv"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--beam", type=int, default=4)
    p.add_argument("--per-direction", type=int, default=500, help="max sentences per direction+corpus")
    p.add_argument("--examples", type=int, default=3, help="example translations to print per direction")
    p.add_argument("--corpus", default="", help="only this corpus (Tatoeba, Europarl, Domain)")
    args = p.parse_args()

    groups: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    with open(TEST, encoding="utf-8") as f:
        for line in f:
            s, t, x, y, corpus = line.rstrip("\n").split("\t")
            if args.corpus and corpus != args.corpus:
                continue
            if len(groups[(s, t, corpus)]) < args.per_direction:
                groups[(s, t, corpus)].append((x, y))

    tr = Translator(beam_size=args.beam, cache_size=0)
    tr.load()
    print(f"model: {tr.info}  beam {args.beam}\n")
    print(f"{'direction':10} {'corpus':15} {'n':>5} {'BLEU':>6} {'chrF':>6}")
    total_hyp, total_ref = [], []
    by_corpus: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
    start = time.perf_counter()
    for (s, t, corpus), pairs in sorted(groups.items()):
        hyps = tr.translate_many([x for x, _ in pairs], [s] * len(pairs), t)
        refs = [y for _, y in pairs]
        bleu = sacrebleu.corpus_bleu(hyps, [refs]).score
        chrf = sacrebleu.corpus_chrf(hyps, [refs]).score
        print(f"{s}->{t:7} {corpus:15} {len(pairs):>5} {bleu:6.1f} {chrf:6.1f}")
        for (x, y), h in list(zip(pairs, hyps))[: args.examples]:
            print(f"      src: {x}\n      out: {h}\n      ref: {y}")
        total_hyp += hyps
        total_ref += refs
        by_corpus[corpus][0].extend(hyps)
        by_corpus[corpus][1].extend(refs)
    print()
    for corpus, (hyps, refs) in sorted(by_corpus.items()):
        print(f"{corpus:15} BLEU {sacrebleu.corpus_bleu(hyps, [refs]).score:.1f}  "
              f"chrF {sacrebleu.corpus_chrf(hyps, [refs]).score:.1f}  ({len(hyps)} sentences)")
    print(f"all: BLEU {sacrebleu.corpus_bleu(total_hyp, [total_ref]).score:.1f}  "
          f"chrF {sacrebleu.corpus_chrf(total_hyp, [total_ref]).score:.1f}  "
          f"({len(total_hyp)} sentences in {time.perf_counter() - start:.0f}s)")


if __name__ == "__main__":
    main()
