"""Offline-only multi-query candidate ablation orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from industrial_rag.services.lexical_retrieval import BM25Index
from industrial_rag.services.reranker_runtime import RerankerRuntime, RerankProvider
from industrial_rag.services.rrf_fusion import reciprocal_rank_fusion


@dataclass(frozen=True, slots=True)
class QueryVariant:
    variant_id: str
    query: str


@dataclass(frozen=True, slots=True)
class A3CandidateRun:
    query_variants: tuple[QueryVariant, ...]
    union_child_ids: tuple[str, ...]
    first_seen_by_child: dict[str, dict[str, Any]]
    fused_rows: tuple[dict[str, Any], ...]
    final_rows: tuple[dict[str, Any], ...]
    latency_ms: float
    rerank_latency_ms: float
    rerank_fallback_reason: str | None


DenseRetriever = Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]]]]


def _normalize_rows(
    rows: Sequence[Mapping[str, Any]],
    valid_ids: set[str],
    *,
    source: str,
    variant: QueryVariant,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rank, row in enumerate(rows, 1):
        child_id = str(row.get("child_chunk_id") or row.get("chunk_id") or "").strip()
        if not child_id or child_id in seen or child_id not in valid_ids:
            continue
        seen.add(child_id)
        item = dict(row)
        item.update({"child_chunk_id": child_id, "rank": rank, "source": source})
        item["variant_id"] = variant.variant_id
        item["variant_query"] = variant.query
        normalized.append(item)
    return normalized


def _union_by_source(
    rows_by_variant: Sequence[tuple[QueryVariant, Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]],
    valid_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...], dict[str, dict[str, Any]]]:
    dense_union: list[dict[str, Any]] = []
    sparse_union: list[dict[str, Any]] = []
    seen_dense: set[str] = set()
    seen_sparse: set[str] = set()
    first_seen: dict[str, dict[str, Any]] = {}
    for variant, dense_raw, sparse_raw in rows_by_variant:
        dense_rows = _normalize_rows(dense_raw, valid_ids, source="lightrag", variant=variant)
        sparse_rows = _normalize_rows(sparse_raw, valid_ids, source="sparse", variant=variant)
        for source_rows, target, seen, source_name in (
            (dense_rows, dense_union, seen_dense, "lightrag"),
            (sparse_rows, sparse_union, seen_sparse, "sparse"),
        ):
            for row in source_rows:
                child_id = row["child_chunk_id"]
                if child_id not in seen:
                    seen.add(child_id)
                    target.append(row)
                    first_seen.setdefault(
                        child_id,
                        {
                            "variant_id": variant.variant_id,
                            "variant_query": variant.query,
                            "source": source_name,
                            "first_rank": row["rank"],
                        },
                    )
    return dense_union, sparse_union, tuple(dict.fromkeys([row["child_chunk_id"] for row in dense_union + sparse_union])), first_seen


async def run_a3_candidates(
    *,
    query_variants: Sequence[QueryVariant],
    dense_retriever: DenseRetriever,
    sparse_index: BM25Index,
    reranker_provider: RerankProvider | None,
    generation_chunk_ids: set[str] | frozenset[str],
    candidate_top_n: int = 20,
    final_top_k: int = 10,
    rrf_k: int = 60,
) -> A3CandidateRun:
    """Run one A3 candidate experiment and exactly one final reranker call."""

    if not query_variants or not query_variants[0].variant_id == "original":
        raise ValueError("A3 must retain the original query as its first variant")
    if candidate_top_n <= 0 or final_top_k <= 0:
        raise ValueError("candidate_top_n and final_top_k must be positive")
    variants = tuple(query_variants)
    started = time.perf_counter()
    rows_by_variant: list[tuple[QueryVariant, Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]] = []
    for variant in variants:
        dense_rows, sparse_rows = await asyncio.gather(
            dense_retriever(variant.query, candidate_top_n),
            asyncio.to_thread(sparse_index.search, variant.query, limit=candidate_top_n),
        )
        sparse_payload = []
        for row in sparse_rows:
            if isinstance(row, Mapping):
                sparse_payload.append(dict(row))
            else:
                sparse_payload.append(
                    {
                        "child_chunk_id": row.child_chunk_id,
                        "score": row.score,
                        "rank": row.rank,
                    }
                )
        rows_by_variant.append((variant, dense_rows, sparse_payload))
    dense_union, sparse_union, union_ids, first_seen = _union_by_source(
        rows_by_variant, set(generation_chunk_ids)
    )
    fused = reciprocal_rank_fusion(
        {"lightrag": dense_union, "sparse": sparse_union}, k=rrf_k, limit=candidate_top_n
    )
    fused_rows = tuple(
        {
            "child_chunk_id": item.child_chunk_id,
            "score": item.rrf_score,
            "rrf_score": item.rrf_score,
            "rank": item.rrf_rank,
            "source": "rrf",
            "contributions": [
                {
                    "source": contribution.source,
                    "original_rank": contribution.original_rank,
                    "original_score": contribution.original_score,
                }
                for contribution in item.contributions
            ],
        }
        for item in fused
    )
    rerank_started = time.perf_counter()
    rerank_result = await RerankerRuntime(
        provider=reranker_provider,
        timeout_seconds=2.0,
        provider_name="aliyun_model_studio",
    ).rerank(variants[0].query, fused_rows, limit=final_top_k)
    final_rows = tuple(dict(row, rank=rank) for rank, row in enumerate(rerank_result.candidates, 1))
    return A3CandidateRun(
        query_variants=variants,
        union_child_ids=union_ids,
        first_seen_by_child=first_seen,
        fused_rows=fused_rows,
        final_rows=final_rows,
        latency_ms=(time.perf_counter() - started) * 1000,
        rerank_latency_ms=(time.perf_counter() - rerank_started) * 1000,
        rerank_fallback_reason=rerank_result.fallback_reason,
    )


__all__ = ["A3CandidateRun", "QueryVariant", "run_a3_candidates"]
