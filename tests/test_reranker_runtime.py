import asyncio

import pytest
from industrial_rag.services.reranker_runtime import RerankerResult, RerankerRuntime


def test_reranker_provider_success_returns_final_order_and_metadata():
    async def provider(_query, candidates):
        return [(candidates[1], 0.9), (candidates[0], 0.2)]

    result = asyncio.run(
        RerankerRuntime(provider=provider, timeout_seconds=0.2).rerank(
            "q", [{"child_chunk_id": "a"}, {"child_chunk_id": "b"}], limit=1
        )
    )

    assert isinstance(result, RerankerResult)
    assert [item["child_chunk_id"] for item in result.candidates] == ["b"]
    assert result.enabled is True
    assert result.provider == "custom"
    assert result.fallback_reason is None


def test_reranker_provider_failure_falls_back_to_rrf_ordering():
    async def provider(_query, _candidates):
        raise RuntimeError("provider unavailable")

    result = asyncio.run(
        RerankerRuntime(provider=provider).rerank(
            "q", [{"child_chunk_id": "a"}, {"child_chunk_id": "b"}], limit=2
        )
    )

    assert [item["child_chunk_id"] for item in result.candidates] == ["a", "b"]
    assert result.enabled is False
    assert result.fallback_reason == "provider_failure"


def test_reranker_timeout_falls_back_without_raising():
    async def provider(_query, _candidates):
        await asyncio.sleep(0.05)
        return []

    result = asyncio.run(
        RerankerRuntime(provider=provider, timeout_seconds=0.001).rerank(
            "q", [{"child_chunk_id": "a"}], limit=1
        )
    )

    assert [item["child_chunk_id"] for item in result.candidates] == ["a"]
    assert result.enabled is False
    assert result.fallback_reason == "timeout"


def test_reranker_rejects_non_positive_limit():
    runtime = RerankerRuntime(provider=None)
    with pytest.raises(ValueError):
        asyncio.run(runtime.rerank("q", [], limit=0))
