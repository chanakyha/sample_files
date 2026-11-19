"""Load balancer for the Translator API: one public port in front of several API instances.

  client (desktop app, scripts)
        |
        v
  loadbalancer.py :8000  --least busy healthy instance-->  app.py :8001 / :8002 / :8003 ...

* Least-connections: each request goes to the healthy instance with the fewest requests in
  progress (round-robin between equals). Translation is CPU-bound, so this spreads work evenly.
* Health checks: GET /health on every instance every --health-interval seconds; instances
  that fail are taken out of rotation and put back when they answer again.
* Failover: if an instance cannot be reached, the request is retried on another one
  (translation requests have no side effects, so retrying is safe).
* Request ids: every request gets an X-Request-ID (kept if the client sent one) that is passed
  to the instance and returned to the client, so it can be found in both logs.
* GET /lb/status shows the instances, their health and request counts.

Logs: logs/loadbalancer.log (JSON lines, see logsetup.py).

Usually started by cluster.py. Standalone:
    python loadbalancer.py --port 8000 --backends http://127.0.0.1:8001,http://127.0.0.1:8002
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from logsetup import setup_logging

logger = logging.getLogger("loadbalancer")
access_logger = logging.getLogger("loadbalancer.access")

# Headers that belong to one connection and must not be forwarded
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
              "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


class Backend:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.healthy = False
        self.active = 0  # requests in progress
        self.requests = 0
        self.errors = 0
        self.total_ms = 0.0
        self.last_error = ""
        self.instance = ""

    def status(self) -> dict:
        return {
            "url": self.url, "instance": self.instance, "healthy": self.healthy,
            "active": self.active, "requests": self.requests, "errors": self.errors,
            "avg_ms": round(self.total_ms / self.requests, 1) if self.requests else None,
            "last_error": self.last_error,
        }


class Pool:
    def __init__(self, urls: list[str]):
        self.backends = [Backend(u) for u in urls]
        self._rr = itertools.count()

    def pick(self, exclude: list[Backend]) -> Backend | None:
        candidates = [b for b in self.backends if b.healthy and b not in exclude]
        if not candidates:
            return None
        least = min(b.active for b in candidates)
        tied = [b for b in candidates if b.active == least]
        return tied[next(self._rr) % len(tied)]


def create_app(backend_urls: list[str], health_interval: float = 2.0,
               transport: httpx.AsyncBaseTransport | None = None,
               request_timeout: float = 600.0) -> Starlette:
    """`transport` lets tests replace the network with fake instances."""
    pool = Pool(backend_urls)
    state: dict = {}

    async def check(backend: Backend) -> None:
        try:
            r = await state["client"].get(backend.url + "/health", timeout=3.0)
            ok = r.status_code == 200
            if ok:
                backend.instance = r.json().get("instance", "")
            error = "" if ok else f"health returned {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            ok, error = False, f"{type(e).__name__}: {e}"
        if ok != backend.healthy:
            if ok:
                logger.info("backend %s is UP (%s)", backend.url, backend.instance,
                            extra={"fields": {"backend": backend.url, "event": "backend_up"}})
            else:
                logger.warning("backend %s is DOWN: %s", backend.url, error,
                               extra={"fields": {"backend": backend.url, "event": "backend_down",
                                                 "error": error}})
        backend.healthy = ok
        if error:
            backend.last_error = error

    async def health_loop() -> None:
        while True:
            await asyncio.sleep(health_interval)
            await asyncio.gather(*(check(b) for b in pool.backends))

    @asynccontextmanager
    async def lifespan(_app):
        state["client"] = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(request_timeout, connect=2.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        await asyncio.gather(*(check(b) for b in pool.backends))
        healthy = sum(b.healthy for b in pool.backends)
        logger.info("load balancer started with %d backend(s), %d healthy", len(pool.backends), healthy,
                    extra={"fields": {"backends": [b.url for b in pool.backends]}})
        task = asyncio.create_task(health_loop())
        yield
        task.cancel()
        await state["client"].aclose()
        logger.info("load balancer stopped")

    async def lb_status(_request: Request) -> JSONResponse:
        return JSONResponse({
            "backends": [b.status() for b in pool.backends],
            "healthy": sum(b.healthy for b in pool.backends),
            "total": len(pool.backends),
        })

    async def proxy(request: Request) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        client_ip = request.client.host if request.client else ""
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["x-request-id"] = request_id
        headers["x-forwarded-for"] = request.headers.get("x-forwarded-for") or client_ip
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        start = time.perf_counter()
        tried: list[Backend] = []
        quiet = request.url.path == "/health"

        while (backend := pool.pick(tried)) is not None:
            tried.append(backend)
            backend.active += 1
            t0 = time.perf_counter()
            try:
                r = await state["client"].request(request.method, backend.url + target,
                                                  content=body, headers=headers)
            except httpx.TransportError as e:  # instance died / unreachable / timed out
                backend.healthy = False
                backend.errors += 1
                backend.last_error = f"{type(e).__name__}: {e}"
                logger.warning("backend %s failed (%s), retrying on another instance", backend.url,
                               backend.last_error,
                               extra={"fields": {"request_id": request_id, "backend": backend.url,
                                                 "event": "retry"}})
                continue
            finally:
                backend.active -= 1
            ms = (time.perf_counter() - t0) * 1000
            backend.requests += 1
            backend.total_ms += ms
            if r.status_code >= 500:
                backend.errors += 1
            fields = {"request_id": request_id, "method": request.method, "path": request.url.path,
                      "status": r.status_code, "backend": backend.url, "instance": backend.instance,
                      "attempts": len(tried), "bytes_in": len(body), "bytes_out": len(r.content),
                      "backend_ms": round(ms, 1),
                      "duration_ms": round((time.perf_counter() - start) * 1000, 1), "client": client_ip}
            level = logging.DEBUG if quiet else (logging.WARNING if r.status_code >= 400 else logging.INFO)
            access_logger.log(level, "%s %s %d -> %s %.0fms", request.method, request.url.path,
                              r.status_code, backend.instance or backend.url, fields["duration_ms"],
                              extra={"fields": fields})
            out_headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP_BY_HOP
                           and k.lower() != "content-encoding"}
            out_headers["x-request-id"] = request_id
            out_headers["x-backend"] = backend.url
            return Response(r.content, status_code=r.status_code, headers=out_headers)

        access_logger.error("%s %s 503 no healthy backend", request.method, request.url.path,
                            extra={"fields": {"request_id": request_id, "path": request.url.path,
                                              "status": 503, "attempts": len(tried), "client": client_ip}})
        return JSONResponse({"detail": "No healthy translation instance available"}, status_code=503,
                            headers={"x-request-id": request_id})

    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
    return Starlette(routes=[
        Route("/lb/status", lb_status, methods=["GET"]),
        Route("/{path:path}", proxy, methods=methods),
    ], lifespan=lifespan)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.getenv("TRANSLATOR_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--backends", required=True, help="comma-separated instance URLs")
    p.add_argument("--health-interval", type=float, default=2.0)
    args = p.parse_args(argv)
    setup_logging("loadbalancer")
    app = create_app([u for u in args.backends.split(",") if u], args.health_interval)
    uvicorn.run(app, host=args.host, port=args.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
