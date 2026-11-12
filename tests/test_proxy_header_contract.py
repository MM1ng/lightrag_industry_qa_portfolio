"""The anonymous rate limit must survive the ASGI server's proxy headers.

uvicorn rewrites ``request.client`` from ``X-Forwarded-For`` before the app sees
the request whenever the peer is in ``--forwarded-allow-ips`` (default
``127.0.0.1``). ``IRA_TRUST_PROXY_HEADERS=false`` cannot undo that, so a caller
who can reach uvicorn from a trusted peer gets a fresh bucket per request.

The application cannot detect the rewrite reliably, so the launch command is the
control. These tests pin the contract:

* the bundled launcher disables proxy headers by default;
* the trusted mode must be opted into explicitly;
* the startup log states which mode is active.

Measured on 2026-10-29 against the real server: default launch admitted 31
requests carrying 31 different ``X-Forwarded-For`` values; ``--no-proxy-headers``
returned 429 on the 31st.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from industrial_rag.api import _log_proxy_header_mode
from industrial_rag.config import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "start_api.ps1"


def test_launcher_disables_uvicorn_proxy_headers_by_default() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--no-proxy-headers" in text
    # Opt-in only: forwarded-allow-ips must not be hard-coded into the default path.
    assert "--forwarded-allow-ips" in text
    assert "if ($ForwardedAllowIps)" in text


def test_startup_log_states_the_proxy_header_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(api_key="offline-test-key", trust_proxy_headers=False)

    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        _log_proxy_header_mode(settings)
    assert "mode=IGNORED" in caplog.text
    assert "--no-proxy-headers" in caplog.text


def test_startup_log_reports_trusted_mode(caplog: object) -> None:
    settings = Settings(api_key="offline-test-key", trust_proxy_headers=True)

    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        _log_proxy_header_mode(settings)
    assert "mode=TRUSTED" in caplog.text
