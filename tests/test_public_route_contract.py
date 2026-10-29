"""Contract tests for the anonymous browser route boundary.

``_is_public_browser_request`` is the single decision point that lets a request
skip Bearer authentication. A route that accidentally matches the pattern
becomes anonymously reachable, so the allowed set is pinned here explicitly.

The cases below are the current behaviour of the boundary. Each new anonymous
route must be added deliberately, with a matching entry in ``PUBLIC_CASES``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from industrial_rag.api import _is_public_browser_request, create_app
from industrial_rag.config import Settings
from starlette.requests import Request

# Routes that may be served to an unauthenticated browser.
PUBLIC_CASES: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/knowledge-bases"),
    ("GET", "/v1/knowledge-bases/kb-1/documents/doc-1/source"),
    ("GET", "/v1/knowledge-bases/kb-1/documents/doc-1/source-file"),
    ("GET", "/v1/graph/overview"),
    ("GET", "/v1/graph/neighborhood"),
    ("GET", "/v1/graph/native"),
    ("POST", "/v1/feedback"),
    ("POST", "/v1/knowledge-bases/kb-1/query"),
)

# Routes that must keep requiring credentials. The near-misses are the
# interesting ones: they share a prefix or a suffix with a public route but
# must not be treated as public.
PRIVATE_CASES: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/knowledge-bases/kb-1"),
    ("POST", "/v1/knowledge-bases"),
    ("GET", "/v1/knowledge-bases/kb-1/documents"),
    ("POST", "/v1/knowledge-bases/kb-1/documents"),
    ("PUT", "/v1/knowledge-bases/kb-1/documents/doc-1"),
    ("DELETE", "/v1/knowledge-bases/kb-1/documents/doc-1"),
    ("GET", "/v1/knowledge-bases/kb-1/documents/doc-1/summary"),
    ("GET", "/v1/knowledge-bases/kb-1/generations"),
    ("GET", "/v1/knowledge-bases/kb-1/query"),
    ("POST", "/v1/knowledge-bases/kb-1/query/extra"),
    ("POST", "/v1/knowledge-bases/kb-1/documents/doc-1/reprocess"),
    ("POST", "/v1/knowledge-bases//query"),
    ("GET", "/v1/tasks"),
    ("GET", "/v1/tasks/task-1"),
    ("GET", "/v1/graph"),
    ("POST", "/v1/graph/overview"),
    ("GET", "/v1/feedback"),
    ("GET", "/v1/admin/feedback"),
    ("GET", "/metrics"),
)


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


class _NoRuntime:
    """Runtime that fails loudly if the auth boundary ever lets a request through."""

    def query(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("the auth boundary must reject before runtime use")

    def close(self) -> None:
        return None


def _secured_app():
    settings = Settings(
        api_key="offline-test-key",
        service_api_key="service-secret",
        admin_api_key="admin-secret",
    )
    return create_app(settings=settings, runtime_factory=lambda _: _NoRuntime())


@pytest.mark.parametrize(("method", "path"), PUBLIC_CASES)
def test_public_routes_are_allowlisted(method: str, path: str) -> None:
    assert _is_public_browser_request(_request(method, path)) is True


@pytest.mark.parametrize(("method", "path"), PRIVATE_CASES)
def test_non_public_routes_are_not_allowlisted(method: str, path: str) -> None:
    assert _is_public_browser_request(_request(method, path)) is False


def test_middleware_rejects_unauthenticated_management_request() -> None:
    with TestClient(_secured_app(), raise_server_exceptions=False) as client:
        response = client.get("/v1/knowledge-bases/kb-1/documents")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_middleware_does_not_reject_public_query_route() -> None:
    with TestClient(_secured_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/knowledge-bases/kb-1/query",
            json={"query": "泵的维护步骤", "history": []},
        )

    # The handler may fail because no knowledge base exists, but the request
    # itself must never be rejected by the authentication middleware.
    assert response.status_code != 401


def test_health_routes_stay_reachable_with_credentials_configured() -> None:
    with TestClient(_secured_app(), raise_server_exceptions=False) as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/healthz").status_code == 200


def test_missing_credentials_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured deployment must not grant anonymous administrator rights."""

    monkeypatch.delenv("IRA_DEV_INSECURE", raising=False)
    monkeypatch.setenv("IRA_DEPLOYMENT_ENVIRONMENT", "local_dev")
    app = create_app(settings=Settings(api_key="offline-test-key"), runtime_factory=lambda _: _NoRuntime())

    with TestClient(app, raise_server_exceptions=False) as client:
        management = client.get("/v1/knowledge-bases/kb-1/documents")
        public = client.get("/v1/graph/overview")
        health = client.get("/readyz")

    assert management.status_code == 401
    assert public.status_code == 401
    assert health.status_code == 200


def test_insecure_local_dev_flag_restores_uncredentialed_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRA_DEV_INSECURE", "1")
    monkeypatch.setenv("IRA_DEPLOYMENT_ENVIRONMENT", "local_dev")
    app = create_app(settings=Settings(api_key="offline-test-key"), runtime_factory=lambda _: _NoRuntime())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/knowledge-bases/kb-1/documents")

    assert response.status_code != 401


def test_insecure_flag_is_ignored_outside_local_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRA_DEV_INSECURE", "1")
    settings = Settings(
        api_key="offline-test-key",
        deployment_environment="local_staging",
        service_api_key="service-secret",
        admin_api_key="admin-secret",
    )
    app = create_app(settings=settings, runtime_factory=lambda _: _NoRuntime())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/knowledge-bases/kb-1/documents")

    assert response.status_code == 401
