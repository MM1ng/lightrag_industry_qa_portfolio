"""Responses produced inside the auth middleware must reach the status counters.

``_record_response_status`` exists because 401 (fail-closed) and 429 (anonymous
rate limit) short-circuit before ``collect_operational_metrics`` runs. Without
it ``http_status_401_total`` and ``http_status_429_total`` stay at zero while the
caller clearly received those codes — the 2026-10-23 audit flagged the 429 half
as D8.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from industrial_rag.api import create_app
from industrial_rag.config import Settings
from industrial_rag.operational_metrics import operational_metrics

SERVICE_KEY = "service-" + "k" * 32
ADMIN_KEY = "admin-" + "k" * 32


class _NoRuntime:
    """Runtime stub: the auth boundary decides before the handler is reached."""

    async def query(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("rate limit must reject before runtime use")

    def close(self) -> None:
        return None


def _counters() -> dict[str, int]:
    return dict(operational_metrics.snapshot()["counters"])


def _delta(before: dict[str, int], after: dict[str, int], name: str) -> int:
    return after.get(name, 0) - before.get(name, 0)


def _app(*, limit: int, keys: bool) -> object:
    settings = Settings(
        api_key="offline-test-key",
        service_api_key=SERVICE_KEY if keys else None,
        admin_api_key=ADMIN_KEY if keys else None,
        deployment_environment="local_dev",
        public_rate_limit_per_minute=limit,
        feedback_rate_limit_per_minute=1,
    )
    return create_app(settings=settings, runtime_factory=lambda _: _NoRuntime())


def test_rate_limited_response_increments_status_counter() -> None:
    app = _app(limit=2, keys=True)
    before = _counters()

    with TestClient(app, raise_server_exceptions=False) as client:
        codes = [
            client.post(
                "/v1/knowledge-bases/kb-1/query",
                json={"query": "泵的维护步骤", "history": []},
            ).status_code
            for _ in range(3)
        ]

    after = _counters()
    assert codes[-1] == 429
    assert _delta(before, after, "http_status_429_total") == 1
    assert _delta(before, after, "rate_limited_total") == 1


def test_fail_closed_401_increments_status_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IRA_DEV_INSECURE", raising=False)
    monkeypatch.setenv("IRA_DEPLOYMENT_ENVIRONMENT", "local_dev")
    app = _app(limit=30, keys=False)
    before = _counters()

    with TestClient(app, raise_server_exceptions=False) as client:
        status = client.get("/v1/knowledge-bases/kb-1/documents").status_code

    after = _counters()
    assert status == 401
    assert _delta(before, after, "http_status_401_total") == 1


def test_successful_requests_are_counted_once() -> None:
    """The middleware path must not double count now that both paths increment."""

    app = _app(limit=30, keys=True)
    before = _counters()

    with TestClient(app, raise_server_exceptions=False) as client:
        status = client.get("/version").status_code

    after = _counters()
    assert status == 200
    assert _delta(before, after, "http_request_total") == 1
    assert _delta(before, after, "http_status_200_total") == 1
