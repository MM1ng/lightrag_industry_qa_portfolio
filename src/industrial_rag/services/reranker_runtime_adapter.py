"""Adapters for wiring a concrete reranker into the generic runtime boundary."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any


class DashScopeRuntimeAdapter:
    """Translate frozen child candidates to the existing provider contract."""

    def __init__(self, *, provider: Any, chunk_records: Mapping[str, Mapping[str, Any]]) -> None:
        self.provider = provider
        self.chunk_records = chunk_records

    async def __call__(
        self, query: str, candidates: Sequence[Mapping[str, Any]]
    ) -> list[tuple[dict[str, str], float]]:
        provider_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            child_id = str(candidate.get("child_chunk_id") or "").strip()
            record = self.chunk_records.get(child_id)
            if not child_id or record is None:
                raise ValueError(f"reranker candidate is not in frozen generation: {child_id}")
            content = str(record.get("content") or "")
            provider_candidates.append(
                {
                    "chunk_id": child_id,
                    "text": content,
                    "child_text_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "document_id": record.get("document_id"),
                    "page": record.get("page_start"),
                    "original_rank": candidate.get("rank"),
                    "original_score": candidate.get("rrf_score", candidate.get("score")),
                }
            )
        results = await self.provider.rerank(query, provider_candidates, top_n=len(provider_candidates))
        output: list[tuple[dict[str, str], float]] = []
        returned: list[dict[str, Any]] = []
        for result in results:
            child_id = str(getattr(result, "chunk_id", "") or "").strip()
            score = getattr(result, "rerank_score", None)
            if child_id and isinstance(score, (int, float)):
                output.append(({"child_chunk_id": child_id}, float(score)))
                returned.append({"child_chunk_id": child_id, "score": float(score)})
        calls = getattr(self.provider, "calls", None)
        if isinstance(calls, list) and calls:
            calls[-1]["returned_scores"] = returned
            calls[-1]["returned_ranks"] = [
                {"child_chunk_id": item["child_chunk_id"], "rank": rank}
                for rank, item in enumerate(returned, 1)
            ]
        return output


__all__ = ["DashScopeRuntimeAdapter"]
