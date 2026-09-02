"""Deterministic, provenance-preserving Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RRFContribution:
    source: str
    original_rank: int
    original_score: float | None


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    child_chunk_id: str
    rrf_score: float
    rrf_rank: int
    contributions: tuple[RRFContribution, ...]


def reciprocal_rank_fusion(
    ranked_sources: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    k: int = 60,
    limit: int | None = None,
) -> tuple[FusedCandidate, ...]:
    """Fuse ranked candidate lists while retaining every source contribution.

    Rows are ranked by their position in each source list, not by score. Rows
    without a non-empty canonical ``child_chunk_id`` are discarded. Duplicate
    IDs in one source contribute only at their first rank.
    """

    if k < 0:
        raise ValueError("RRF k must be non-negative")
    if limit is not None and limit <= 0:
        return ()

    scores: dict[str, float] = {}
    contributions: dict[str, list[RRFContribution]] = {}
    for source, rows in ranked_sources.items():
        seen: set[str] = set()
        for rank, row in enumerate(rows, 1):
            child_chunk_id = str(row.get("child_chunk_id") or "").strip()
            if not child_chunk_id or child_chunk_id in seen:
                continue
            seen.add(child_chunk_id)
            raw_score = row.get("score")
            original_score = raw_score if isinstance(raw_score, (int, float)) else None
            contributions.setdefault(child_chunk_id, []).append(
                RRFContribution(
                    source=str(source), original_rank=rank, original_score=original_score
                )
            )
            scores[child_chunk_id] = scores.get(child_chunk_id, 0.0) + 1.0 / (k + rank)

    ordered = sorted(scores, key=lambda child_id: (-scores[child_id], child_id))
    if limit is not None:
        ordered = ordered[:limit]
    return tuple(
        FusedCandidate(
            child_chunk_id=child_id,
            rrf_score=scores[child_id],
            rrf_rank=rank,
            contributions=tuple(contributions[child_id]),
        )
        for rank, child_id in enumerate(ordered, 1)
    )


__all__ = ["FusedCandidate", "RRFContribution", "reciprocal_rank_fusion"]
