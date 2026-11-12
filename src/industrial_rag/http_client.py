"""httpx client construction that survives a malformed proxy environment.

``httpx.AsyncClient()`` reads ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY``
while it is being *constructed*, before any request is made. A ``NO_PROXY``
that lists a bracketed IPv6 literal — ``[::1]``, which shell profiles and
several desktop proxy tools emit by default — makes it raise
``httpx.InvalidURL: Invalid port: ':1]'``.

That turns one environment variable into a feature outage: the voice provider
builds its client inside the application lifespan, where ``local_dev`` swallows
startup exceptions, so the operator sees "语音功能未启用" instead of the real
cause. Any client we build must either work or say why it does not.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("industrial_rag.http_client")


def build_async_client(
    *,
    timeout: float | httpx.Timeout = 30.0,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an ``AsyncClient``, dropping environment proxies if they are unusable.

    Environment proxies are honoured whenever the variables parse cleanly, so a
    deployment that needs them keeps working. When they do not parse, the client
    is rebuilt with ``trust_env=False`` and the reason is logged: a request that
    goes out directly is a far better outcome than a silently disabled feature.
    """

    trust_env = bool(kwargs.pop("trust_env", True))
    try:
        return httpx.AsyncClient(timeout=timeout, trust_env=trust_env, **kwargs)
    except httpx.InvalidURL as error:
        if not trust_env:
            # Nothing environment-related to fall back on; let the caller see it.
            raise
        logger.warning(
            "environment proxy settings are unusable (%s); rebuilding this client "
            "with trust_env=False. Check HTTP_PROXY/HTTPS_PROXY/NO_PROXY — a "
            "bracketed IPv6 literal such as '[::1]' in NO_PROXY is enough to "
            "trigger this.",
            error,
        )
        return httpx.AsyncClient(timeout=timeout, trust_env=False, **kwargs)
