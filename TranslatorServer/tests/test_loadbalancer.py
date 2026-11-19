"""Load balancer tests with fake API instances (httpx.MockTransport): no models, no network."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loadbalancer import create_app  # noqa: E402

URLS = ["http://a:1", "http://b:2", "http://c:3"]


class FakeCluster:
    """Fake instances: `down` ones refuse connections, `broken` ones fail health checks."""

    def __init__(self):
        self.down: set[str] = set()
        self.hits: Counter[str] = Counter()
        self.seen_headers: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        base = f"http://{request.url.host}:{request.url.port}"
        if base in self.down:
            raise httpx.ConnectError("connection refused", request=request)
        name = request.url.host
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "instance": f"api-{name}"})
        self.hits[name] += 1
        self.seen_headers.append(dict(request.headers))
        if request.url.path == "/translate":
            body = json.loads(request.content)
            return httpx.Response(200, json={"translation": body["text"], "instance": name})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def cluster():
    return FakeCluster()


def client_for(cluster: FakeCluster) -> TestClient:
    app = create_app(URLS, health_interval=3600, transport=httpx.MockTransport(cluster.handler))
    return TestClient(app)


def test_spreads_requests_over_all_instances(cluster):
    with client_for(cluster) as c:
        for _ in range(30):
            r = c.post("/translate", json={"text": [["hi"]], "target_lang": "fr"})
            assert r.status_code == 200
    assert set(cluster.hits) == {"a", "b", "c"}
    assert all(n == 10 for n in cluster.hits.values()), cluster.hits


def test_fails_over_when_an_instance_dies(cluster):
    with client_for(cluster) as c:
        cluster.down.add("http://b:2")  # dies after the health check marked it healthy
        for _ in range(9):
            r = c.post("/translate", json={"text": [["hi"]], "target_lang": "fr"})
            assert r.status_code == 200
        status = c.get("/lb/status").json()
    assert "b" not in cluster.hits
    b = next(x for x in status["backends"] if x["url"] == "http://b:2")
    assert b["healthy"] is False and b["errors"] == 1
    assert status["healthy"] == 2


def test_503_when_no_instance_is_up(cluster):
    cluster.down.update(URLS)
    with client_for(cluster) as c:
        r = c.post("/translate", json={"text": [["hi"]], "target_lang": "fr"})
    assert r.status_code == 503
    assert "x-request-id" in r.headers


def test_request_id_is_generated_forwarded_and_returned(cluster):
    with client_for(cluster) as c:
        r1 = c.post("/translate", json={"text": [["a"]], "target_lang": "fr"})
        r2 = c.post("/translate", json={"text": [["a"]], "target_lang": "fr"},
                    headers={"X-Request-ID": "my-id-123"})
    assert r1.headers["x-request-id"] == cluster.seen_headers[0]["x-request-id"]
    assert r2.headers["x-request-id"] == "my-id-123" == cluster.seen_headers[1]["x-request-id"]
    assert r1.headers["x-backend"] in URLS


def test_errors_from_instance_are_passed_through(cluster):
    with client_for(cluster) as c:
        r = c.get("/nothing-here")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_status_endpoint(cluster):
    with client_for(cluster) as c:
        c.post("/translate", json={"text": [["a"]], "target_lang": "fr"})
        s = c.get("/lb/status").json()
    assert s["total"] == 3 and s["healthy"] == 3
    assert sum(b["requests"] for b in s["backends"]) == 1
    assert {b["instance"] for b in s["backends"]} == {"api-a", "api-b", "api-c"}
