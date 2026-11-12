"""Development-only retrieval ranking metrics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def reciprocal_rank(ranked_ids: Sequence[str], relevant_ids: set[str], *, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    for rank, child_id in enumerate(_dedupe(ranked_ids), 1):
        if rank > k:
            break
        if child_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def _recall(ranked_ids: Sequence[str], relevant_ids: set[str], *, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant_ids:
        return 0.0
    return len(set(_dedupe(ranked_ids)[:k]) & relevant_ids) / len(relevant_ids)


def evaluate_rankings(
    cases: Iterable[Mapping[str, Any]],
    rankings_by_system: Mapping[str, Sequence[Sequence[str]]],
    *,
    case_metadata: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return evidence and question-level metrics overall and by frozen strata."""
    normalized_cases = list(cases)
    if not normalized_cases:
        raise ValueError("evaluation cases must not be empty")
    if case_metadata is not None and len(case_metadata) != len(normalized_cases):
        raise ValueError("case metadata count does not match evaluation cases")
    prepared = [
        (
            str(case.get("question_type") or "unknown"),
            {str(value).strip() for value in case.get("relevant_chunk_ids", ()) if str(value).strip()},
        )
        for case in normalized_cases
    ]
    report: dict[str, dict[str, Any]] = {}
    for system, rankings in rankings_by_system.items():
        if len(rankings) != len(prepared):
            raise ValueError(f"{system} case count does not match evaluation cases")
        buckets: dict[str, list[tuple[Sequence[str], set[str]]]] = {"overall": []}
        metadata_rows = case_metadata or ({"question_type": question_type} for question_type, _ in prepared)
        for index, ((question_type, relevant), ranking, metadata) in enumerate(zip(prepared, rankings, metadata_rows, strict=True)):
            buckets["overall"].append((ranking, relevant))
            buckets.setdefault(question_type, []).append((ranking, relevant))
            for dimension in ("difficulty", "source_document", "source", "evidence_pattern", "question_type"):
                value = metadata.get(dimension)
                if value is not None:
                    buckets.setdefault(f"{dimension}={value}", []).append((ranking, relevant))
        report[system] = {bucket: _metrics(values) for bucket, values in buckets.items()}
    return report


def _metrics(values: Sequence[tuple[Sequence[str], set[str]]]) -> dict[str, float]:
    return {
        "n": len(values),
        "recall@5": _mean(_recall(ranking, relevant, k=5) for ranking, relevant in values),
        "recall@10": _mean(_recall(ranking, relevant, k=10) for ranking, relevant in values),
        "mrr@5": _mean(reciprocal_rank(ranking, relevant, k=5) for ranking, relevant in values),
        "mrr@10": _mean(reciprocal_rank(ranking, relevant, k=10) for ranking, relevant in values),
        "question_hit@5": _mean(float(bool(set(_dedupe(ranking)[:5]) & relevant)) for ranking, relevant in values),
        "question_hit@10": _mean(float(bool(set(_dedupe(ranking)[:10]) & relevant)) for ranking, relevant in values),
        "complete_evidence_coverage@5": _mean(float(relevant <= set(_dedupe(ranking)[:5])) for ranking, relevant in values),
        "complete_evidence_coverage@10": _mean(float(relevant <= set(_dedupe(ranking)[:10])) for ranking, relevant in values),
    }


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


__all__ = ["evaluate_rankings", "reciprocal_rank"]
