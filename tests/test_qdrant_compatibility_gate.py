"""Fail-closed behaviour of the Qdrant minor-version gate.

The gate exists so a silent client/server drift cannot degrade vector search in
ways that look like retrieval bugs. It must reject every mismatch path rather
than warn, so each branch is pinned here.
"""

from __future__ import annotations

import pytest
from industrial_rag import qdrant_compatibility
from industrial_rag.qdrant_compatibility import check_qdrant_compatibility


class FakeResponse:
    def __init__(self, payload: dict, *, raise_for_status_error: Exception | None = None) -> None:
        self._payload = payload
        self._error = raise_for_status_error

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error


class FakeClient:
    """Minimal AsyncClient stand-in that records the requested URL."""

    def __init__(self, response: FakeResponse, *, timeout: float = 10.0) -> None:
        self._response = response
        self.timeout = timeout
        self.requested: list[str] = []

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def get(self, url: str) -> FakeResponse:
        self.requested.append(url)
        return self._response


def patch_client(monkeypatch, response: FakeResponse) -> FakeClient:
    holder: dict[str, FakeClient] = {}

    def factory(*args, **kwargs):
        client = FakeClient(response, **kwargs)
        holder["client"] = client
        return client

    monkeypatch.setattr(qdrant_compatibility, "AsyncClient", factory)
    return holder


def patch_client_version(monkeypatch, value: str) -> None:
    monkeypatch.setattr(qdrant_compatibility, "version", lambda _name: value)


@pytest.mark.asyncio
async def test_matching_minor_versions_pass(monkeypatch) -> None:
    patch_client_version(monkeypatch, "1.13.3")
    holder = patch_client(monkeypatch, FakeResponse({"version": "1.13.7"}))
    result = await check_qdrant_compatibility("http://qdrant:6333/")
    assert result["compatible"] == "true"
    assert result["client_version"] == "1.13.3"
    assert result["server_version"] == "1.13.7"
    assert result["expected_minor"] == "1.13"
    # The trailing slash must be normalised before the version probe.
    assert holder["client"].requested == ["http://qdrant:6333/"]


@pytest.mark.asyncio
async def test_client_minor_mismatch_fails_closed(monkeypatch) -> None:
    patch_client_version(monkeypatch, "1.12.4")
    patch_client(monkeypatch, FakeResponse({"version": "1.13.7"}))
    with pytest.raises(RuntimeError, match="minor version"):
        await check_qdrant_compatibility("http://qdrant:6333")


@pytest.mark.asyncio
async def test_server_minor_mismatch_fails_closed(monkeypatch) -> None:
    patch_client_version(monkeypatch, "1.13.3")
    patch_client(monkeypatch, FakeResponse({"version": "1.14.0"}))
    with pytest.raises(RuntimeError, match="minor version"):
        await check_qdrant_compatibility("http://qdrant:6333")


@pytest.mark.asyncio
async def test_missing_server_version_fails_closed(monkeypatch) -> None:
    """An empty version string must not be treated as a match."""
    patch_client_version(monkeypatch, "1.13.3")
    patch_client(monkeypatch, FakeResponse({}))
    with pytest.raises(RuntimeError, match="minor version"):
        await check_qdrant_compatibility("http://qdrant:6333")


@pytest.mark.asyncio
async def test_custom_expected_minor_is_honoured(monkeypatch) -> None:
    patch_client_version(monkeypatch, "1.14.0")
    patch_client(monkeypatch, FakeResponse({"version": "1.14.2"}))
    result = await check_qdrant_compatibility("http://qdrant:6333", expected_minor="1.14")
    assert result["expected_minor"] == "1.14"


@pytest.mark.asyncio
async def test_http_error_propagates(monkeypatch) -> None:
    patch_client_version(monkeypatch, "1.13.3")
    patch_client(
        monkeypatch,
        FakeResponse({"version": "1.13.7"}, raise_for_status_error=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        await check_qdrant_compatibility("http://qdrant:6333")
