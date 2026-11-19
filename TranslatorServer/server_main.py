"""Entry point of the bundled TranslatorServer.exe (built by build_server_exe.cmd with PyInstaller).

    TranslatorServer.exe                         cluster: 2 instances + load balancer on port 8000
    TranslatorServer.exe --instances 4           cluster with 4 instances
    TranslatorServer.exe --instances 4 --host 0.0.0.0 --port 8000
    TranslatorServer.exe --single [--port 8000] [--host 127.0.0.1]   one instance, no load balancer
    TranslatorServer.exe --help

Internal modes, started by the cluster supervisor (cluster.py) for its child processes:

    TranslatorServer.exe worker --host 127.0.0.1 --port 8001 [--no-access-log]
    TranslatorServer.exe balancer --host 127.0.0.1 --port 8000 --backends http://...,http://...

The models are read from models\\ and the logs written to logs\\, both next to the exe
(TRANSLATOR_MODEL_DIR / TRANSLATOR_LOG_DIR override them). The same file also runs from source:
python server_main.py --instances 4.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys


def run_worker(argv: list[str], default_instance: str | None = None) -> None:
    """One API instance (app.py) served by uvicorn."""
    p = argparse.ArgumentParser(prog="TranslatorServer.exe worker")
    p.add_argument("--host", default=os.getenv("TRANSLATOR_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-access-log", action="store_true")
    args = p.parse_args(argv)
    if default_instance:
        os.environ.setdefault("TRANSLATOR_INSTANCE", default_instance)

    import uvicorn

    from app import app  # loads the logging setup of this instance

    uvicorn.run(app, host=args.host, port=args.port, access_log=not args.no_access_log)


def usage() -> None:
    print(__doc__.split("Internal modes")[0].rstrip())
    print("\nCluster options:")
    import cluster

    cluster.main(["--help"])


def main() -> None:
    multiprocessing.freeze_support()
    argv = sys.argv[1:]
    mode = argv[0] if argv else ""

    if mode == "worker":
        run_worker(argv[1:])
    elif mode == "balancer":
        import loadbalancer

        loadbalancer.main(argv[1:])
    elif mode in ("-h", "--help", "/?"):
        try:
            usage()
        except SystemExit:
            pass
    elif "--single" in argv:
        rest = [a for a in argv if a != "--single"]
        host = os.getenv("TRANSLATOR_HOST", "127.0.0.1")
        if "--host" not in rest:
            rest = ["--host", host] + rest
        port = rest[rest.index("--port") + 1] if "--port" in rest else "8000"
        shown = rest[rest.index("--host") + 1]
        print(f"Translator API on http://{shown}:{port}  (docs: /docs)  - press Ctrl+C to stop", flush=True)
        run_worker(rest, default_instance="api")
    else:
        import cluster

        print("Translator server: starting the cluster - press Ctrl+C to stop", flush=True)
        cluster.main(argv)


if __name__ == "__main__":
    main()
