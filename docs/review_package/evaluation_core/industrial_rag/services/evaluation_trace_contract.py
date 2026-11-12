"""Deterministic, evaluation-only trace schema and metric helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _rank(ids: Sequence[str], evidence_id: str) -> int | None:
    try:
        return list(ids).index(evidence_id) + 1
    except ValueError:
        return None


def build_evaluation_trace(
    *,
    question_id: str,
    question: str,
    variants: Sequence[Mapping[str, Any]],
    retrieval_candidates: Sequence[Mapping[str, Any]],
    fusion_candidates: Sequence[Mapping[str, Any]],
    rerank_candidates: Sequence[Mapping[str, Any]],
    final_top5: Sequence[str],
    final_top10: Sequence[str],
    gold_evidence_ids: Sequence[str],
) -> dict[str, Any]:
    canonical_gold = [str(item) for item in gold_evidence_ids]
    retrieval = [dict(item) for item in retrieval_candidates]
    fusion = [dict(item) for item in fusion_candidates]
    rerank = [dict(item) for item in rerank_candidates]
    top5, top10 = [str(x) for x in final_top5], [str(x) for x in final_top10]
    retrieval_by_gold: dict[str, list[dict[str, Any]]] = {item: [] for item in canonical_gold}
    for item in retrieval:
        evidence_id = str(item.get("evidence_id", ""))
        if evidence_id in retrieval_by_gold:
            retrieval_by_gold[evidence_id].append(item)
    return {
        "trace_version": "phase13d2-evaluation-trace-v1",
        "question_id": question_id,
        "question": question,
        "query_variants": [dict(item) for item in variants],
        "retrieval_candidates": retrieval,
        "fusion_candidates": fusion,
        "rerank_candidates": rerank,
        "final": {"top5_evidence_ids": top5, "top10_evidence_ids": top10},
        "gold_lineage": [
            {
                "gold_evidence_id": evidence_id,
                "retrieval_hit": bool(retrieval_by_gold[evidence_id]),
                "retrieval_sources": sorted({str(item.get("retriever_source")) for item in retrieval_by_gold[evidence_id]}),
                "best_local_rank": min((int(item["local_rank"]) for item in retrieval_by_gold[evidence_id] if item.get("local_rank") is not None), default=None),
                "fusion_rank": next((item.get("fusion_rank") for item in fusion if item.get("evidence_id") == evidence_id), None),
                "rerank_rank": next((item.get("rerank_rank") for item in rerank if item.get("evidence_id") == evidence_id), None),
                "final_top5_rank": _rank(top5, evidence_id),
                "final_top10_rank": _rank(top10, evidence_id),
            }
            for evidence_id in canonical_gold
        ],
    }


def validate_trace_contract(trace: Mapping[str, Any], *, raise_on_error: bool = False) -> list[str]:
    errors: list[str] = []
    for field in ("question_id", "question", "query_variants", "retrieval_candidates", "fusion_candidates", "rerank_candidates", "final", "gold_lineage"):
        if field not in trace:
            errors.append(f"missing:{field}")
    final = trace.get("final", {})
    for field in ("top5_evidence_ids", "top10_evidence_ids"):
        if field not in final:
            errors.append(f"missing:final.{field}")
    for field in ("top5_evidence_ids", "top10_evidence_ids"):
        values = list(final.get(field, []))
        if len(values) != len(set(values)):
            errors.append(f"duplicate:{field}")
    if raise_on_error and errors:
        raise ValueError("; ".join(errors))
    return errors


def recompute_trace_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gold_count = sum(len(row["expected_evidence"]) for row in rows)
    def values(k: int) -> tuple[list[float], list[float], list[bool], list[bool], list[bool], list[bool]]:
        recalls: list[float] = []
        reciprocal: list[float] = []
        hits: list[bool] = []
        completes: list[bool] = []
        for row in rows:
            gold, ranked = set(row["expected_evidence"]), list(row[f"final_top{k}"])
            found = gold & set(ranked)
            recalls.append(len(found) / len(gold) if gold else 0.0)
            first = next((index + 1 for index, item in enumerate(ranked) if item in gold), None)
            reciprocal.append(1.0 / first if first is not None else 0.0)
            hits.append(bool(found))
            completes.append(bool(gold) and gold <= set(ranked))
        return recalls, reciprocal, hits, completes, [], []
    result: dict[str, Any] = {"gold_count": gold_count}
    for k in (5, 10):
        recalls, reciprocal, hits, completes, _, _ = values(k)
        n = len(rows) or 1
        result.update({f"recall@{k}": sum(recalls) / n, f"mrr@{k}": sum(reciprocal) / n, f"question_hit@{k}": sum(hits) / n, f"complete@{k}": sum(completes) / n})
    return result

