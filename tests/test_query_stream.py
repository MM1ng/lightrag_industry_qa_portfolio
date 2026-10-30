"""Streaming query surface.

Two layers are covered here:

* ``_OfficialBackend.generate`` must stream from the upstream model when asked,
  and still return the complete text for citation grounding.
* ``_query_event_stream`` turns a blocking query into ordered SSE frames, always
  ending in a terminal ``final`` or ``error`` event.

The preview/token distinction matters: deltas are unvalidated text, so the only
authoritative result is the ``final`` payload — the same one the non-streaming
endpoint returns.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from industrial_rag.api import _query_event_stream
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.lightrag_service import _OfficialBackend


def _run(coro):
    return asyncio.run(coro)


async def _collect(stream: AsyncIterator[bytes]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    async for chunk in stream:
        if not chunk.startswith(b"data: "):
            continue  # keep-alive comment
        frames.append(json.loads(chunk[len(b"data: ") :].decode().strip()))
    return frames


def test_event_stream_emits_stage_delta_then_final() -> None:
    async def _runner(on_delta) -> dict[str, Any]:
        on_delta("结论：")
        on_delta("请先停机。")
        return {"status": "success", "answer": "结论：请先停机。"}

    frames = _run(_collect(_query_event_stream(_runner)))

    assert [frame["type"] for frame in frames] == [
        "stage",
        "stage",
        "delta",
        "delta",
        "final",
    ]
    assert frames[0]["stage"] == "retrieving"
    assert frames[1]["stage"] == "generating"
    assert "".join(frame["text"] for frame in frames if frame["type"] == "delta") == (
        "结论：请先停机。"
    )
    assert frames[-1]["payload"]["status"] == "success"


def test_event_stream_reports_app_error_as_terminal_frame() -> None:
    async def _runner(on_delta) -> dict[str, Any]:
        raise AppError(
            AppErrorCode.qa_timeout, "问答请求超时，请稍后重试。", status_code=504
        )

    frames = _run(_collect(_query_event_stream(_runner)))

    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == AppErrorCode.qa_timeout
    assert not [frame for frame in frames if frame["type"] == "final"]


def test_event_stream_reports_unexpected_failure() -> None:
    async def _runner(on_delta) -> dict[str, Any]:
        raise RuntimeError("boom")

    frames = _run(_collect(_query_event_stream(_runner)))

    assert frames[-1] == {
        "type": "error",
        "code": "INTERNAL_ERROR",
        "message": "查询没有完成，请稍后重试。",
    }


class _StreamingModel:
    """Minimal stand-in for the OpenAI-compatible callable."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, prompt: str, *, system_prompt=None, stream=False, **kwargs):
        self.calls.append({"stream": stream})
        if not stream:
            return "结论：请先停机。"
        return self._generator()

    async def _generator(self):
        for piece in ("结论：", "请先停机。"):
            yield piece


def _backend(model: Any) -> _OfficialBackend:
    backend = _OfficialBackend.__new__(_OfficialBackend)
    backend._llm_model_func = model
    return backend


def test_generate_streams_fragments_and_returns_full_text() -> None:
    model = _StreamingModel()
    deltas: list[str] = []

    answer = _run(
        _backend(model).generate(
            "泵维护前要做什么？", "context", "system", on_delta=deltas.append
        )
    )

    assert answer == "结论：请先停机。"
    assert "".join(deltas) == answer
    assert model.calls == [{"stream": True}]


def test_generate_without_callback_keeps_the_blocking_call() -> None:
    model = _StreamingModel()

    answer = _run(_backend(model).generate("问题", "context", "system"))

    assert answer == "结论：请先停机。"
    assert model.calls == [{"stream": False}]


def test_generate_rejects_a_stream_when_no_callback_was_requested() -> None:
    class _Misbehaving(_StreamingModel):
        async def __call__(self, prompt, *, system_prompt=None, stream=False, **kwargs):
            return self._generator()

    with pytest.raises(RuntimeError, match="streaming response"):
        _run(_backend(_Misbehaving()).generate("问题", "context", "system"))
