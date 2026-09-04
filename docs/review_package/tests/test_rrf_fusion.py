import asyncio

from industrial_rag.services.lexical_retrieval import BM25Index
from industrial_rag.services.retrieval_fusion import HybridRetriever
from industrial_rag.services.rrf_fusion import reciprocal_rank_fusion


def test_rrf_preserves_source_rank_score_and_deduplicates_child_ids():
    fused = reciprocal_rank_fusion(
        {
            "lightrag": [
                {"child_chunk_id": "c1", "score": 0.9},
                {"child_chunk_id": "c2", "score": 0.8},
            ],
            "sparse": [
                {"child_chunk_id": "c2", "score": 4.2},
                {"child_chunk_id": "c1", "score": 3.1},
            ],
        },
        k=60,
    )

    assert [item.child_chunk_id for item in fused] == ["c1", "c2"]
    assert len(fused) == 2
    assert fused[0].rrf_score == fused[1].rrf_score
    assert {(entry.source, entry.original_rank, entry.original_score) for entry in fused[0].contributions} == {
        ("lightrag", 1, 0.9),
        ("sparse", 2, 3.1),
    }


def test_rrf_uses_stable_child_id_tie_break_and_ignores_empty_or_invalid_rows():
    fused = reciprocal_rank_fusion(
        {
            "sparse": [
                {"child_chunk_id": "z", "score": None},
                {"child_chunk_id": "", "score": 100},
                {"score": 50},
            ],
            "lightrag": [{"child_chunk_id": "a", "score": None}],
        },
        k=0,
    )

    assert [item.child_chunk_id for item in fused] == ["a", "z"]
    assert all(item.contributions[0].original_score is None for item in fused)


def test_rrf_limit_returns_top_unique_candidates():
    fused = reciprocal_rank_fusion(
        {
            "lightrag": [{"child_chunk_id": f"c{i}", "score": float(i)} for i in range(5)]
        },
        k=60,
        limit=2,
    )

    assert len(fused) == 2
    assert [item.rrf_rank for item in fused] == [1, 2]


def test_hybrid_retriever_runs_dense_and_sparse_concurrently_and_fuses():
    sparse = BM25Index.from_records(
        [
            {"chunk_id": "c1", "content": "2196-R pump"},
            {"chunk_id": "c2", "content": "NPSH parameter"},
        ]
    )
    events: list[str] = []

    async def dense(_query: str):
        events.append("dense")
        await asyncio.sleep(0)
        return [{"child_chunk_id": "c2", "score": 0.9}]

    fused = asyncio.run(
        HybridRetriever(dense_retriever=dense, sparse_index=sparse).retrieve("NPSH", top_k=2)
    )

    assert events == ["dense"]
    assert fused[0].child_chunk_id == "c2"
    assert {item.source for item in fused[0].contributions} == {"lightrag", "sparse"}
