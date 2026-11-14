"""Logging for the API instances, the load balancer and the cluster launcher.

Every process writes
  * readable lines to the console, and
  * one JSON object per line to logs/<name>.log (rotating: 10 MB x 5 files), e.g.
    {"ts": "2026-11-14T20:31:05.123+05:30", "level": "INFO", "logger": "translator.access",
     "instance": "api-8001", "msg": "POST /translate 200 41ms", "request_id": "3f9c...", ...}

Extra fields are passed with logger.info(msg, extra={"fields": {...}}). The request_id is set
by the load balancer (X-Request-ID header) and logged by the API instance too, so one request
can be followed through both logs.

Settings: TRANSLATOR_LOG_DIR (default ./logs), TRANSLATOR_LOG_LEVEL (default INFO).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
_configured: set[str] = set()


class JsonFormatter(logging.Formatter):
    def __init__(self, instance: str):
        super().__init__()
        self.instance = instance

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "instance": self.instance,
            "pid": record.process,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def log_dir() -> Path:
    path = Path(os.getenv("TRANSLATOR_LOG_DIR", str(SERVICE_DIR / "logs")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(name: str) -> Path:
    """Configure the root logger (and uvicorn's loggers) for this process. Returns the log file."""
    file = log_dir() / f"{name}.log"
    if name in _configured:
        return file
    _configured.add(name)
    level = getattr(logging, os.getenv("TRANSLATOR_LOG_LEVEL", "INFO").upper(), logging.INFO)

    file_handler = logging.handlers.RotatingFileHandler(
        file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(JsonFormatter(name))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(f"%(asctime)s %(levelname)-7s [{name}] %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(console)
    # uvicorn's own loggers (startup, errors) do not propagate to the root logger
    for logger_name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(logger_name)
        lg.handlers = [file_handler, console]
        lg.propagate = False
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our access log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return file
