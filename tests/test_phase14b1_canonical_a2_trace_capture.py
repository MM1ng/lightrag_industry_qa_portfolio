from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_real_a2_runner_emits_pre_and_post_rerank_trace_without_changing_final_ids() -> None:
    from industrial_rag.parser_models import ChildChunk
    from industrial_rag.services.lexical_retrieval import BM25Index
    from industrial_rag.services.retrieval_ab_evaluation import run_ab_evaluation

    children = [
        ChildChunk(chunk_id="child-1", parent_chunk_id="parent-1", document_id="doc-1", document_name="manual.pdf", content="alpha", embedding_content="alpha"),
        ChildChunk(chunk_id="child-2", parent_chunk_id="parent-1", document_id="doc-1", document_name="manual.pdf", content="beta", embedding_content="beta"),
    ]
    events: list[dict[str, object]] = []

    async def dense(_query: str, _limit: int):
        return [{"child_chunk_id": "child-1", "score": 0.7}, {"child_chunk_id": "child-2", "score": 0.6}]

    async def rerank(_query: str, candidates):
        return [(candidates[1], 0.9), (candidates[0], 0.1)]

    report = await run_ab_evaluation(
        cases=[{"id": "S1", "split": "development", "question": "beta", "relevant_chunk_ids": ["child-2"]}],
        generation=SimpleNamespace(chunk_ids=frozenset({"child-1", "child-2"}), generation_id="g", child_manifest_hash="h", corpus_fingerprint="c"),
        sparse_index=BM25Index.from_records([child.to_dict() for child in children]),
        lightrag_retriever=dense,
        reranker_provider=rerank,
        allow_reranker_fallback=False,
        candidate_top_n=2,
        final_top_k=1,
        trace_observer=events.append,
    )

    assert [event["event"] for event in events] == ["pre_rerank", "post_rerank"]
    assert {row["child_chunk_id"] for row in events[0]["fusion_candidates"]} == {"child-1", "child-2"}
    assert events[1]["rerank_candidates"][0]["rerank_score"] == 0.9
    assert events[1]["rerank_candidates"][1]["rerank_rank"] == 2
    assert report["per_question"][0]["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"][0]["child_chunk_id"] == events[1]["rerank_candidates"][0]["child_chunk_id"]
