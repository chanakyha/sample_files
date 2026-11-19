"""Run the Translator API scaled out: N API instances behind the load balancer.

    python cluster.py                      # 2 instances on 8001-8002, load balancer on 8000
    python cluster.py --instances 4 --port 8000 --host 0.0.0.0
    TranslatorServer.exe --instances 4     (the bundled build runs this same code)

* Each instance is a separate process (own model copy, own CPU threads), so translations run
  in parallel. CPU threads are split between the instances (--threads to override).
* The instances listen on 127.0.0.1 only; clients use the load balancer port.
* A supervisor restarts an instance that crashes (and logs it). Ctrl+C stops everything.

Logs (JSON lines, rotating) in logs/: cluster.log, loadbalancer.log, api-8001.log, ...
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from logsetup import setup_logging

FROZEN = getattr(sys, "frozen", False)  # running as the bundled TranslatorServer.exe
SERVICE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
logger = logging.getLogger("cluster")


def worker_cmd(port: int) -> list[str]:
    """Command that starts one API instance."""
    args = ["--host", "127.0.0.1", "--port", str(port), "--no-access-log"]
    if FROZEN:  # TranslatorServer.exe worker ... (see server_main.py)
        return [sys.executable, "worker", *args]
    return [sys.executable, "-m", "uvicorn", "app:app", *args]


def balancer_cmd(host: str, port: int, urls: list[str]) -> list[str]:
    """Command that starts the load balancer."""
    args = ["--host", host, "--port", str(port), "--backends", ",".join(urls)]
    if FROZEN:
        return [sys.executable, "balancer", *args]
    return [sys.executable, "loadbalancer.py", *args]


class Child:
    def __init__(self, name: str, cmd: list[str], env: dict[str, str]):
        self.name, self.cmd, self.env = name, cmd, env
        self.proc: subprocess.Popen | None = None
        self.restarts = 0

    def start(self) -> None:
        self.proc = subprocess.Popen(self.cmd, cwd=SERVICE, env=self.env)
        logger.info("started %s (pid %d)", self.name, self.proc.pid,
                    extra={"fields": {"process": self.name, "pid": self.proc.pid, "event": "start"}})

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def main(argv: list[str] | None = None) -> None:
    cpus = os.cpu_count() or 4
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instances", type=int, default=int(os.getenv("TRANSLATOR_INSTANCES", "2")))
    p.add_argument("--host", default=os.getenv("TRANSLATOR_HOST", "127.0.0.1"),
                   help="load balancer address (0.0.0.0 = reachable from other machines)")
    p.add_argument("--port", type=int, default=8000, help="load balancer port")
    p.add_argument("--base-port", type=int, default=8001, help="first instance port")
    p.add_argument("--threads", type=int, default=0, help="CPU threads per instance (default: split cores)")
    args = p.parse_args(argv)
    setup_logging("cluster")

    threads = args.threads or max(1, cpus // args.instances)
    children: list[Child] = []
    urls = []
    for i in range(args.instances):
        port = args.base_port + i
        env = dict(os.environ, TRANSLATOR_INSTANCE=f"api-{port}", TRANSLATOR_THREADS=str(threads),
                   PYTHONIOENCODING="utf-8")
        children.append(Child(f"api-{port}", worker_cmd(port), env))
        urls.append(f"http://127.0.0.1:{port}")
    balancer = Child("loadbalancer", balancer_cmd(args.host, args.port, urls),
                     dict(os.environ, PYTHONIOENCODING="utf-8"))
    logger.info("cluster: %d instance(s) x %d threads, load balancer on http://%s:%d",
                args.instances, threads, args.host, args.port,
                extra={"fields": {"instances": args.instances, "threads": threads, "port": args.port}})

    for c in children:
        c.start()
    balancer.start()

    stopping = False

    def on_signal(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_signal)

    try:
        while not stopping:
            time.sleep(1)
            for c in children + [balancer]:
                code = c.proc.poll() if c.proc else None
                if code is not None and not stopping:
                    c.restarts += 1
                    logger.error("%s exited with code %s, restarting (restart #%d)", c.name, code, c.restarts,
                                 extra={"fields": {"process": c.name, "exit_code": code,
                                                   "restarts": c.restarts, "event": "crash"}})
                    time.sleep(1)
                    c.start()
    finally:
        logger.info("stopping cluster")
        for c in [balancer] + children:
            c.stop()
        logger.info("cluster stopped")


if __name__ == "__main__":
    main()
