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


class RerankRuntimeBlocked(RuntimeError):
    """Raised when strict evaluation cannot produce a valid rerank result."""


@dataclass(frozen=True, slots=True)
class RerankerResult:
    candidates: tuple[dict[str, Any], ...]
    trace_candidates: tuple[dict[str, Any], ...]
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
        allow_fallback: bool = True,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("reranker timeout must be positive")
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._provider_name = provider_name
        self._allow_fallback = allow_fallback

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
            if not self._allow_fallback:
                raise RerankRuntimeBlocked("provider_unavailable")
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
                if not self._allow_fallback:
                    raise RerankRuntimeBlocked("invalid_provider_result")
                return self._fallback(baseline[:limit], started, "invalid_provider_result")
            scored.sort(key=lambda item: (-item[2], item[0]))
            trace_candidates = tuple(
                {**item[1], "rerank_rank": rank}
                for rank, item in enumerate(scored, 1)
            )
            final = tuple(item[1] for item in scored[:limit])
            return RerankerResult(
                candidates=final,
                trace_candidates=trace_candidates,
                enabled=True,
                provider=self._provider_name,
                latency_ms=(time.perf_counter() - started) * 1000,
                candidate_count=len(baseline),
                final_count=len(final),
                fallback_reason=None,
            )
        except RerankRuntimeBlocked:
            raise
        except TimeoutError:
            if not self._allow_fallback:
                raise RerankRuntimeBlocked("timeout")
            return self._fallback(baseline[:limit], started, "timeout")
        except Exception:
            if not self._allow_fallback:
                raise RerankRuntimeBlocked("provider_failure")
            return self._fallback(baseline[:limit], started, "provider_failure")

    def _fallback(
        self, baseline: tuple[dict[str, Any], ...], started: float, reason: str
    ) -> RerankerResult:
        return RerankerResult(
            candidates=baseline,
            trace_candidates=tuple(
                {**candidate, "rerank_rank": rank}
                for rank, candidate in enumerate(baseline, 1)
            ),
            enabled=False,
            provider=self._provider_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            candidate_count=len(baseline),
            final_count=len(baseline),
            fallback_reason=reason,
        )


__all__ = ["RerankProvider", "RerankRuntimeBlocked", "RerankerResult", "RerankerRuntime"]
