"""Fail-safe asynchronous external reranker boundary."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

RerankProvider = Callable[
    [str, Sequence[Mapping[str, Any]]], Awaitable[Sequence[tuple[Mapping[str, Any], float]]]
]


@dataclass(frozen=True, slots=True)
class RerankerResult:
    candidates: tuple[dict[str, Any], ...]
    enabled: bool
    provider: str
    latency_ms: float
    candidate_count: int
    final_count: int
    fallback_reason: str | None


class RerankerRuntime:
    def __init__(
        self,
        *,
        provider: RerankProvider | None,
        timeout_seconds: float = 2.0,
        provider_name: str = "custom",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("reranker timeout must be positive")
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._provider_name = provider_name

    async def rerank(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        limit: int,
    ) -> RerankerResult:
        if limit <= 0:
            raise ValueError("reranker limit must be positive")
        started = time.perf_counter()
        baseline = tuple(dict(candidate) for candidate in candidates)
        if self._provider is None:
            return self._fallback(baseline[:limit], started, "provider_unavailable")
        try:
            ranked = await asyncio.wait_for(
                self._provider(query, baseline), timeout=self._timeout_seconds
            )
            by_identity = {
                str(candidate.get("child_chunk_id") or ""): candidate for candidate in baseline
            }
            scored: list[tuple[int, dict[str, Any], float]] = []
            for index, pair in enumerate(ranked):
                if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                    continue
                candidate, score = pair
                if not isinstance(candidate, Mapping) or not isinstance(score, (int, float)):
                    continue
                child_id = str(candidate.get("child_chunk_id") or "")
                original = by_identity.get(child_id)
                if original is None:
                    continue
                enriched = dict(original)
                enriched["rerank_score"] = float(score)
                scored.append((index, enriched, float(score)))
            if not scored:
                return self._fallback(baseline[:limit], started, "invalid_provider_result")
            scored.sort(key=lambda item: (-item[2], item[0]))
            final = tuple(item[1] for item in scored[:limit])
            return RerankerResult(
                candidates=final,
                enabled=True,
                provider=self._provider_name,
                latency_ms=(time.perf_counter() - started) * 1000,
                candidate_count=len(baseline),
                final_count=len(final),
                fallback_reason=None,
            )
        except TimeoutError:
            return self._fallback(baseline[:limit], started, "timeout")
        except Exception:
            return self._fallback(baseline[:limit], started, "provider_failure")

    def _fallback(
        self, baseline: tuple[dict[str, Any], ...], started: float, reason: str
    ) -> RerankerResult:
        return RerankerResult(
            candidates=baseline,
            enabled=False,
            provider=self._provider_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            candidate_count=len(baseline),
            final_count=len(baseline),
            fallback_reason=reason,
        )


__all__ = ["RerankProvider", "RerankerResult", "RerankerRuntime"]
