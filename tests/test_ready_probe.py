"""Readiness probe contract: async handler, bounded probes, no ``asyncio.run``.

The previous implementation was a synchronous handler that called
``asyncio.run`` twice, once per request, and created a fresh
``AsyncQdrantClient`` on every probe.  These tests pin the replacement down.
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest
from fastapi.testclient import TestClient
from industrial_rag import api as api_module
from industrial_rag.api import create_app
from industrial_rag.config import Settings


class _Runtime:
    def query(self, question, *, mode, timeout):
        raise AssertionError("health probes must not call the runtime")

    def close(self):
        pass


def _ready_endpoint():
    app = create_app(settings=Settings(api_key="offline-test-key"))
    for route in app.routes:
        if getattr(route, "path", None) == "/ready":
            return route.endpoint
    raise AssertionError("/ready route is not registered")


def _client() -> TestClient:
    app = create_app(
        settings=Settings(api_key="offline-test-key"),
        runtime_factory=lambda _: _Runtime(),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_ready_endpoint_is_async() -> None:
    endpoint = _ready_endpoint()
    assert inspect.iscoroutinefunction(endpoint) is True
    # A sync handler would be dispatched to Starlette's worker thread pool,
    # which is exactly what the old ``asyncio.run`` implementation relied on.
    assert "async def ready" in inspect.getsource(endpoint)


def test_ready_source_has_no_asyncio_run() -> None:
    source = inspect.getsource(_ready_endpoint())
    assert "asyncio.run" not in source


def test_ready_reports_db_ok_under_lifespan() -> None:
    with _client() as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"]["db"] == "ok"
    assert body["components"]["config"] == "ok"


def test_ready_reports_503_when_db_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failing_probe() -> bool:
        return False

    monkeypatch.setattr(api_module, "_db_reachable", _failing_probe)
    with _client() as client:
        response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["components"]["db"] != "ok"


def test_ready_db_probe_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _hanging_probe() -> bool:
        await asyncio.sleep(10)
        return True

    monkeypatch.setattr(api_module, "_db_reachable", _hanging_probe)
    with _client() as client:
        started = time.monotonic()
        response = client.get("/ready")
        elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"readiness probe blocked for {elapsed:.2f}s"
    assert response.status_code == 503
    assert response.json()["components"]["db"] != "ok"
