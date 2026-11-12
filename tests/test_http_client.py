"""Tests for the environment-safe httpx client builder.

Regression guard: on a developer machine whose ``NO_PROXY`` lists a bracketed
IPv6 literal (``[::1]``, which several tools and shell profiles emit by
default), ``httpx.AsyncClient()`` raises ``InvalidURL: Invalid port: ':1]'``
while merely reading the environment. Because the voice provider builds its
client inside the application lifespan, that single malformed variable made
every voice call silently degrade to "feature disabled" in ``local_dev``,
where startup exceptions are swallowed. The builder must not let proxy parsing
decide whether a feature works.
"""

from __future__ import annotations

import logging

import pytest
from industrial_rag.http_client import build_async_client

_PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_clean_environment_still_honours_configured_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    async with build_async_client(timeout=5.0) as client:
        assert client.trust_env is True


@pytest.mark.asyncio
async def test_bracketed_ipv6_in_no_proxy_falls_back_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact value a shell profile injected: an IPv6 literal in brackets."""
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "localhost,.localhost,127.0.0.1,[::1],::1")
    async with build_async_client(timeout=5.0) as client:
        assert client.trust_env is False


@pytest.mark.asyncio
async def test_fallback_is_logged_as_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "[::1]")
    with caplog.at_level(logging.WARNING, logger="industrial_rag.http_client"):
        async with build_async_client(timeout=5.0):
            pass
    messages = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("proxy" in m.lower() for m in messages), messages


@pytest.mark.asyncio
async def test_timeout_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_env(monkeypatch)
    async with build_async_client(timeout=12.5) as client:
        assert client.timeout.connect == 12.5


@pytest.mark.asyncio
async def test_extra_keyword_arguments_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    async with build_async_client(
        timeout=5.0, headers={"X-Trace": "voice"}
    ) as client:
        assert client.headers["X-Trace"] == "voice"


@pytest.mark.asyncio
async def test_fallback_client_is_usable_for_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the fallback is a working client, not just a constructed one."""
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "[::1]")
    async with build_async_client(timeout=5.0) as client:
        request = client.build_request(
            "GET", "https://dashscope.invalid/api/v1/ping",
        )
        assert request.url.host == "dashscope.invalid"
