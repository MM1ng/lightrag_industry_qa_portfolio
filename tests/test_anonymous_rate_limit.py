"""Anonymous-surface rate limiting.

The uncredentialed browser surface is the only place the limiter may act; every
Bearer or ``local_dev`` actor must be untouched, which is what keeps the ~1290
existing tests (all running as ``local_dev``) behaving exactly as before.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from industrial_rag.api import _anonymous_client_key, create_app
from industrial_rag.config import Settings
from industrial_rag.rate_limit import AnonymousRateLimiter
from starlette.requests import Request

_PUBLIC_PATH = "/v1/knowledge-bases/kb-1/graph/overview"
_SERVICE_KEY = "s" * 32
_ADMIN_KEY = "a" * 32


class _Runtime:
    def query(self, question, *, mode, timeout):
        raise AssertionError("rate-limit tests must not reach the runtime")

    def close(self):
        pass


def _app(
    *,
    public_limit: int = 3,
    feedback_limit: int = 1,
    trust_proxy_headers: bool = False,
    with_credentials: bool = True,
) -> object:
    settings = Settings(
        api_key="offline-test-key",
        deployment_environment="local_dev",
        service_api_key=_SERVICE_KEY if with_credentials else None,
        admin_api_key=_ADMIN_KEY if with_credentials else None,
        public_rate_limit_per_minute=public_limit,
        feedback_rate_limit_per_minute=feedback_limit,
        trust_proxy_headers=trust_proxy_headers,
    )
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())
    # No lifespan: these tests only exercise the middleware boundary.
    app.state.service_api_key = settings.service_api_key
    app.state.admin_api_key = settings.admin_api_key
    app.state.resolved_settings = settings
    app.state.runtime = None
    return app


class _FakeClock:
    """Deterministic monotonic clock so window expiry needs no ``sleep``."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_anonymous_query_returns_429_after_budget() -> None:
    app = _app(public_limit=2, feedback_limit=1)
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.get(_PUBLIC_PATH)
        second = client.get(_PUBLIC_PATH)
        blocked = client.get(_PUBLIC_PATH)

    assert first.status_code != 429
    assert second.status_code != 429
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["retryable"] is True
    assert int(blocked.headers["Retry-After"]) >= 1


def test_bearer_requests_are_not_limited() -> None:
    app = _app(public_limit=1, feedback_limit=1)
    headers = {"Authorization": f"Bearer {_SERVICE_KEY}"}
    with TestClient(app, raise_server_exceptions=False) as client:
        # A management path, i.e. not part of the anonymous allowlist.
        statuses = [
            client.get("/v1/knowledge-bases/kb-1/documents", headers=headers).status_code
            for _ in range(5)
        ]

    assert 429 not in statuses


def test_local_dev_insecure_actor_is_not_limited() -> None:
    # No credentials + IRA_DEV_INSECURE=1 (set by conftest) => local_dev actor,
    # which returns before the anonymous branch. This is why the ~1290 existing
    # tests keep their behaviour.
    app = _app(public_limit=1, feedback_limit=1, with_credentials=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        statuses = [client.get(_PUBLIC_PATH).status_code for _ in range(5)]

    assert 429 not in statuses


def test_limit_disabled_when_config_is_zero() -> None:
    app = _app(public_limit=0, feedback_limit=0)
    with TestClient(app, raise_server_exceptions=False) as client:
        statuses = [client.get(_PUBLIC_PATH).status_code for _ in range(10)]

    assert 429 not in statuses


def test_window_expiry_restores_access() -> None:
    app = _app(public_limit=2, feedback_limit=1)
    with TestClient(app, raise_server_exceptions=False) as client:
        # Installed after startup: the lifespan builds a fresh limiter.
        clock = _FakeClock()
        app.state.anonymous_rate_limiter = AnonymousRateLimiter(
            default_limit=2,
            feedback_limit=1,
            window_seconds=60.0,
            clock=clock,
        )

        first = client.get(_PUBLIC_PATH).status_code
        second = client.get(_PUBLIC_PATH).status_code
        blocked = client.get(_PUBLIC_PATH).status_code

        clock.advance(61.0)
        after_window = client.get(_PUBLIC_PATH).status_code

    assert 429 not in (first, second)
    assert blocked == 429
    assert after_window != 429


def test_distinct_client_ips_have_independent_budgets() -> None:
    app = _app(public_limit=1, feedback_limit=1, trust_proxy_headers=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        first_a = client.get(_PUBLIC_PATH, headers={"X-Forwarded-For": "203.0.113.7"}).status_code
        first_b = client.get(_PUBLIC_PATH, headers={"X-Forwarded-For": "203.0.113.8"}).status_code
        second_a = client.get(_PUBLIC_PATH, headers={"X-Forwarded-For": "203.0.113.7"}).status_code

    assert 429 not in (first_a, first_b)
    assert second_a == 429


def test_feedback_has_tighter_budget_than_query() -> None:
    app = _app(public_limit=5, feedback_limit=1)
    payload = {"request_id": "req-1", "feedback_type": "helpful"}
    with TestClient(app, raise_server_exceptions=False) as client:
        first_feedback = client.post("/v1/feedback", json=payload).status_code
        second_feedback = client.post("/v1/feedback", json=payload).status_code
        # The shared bucket still has budget: feedback did not consume it.
        query = client.get(_PUBLIC_PATH).status_code

    assert first_feedback != 429
    assert second_feedback == 429
    assert query != 429


def test_missing_peer_address_shares_one_bucket() -> None:
    """Without a peer address the limiter over-restricts instead of failing open.

    uvicorn always reports a peer, so this only happens under unusual ASGI
    servers; sharing one bucket is the safe direction for an abuse brake.
    """

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/knowledge-bases/kb-1/graph/overview",
            "headers": [(b"x-forwarded-for", b"203.0.113.7")],
            "client": None,
        }
    )

    assert _anonymous_client_key(request, trust_proxy_headers=False) == "unknown"
    # Even a forged header must not buy a private bucket while trust is off.
    assert _anonymous_client_key(request, trust_proxy_headers=True) == "203.0.113.7"
