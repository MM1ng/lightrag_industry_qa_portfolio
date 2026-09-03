from __future__ import annotations

import asyncio

from industrial_rag.services.multi_query_ablation import QueryVariant, run_a3_candidates


def test_a3_unions_deduplicates_and_reranks_once() -> None:
    calls: list[str] = []

    async def dense(query: str, _limit: int):
        calls.append(query)
        rows = {
            "原始": [{"child_chunk_id": "gold-a", "score": 1.0}],
            "参数角度": [{"child_chunk_id": "gold-b", "score": 2.0}, {"child_chunk_id": "gold-a", "score": 1.0}],
            "故障角度": [{"child_chunk_id": "other", "score": 3.0}],
        }
        return rows[query]

    class Sparse:
        def search(self, query: str, *, limit: int):
            rows = {
                "原始": [{"child_chunk_id": "gold-a", "score": 1.0, "rank": 1}],
                "参数角度": [{"child_chunk_id": "gold-b", "score": 2.0, "rank": 1}],
                "故障角度": [{"child_chunk_id": "other", "score": 3.0, "rank": 1}],
            }
            return rows[query][:limit]

    rerank_calls: list[tuple[str, int]] = []

    async def reranker(query, candidates):
        rerank_calls.append((query, len(candidates)))
        return [(candidate, float(index)) for index, candidate in enumerate(candidates, 1)]

    result = asyncio.run(
        run_a3_candidates(
            query_variants=(
                QueryVariant("original", "原始"),
                QueryVariant("parameter", "参数角度"),
                QueryVariant("fault", "故障角度"),
            ),
            dense_retriever=dense,
            sparse_index=Sparse(),
            reranker_provider=reranker,
            generation_chunk_ids={"gold-a", "gold-b", "other"},
            candidate_top_n=20,
            final_top_k=2,
            rrf_k=60,
        )
    )

    assert calls == ["原始", "参数角度", "故障角度"]
    assert rerank_calls == [("原始", 3)]
    assert len(result.final_rows) == 2
    assert result.first_seen_by_child["gold-b"]["variant_id"] == "parameter"
    assert result.first_seen_by_child["gold-b"]["first_rank"] == 1
    assert set(result.union_child_ids) == {"gold-a", "gold-b", "other"}
