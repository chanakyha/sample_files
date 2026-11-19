"""Load test for the Translator API (single instance or the load-balanced cluster).

Every request uses new, randomly generated sentences, so the translation cache never helps
and every sentence is really translated.

Scenarios (pick with --scenario, default: all except soak and chaos):
  ramp     concurrency 1, 4, 8, 16, 32, 64: throughput and latency at each level
  spike    --spike requests sent at the same moment
  large    single requests with many sentences (--large 1000,2000)
  mixed    translate + health + languages + invalid requests together; checks every status code
  soak     --soak-seconds of constant load (watch the instances' memory meanwhile)
  chaos    constant load for --chaos-seconds while API instances are killed every
           --kill-every seconds (cluster.py restarts them); counts failed requests

Usage:
  python tools/loadtest.py --url http://127.0.0.1:8000
  python tools/loadtest.py --scenario soak --soak-seconds 300
  python tools/loadtest.py --scenario chaos --chaos-seconds 120
Results are printed and appended as JSON to logs/loadtest.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

SERVICE = Path(__file__).resolve().parent.parent
RESULTS = SERVICE / "logs" / "loadtest.jsonl"

SUBJECTS = {
    "en": ["The car", "My vehicle", "The van", "Our customer", "The mechanic", "The engine"],
    "es": ["El coche", "Mi vehículo", "La furgoneta", "Nuestro cliente", "El mecánico", "El motor"],
    "fr": ["La voiture", "Mon véhicule", "La camionnette", "Notre client", "Le mécanicien", "Le moteur"],
}
PREDICATES = {
    "en": ["needs a new battery", "will be ready tomorrow morning", "makes a strange noise",
           "has worn brakes", "does not start in the cold", "is waiting at the reception"],
    "es": ["necesita una batería nueva", "estará listo mañana por la mañana", "hace un ruido extraño",
           "tiene los frenos desgastados", "no arranca con el frío", "está esperando en la recepción"],
    "fr": ["a besoin d'une nouvelle batterie", "sera prêt demain matin", "fait un bruit étrange",
           "a des freins usés", "ne démarre pas par temps froid", "attend à l'accueil"],
}


def sentence(rng: random.Random) -> tuple[str, str]:
    lang = rng.choice(["en", "es", "fr"])
    number = rng.randint(1, 10**7)  # unique -> no cache hits
    joiner = {"en": "number", "es": "número", "fr": "numéro"}[lang]
    return lang, f"{rng.choice(SUBJECTS[lang])} {joiner} {number} {rng.choice(PREDICATES[lang])}."


def payload(rng: random.Random, n: int) -> dict:
    target = rng.choice(["en", "es", "fr"])
    return {"text": [[sentence(rng)[1] for _ in range(n)]], "target_lang": target}


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


class Stats:
    def __init__(self, name: str):
        self.name = name
        self.latencies: list[float] = []
        self.statuses: Counter = Counter()
        self.instances: Counter = Counter()
        self.sentences = 0
        self.start = time.perf_counter()
        self.end = self.start

    def add(self, status, latency: float, sentences: int = 0, instance: str = "") -> None:
        self.statuses[status] += 1
        self.latencies.append(latency)
        if status == 200:
            self.sentences += sentences
        if instance:
            self.instances[instance] += 1

    def summary(self, **extra) -> dict:
        self.end = time.perf_counter()
        elapsed = self.end - self.start
        ok = self.statuses.get(200, 0)
        total = sum(self.statuses.values())
        s = {
            "scenario": self.name, "requests": total, "ok": ok,
            "errors": total - ok - sum(v for k, v in self.statuses.items() if k in (413, 422)),
            "statuses": {str(k): v for k, v in self.statuses.items()},
            "seconds": round(elapsed, 1),
            "req_per_s": round(total / elapsed, 2) if elapsed else 0,
            "sentences_per_s": round(self.sentences / elapsed, 1) if elapsed else 0,
            "p50": round(pct(self.latencies, 50), 2), "p95": round(pct(self.latencies, 95), 2),
            "p99": round(pct(self.latencies, 99), 2), "max": round(max(self.latencies, default=0), 2),
            "instances": dict(self.instances),
        }
        s.update(extra)
        return s


async def one(client: httpx.AsyncClient, stats: Stats, method: str, path: str, body=None,
              sentences: int = 0) -> int | str:
    t0 = time.perf_counter()
    try:
        r = await client.request(method, path, json=body)
        status: int | str = r.status_code
        stats.add(status, time.perf_counter() - t0, sentences, r.headers.get("x-instance", ""))
    except httpx.HTTPError as e:
        status = type(e).__name__
        stats.add(status, time.perf_counter() - t0)
    return status


async def run_fixed(client, name: str, concurrency: int, requests: int, per_request: int, seed: int) -> dict:
    stats = Stats(name)
    rng = random.Random(seed)
    bodies = [payload(rng, per_request) for _ in range(requests)]
    sem = asyncio.Semaphore(concurrency)

    async def task(b):
        async with sem:
            await one(client, stats, "POST", "/translate", b, per_request)

    await asyncio.gather(*(task(b) for b in bodies))
    return stats.summary(concurrency=concurrency, sentences_per_request=per_request)


async def run_for(client, name: str, concurrency: int, seconds: float, per_request: int, seed: int,
                  during=None) -> dict:
    stats = Stats(name)
    rng = random.Random(seed)
    deadline = time.perf_counter() + seconds

    async def worker():
        while time.perf_counter() < deadline:
            await one(client, stats, "POST", "/translate", payload(rng, per_request), per_request)

    tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
    extra = {}
    if during:
        extra = await during(deadline)
    await asyncio.gather(*tasks)
    return stats.summary(concurrency=concurrency, sentences_per_request=per_request, **extra)


async def scenario_mixed(client, seed: int) -> dict:
    """Valid and invalid requests interleaved; every response must have the expected status."""
    rng = random.Random(seed)
    stats = Stats("mixed")
    cases = []
    for _ in range(40):
        cases.append(("POST", "/translate", payload(rng, 5), 200, 5))
    for _ in range(20):
        cases.append(("GET", "/health", None, 200, 0))
        cases.append(("GET", "/languages", None, 200, 0))
    for _ in range(10):
        cases.append(("POST", "/translate", {"text": [["Hallo"]], "target_lang": "de"}, 422, 0))
        cases.append(("POST", "/translate", {"text": ["flat list"], "target_lang": "fr"}, 422, 0))
        cases.append(("POST", "/translate", {"text": [[]], "target_lang": "fr"}, 422, 0))
        cases.append(("GET", "/does-not-exist", None, 404, 0))
    rng.shuffle(cases)
    sem = asyncio.Semaphore(16)
    wrong = []

    async def task(case):
        method, path, body, expected, n = case
        async with sem:
            got = await one(client, stats, method, path, body, n)
        if got != expected:
            wrong.append(f"{method} {path} expected {expected} got {got}")

    await asyncio.gather(*(task(c) for c in cases))
    s = stats.summary(unexpected_status=len(wrong), examples=wrong[:5])
    s["errors"] = len(wrong)
    return s


def kill_one_instance(rng: random.Random) -> str:
    """Kill a random API instance started by cluster.py (Windows)."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -match 'uvicorn app:app.*--port 80(0[1-9]|[1-9][0-9])' } | "
         "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
        capture_output=True, text=True).stdout.split("\n")
    procs = [l.strip() for l in out if l.strip() and "--port" in l]
    if not procs:
        return "none"
    victim = rng.choice(procs)
    pid = victim.split()[0]
    port = victim.split("--port")[1].split()[0]
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    return f"api-{port}"


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--scenario", default="ramp,spike,large,mixed",
                   help="comma list of: ramp, spike, large, mixed, soak, chaos")
    p.add_argument("--levels", default="1,4,8,16,32,64", help="ramp concurrency levels")
    p.add_argument("--per-request", type=int, default=10, help="sentences per request")
    p.add_argument("--spike", type=int, default=200)
    p.add_argument("--large", default="1000,2000")
    p.add_argument("--soak-seconds", type=float, default=300)
    p.add_argument("--chaos-seconds", type=float, default=120)
    p.add_argument("--kill-every", type=float, default=20)
    p.add_argument("--concurrency", type=int, default=16, help="for soak and chaos")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=200)
    async with httpx.AsyncClient(base_url=args.url, timeout=900, limits=limits) as client:
        health = (await client.get("/health")).json()
        try:
            lb = (await client.get("/lb/status")).json()
            setup = f"load balancer with {lb['healthy']}/{lb['total']} healthy instances"
        except Exception:
            setup = "single instance"
        print(f"target {args.url}: {setup}, beam {health.get('beam_size')}, model step "
              f"{(health.get('training') or {}).get('step')}\n", flush=True)

        results = []

        def report(r: dict) -> None:
            r["setup"] = setup
            results.append(r)
            print(f"{r['scenario']:<14} conc {str(r.get('concurrency', '-')):>4}  "
                  f"req {r['requests']:>5}  ok {r['ok']:>5}  err {r['errors']:>3}  "
                  f"{r['req_per_s']:>6.1f} req/s  {r['sentences_per_s']:>6.1f} sent/s  "
                  f"p50 {r['p50']:>6.2f}s  p95 {r['p95']:>6.2f}s  p99 {r['p99']:>6.2f}s  max {r['max']:>6.2f}s"
                  + (f"  {r['statuses']}" if r["errors"] else ""), flush=True)
            for k in ("instances", "kills", "unexpected_status", "examples", "note"):
                if r.get(k):
                    print(f"{'':16}{k}: {r[k]}", flush=True)

        scenarios = args.scenario.split(",")
        if "ramp" in scenarios:
            for i, c in enumerate(int(x) for x in args.levels.split(",")):
                report(await run_fixed(client, "ramp", c, max(20, c * 4), args.per_request, args.seed + i))
        if "spike" in scenarios:
            report(await run_fixed(client, "spike", args.spike, args.spike, args.per_request, args.seed + 100))
        if "large" in scenarios:
            for i, n in enumerate(int(x) for x in args.large.split(",")):
                r = await run_fixed(client, f"large {n}", 1, 1, n, args.seed + 200 + i)
                report(r)
        if "mixed" in scenarios:
            report(await scenario_mixed(client, args.seed + 300))
        if "soak" in scenarios:
            report(await run_for(client, "soak", args.concurrency, args.soak_seconds, args.per_request,
                                 args.seed + 400))
        if "chaos" in scenarios:
            rng = random.Random(args.seed)

            async def killer(deadline):
                kills = []
                while time.perf_counter() + args.kill_every < deadline:
                    await asyncio.sleep(args.kill_every)
                    kills.append(await asyncio.to_thread(kill_one_instance, rng))
                return {"kills": kills}

            report(await run_for(client, "chaos", args.concurrency, args.chaos_seconds, args.per_request,
                                 args.seed + 500, during=killer))

    RESULTS.parent.mkdir(exist_ok=True)
    with open(RESULTS, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **r}, ensure_ascii=False) + "\n")
    failed = sum(r["errors"] for r in results)
    print(f"\n{len(results)} runs, {sum(r['requests'] for r in results)} requests, {failed} errors")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
