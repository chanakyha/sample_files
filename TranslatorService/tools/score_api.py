"""Translation score measured end to end through the running API (e.g. the load balancer),
the way clients use it: no source_lang, so automatic language detection is part of the test.

Sends the held-out test set (training/data/prepared/test.tsv) in requests of --batch
sentences, several in parallel, and reports BLEU / chrF per corpus and direction plus how
often the source language was detected correctly.

Usage:  python tools/score_api.py --url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import defaultdict
from pathlib import Path

import httpx
import sacrebleu

SERVICE = Path(__file__).resolve().parent.parent
TEST = SERVICE / "training" / "data" / "prepared" / "test.tsv"


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args()

    rows = [line.rstrip("\n").split("\t") for line in open(TEST, encoding="utf-8")]
    by_target: dict[str, list[int]] = defaultdict(list)
    for i, (_s, t, *_rest) in enumerate(rows):
        by_target[t].append(i)
    jobs = [(t, idx[k:k + args.batch]) for t, idx in by_target.items() for k in range(0, len(idx), args.batch)]

    hyps: list[str | None] = [None] * len(rows)
    detected: list[str | None] = [None] * len(rows)
    sem = asyncio.Semaphore(args.concurrency)
    instances = defaultdict(int)
    start = time.perf_counter()
    async with httpx.AsyncClient(base_url=args.url, timeout=900) as client:
        async def run(target: str, idx: list[int]) -> None:
            async with sem:
                r = await client.post("/translate", json={"text": [[rows[i][2] for i in idx]],
                                                          "target_lang": target})
            r.raise_for_status()
            instances[r.headers.get("x-instance", "?")] += 1
            body = r.json()
            for i, h, d in zip(idx, body["translation"][0], body["detected_lang"][0]):
                hyps[i], detected[i] = h, d

        await asyncio.gather(*(run(t, idx) for t, idx in jobs))
    elapsed = time.perf_counter() - start

    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, (s, t, _x, _y, corpus) in enumerate(rows):
        groups[(corpus, f"{s}->{t}")].append(i)

    def score(idx: list[int]) -> tuple[float, float]:
        h = [hyps[i] for i in idx]
        r = [[rows[i][3] for i in idx]]
        return sacrebleu.corpus_bleu(h, r).score, sacrebleu.corpus_chrf(h, r).score

    print(f"{len(rows)} sentences in {len(jobs)} requests through {args.url} in {elapsed:.0f}s "
          f"({len(rows) / elapsed:.0f} sentences/s), instances used: {dict(instances)}\n")
    print(f"{'corpus':15} {'direction':9} {'n':>5} {'BLEU':>6} {'chrF':>6} {'detected ok':>12}")
    corpora = sorted({c for c, _ in groups})
    for corpus in corpora:
        for (c, d), idx in sorted(groups.items()):
            if c != corpus:
                continue
            b, f = score(idx)
            ok = sum(detected[i] == rows[i][0] for i in idx) / len(idx)
            print(f"{corpus:15} {d:9} {len(idx):>5} {b:6.1f} {f:6.1f} {ok:>11.1%}")
    print()
    for corpus in corpora:
        idx = [i for (c, _), v in groups.items() if c == corpus for i in v]
        b, f = score(idx)
        ok = sum(detected[i] == rows[i][0] for i in idx) / len(idx)
        print(f"{corpus:15} BLEU {b:5.1f}  chrF {f:5.1f}  detection {ok:.1%}  ({len(idx)} sentences)")
    b, f = score(list(range(len(rows))))
    ok = sum(detected[i] == rows[i][0] for i in range(len(rows))) / len(rows)
    print(f"{'all':15} BLEU {b:5.1f}  chrF {f:5.1f}  detection {ok:.1%}  ({len(rows)} sentences)")


if __name__ == "__main__":
    asyncio.run(main())
