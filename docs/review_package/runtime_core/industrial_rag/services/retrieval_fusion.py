"""Async orchestration for dense and sparse retrieval before later reranking."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from industrial_rag.services.lexical_retrieval import BM25Index
from industrial_rag.services.rrf_fusion import FusedCandidate, reciprocal_rank_fusion


class HybridRetriever:
    """Run LightRAG and BM25 concurrently and combine them with RRF."""

    def __init__(
        self,
        *,
        dense_retriever: Callable[[str], Awaitable[list[Mapping[str, Any]]]],
        sparse_index: BM25Index,
        rrf_k: int = 60,
    ) -> None:
        self._dense_retriever = dense_retriever
        self._sparse_index = sparse_index
        self._rrf_k = rrf_k

    async def retrieve(self, query: str, *, top_k: int = 10) -> tuple[FusedCandidate, ...]:
        if top_k <= 0:
            return ()
        dense_task = self._dense_retriever(query)
        sparse_task = asyncio.to_thread(self._sparse_index.search, query, limit=top_k)
        dense_rows, sparse_rows = await asyncio.gather(dense_task, sparse_task)
        sparse_payload = [
            {"child_chunk_id": item.child_chunk_id, "score": item.score} for item in sparse_rows
        ]
        return reciprocal_rank_fusion(
            {"lightrag": dense_rows, "sparse": sparse_payload}, k=self._rrf_k, limit=top_k
        )


__all__ = ["HybridRetriever"]
