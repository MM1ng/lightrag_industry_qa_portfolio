"""Contract tests for the hard deadline on knowledge-base queries.

Before this deadline existed, a stalled upstream model call held the runtime
query lock indefinitely and serialised every later request in the process.
"""

from __future__ import annotations

import asyncio

import pytest
from industrial_rag.config import Settings
from industrial_rag.errors import AppError
from industrial_rag.services.query_application_service import _run_query_with_timeout


class _HangingRuntime:
    async def query(self, question: str, **kwargs: object) -> str:
        await asyncio.sleep(10)
        return "never reached"


class _RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def query(self, question: str, **kwargs: object) -> str:
        self.calls.append((question, kwargs))
        return "answer"


class _LegacyRuntime:
    """Runtime that only accepts the older ``mode``-only signature."""

    async def query(self, question: str, *, mode: str) -> str:
        return f"legacy:{mode}"


@pytest.mark.asyncio
async def test_query_timeout_raises_public_timeout_error() -> None:
    with pytest.raises(AppError) as excinfo:
        await _run_query_with_timeout(
            _HangingRuntime(),
            "泵组维护前需要做什么",
            mode="mix",
            top_k=5,
            chunk_top_k=10,
            timeout_seconds=0.05,
        )

    assert excinfo.value.code == "QA_TIMEOUT"
    assert excinfo.value.status_code == 504


@pytest.mark.asyncio
async def test_query_passes_retrieval_parameters_through() -> None:
    runtime = _RecordingRuntime()

    result = await _run_query_with_timeout(
        runtime,
        "q",
        mode="mix",
        top_k=5,
        chunk_top_k=10,
        timeout_seconds=5,
    )

    assert result == "answer"
    assert runtime.calls == [("q", {"mode": "mix", "top_k": 5, "chunk_top_k": 10})]


@pytest.mark.asyncio
async def test_legacy_runtime_signature_still_supported() -> None:
    result = await _run_query_with_timeout(
        _LegacyRuntime(),
        "q",
        mode="mix",
        top_k=5,
        chunk_top_k=10,
        timeout_seconds=5,
    )

    assert result == "legacy:mix"


def test_settings_reads_query_timeout_from_environment() -> None:
    settings = Settings.from_mapping(
        {"DASHSCOPE_API_KEY": "offline", "QA_REQUEST_TIMEOUT_SECONDS": "45"}
    )

    assert settings.qa_request_timeout_seconds == 45.0


def test_settings_defaults_query_timeout_to_180_seconds() -> None:
    settings = Settings.from_mapping({"DASHSCOPE_API_KEY": "offline"})

    assert settings.qa_request_timeout_seconds == 180.0


def test_settings_rejects_non_positive_query_timeout() -> None:
    with pytest.raises(ValueError):
        Settings.from_mapping(
            {"DASHSCOPE_API_KEY": "offline", "QA_REQUEST_TIMEOUT_SECONDS": "0"}
        )
