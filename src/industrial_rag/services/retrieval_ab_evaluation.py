"""Development-only A/B retrieval evaluation contracts and runner.

This module intentionally has no dependency on the QA application service.  It
executes the three retrieval variants against one already-frozen generation and
keeps the LightRAG baseline as a first-class source for A0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from industrial_rag.services.generation_artifacts import (
    GenerationArtifactError,
    generation_artifact_evidence,
)
from industrial_rag.services.lexical_retrieval import BM25Index
from industrial_rag.services.reranker_runtime import RerankerRuntime, RerankProvider
from industrial_rag.services.rrf_fusion import reciprocal_rank_fusion


class EvaluationBlocked(RuntimeError):
    """The requested evaluation cannot be run without violating its contract."""


class Variant(StrEnum):
    A0 = "A0_lightrag"
    A1 = "A1_lightrag_bm25_rrf"
    A2 = "A2_lightrag_bm25_rrf_reranker"


@dataclass(frozen=True, slots=True)
class VariantConfig:
    sparse_enabled: bool
    rrf_enabled: bool
    reranker_enabled: bool


@dataclass(frozen=True, slots=True)
class FrozenGeneration:
    workspace: Path
    generation_id: str
    child_manifest_hash: str
    chunk_ids: frozenset[str]
    corpus_fingerprint: str

    @classmethod
    def load(cls, workspace: Path) -> FrozenGeneration:
        workspace = Path(workspace).resolve()
        try:
            evidence = generation_artifact_evidence(workspace)
        except GenerationArtifactError as error:
            raise EvaluationBlocked(f"invalid frozen generation: {error}") from error
        markers = list(workspace.rglob("industrial_rag_index.json"))
        text_chunk_files = [path for path in workspace.rglob("kv_store_text_chunks.json") if path.is_file()]
        if not markers or not text_chunk_files:
            raise EvaluationBlocked("LightRAG workspace marker or text-chunk store is missing")
        if not any(path.stat().st_size > 2 for path in text_chunk_files):
            raise EvaluationBlocked("LightRAG workspace text-chunk store is empty")
        chunk_ids = frozenset(str(row["chunk_id"]) for row in evidence.records)
        if len(chunk_ids) != len(evidence.records):
            raise EvaluationBlocked("frozen generation contains duplicate child identities")
        source_fingerprints = "".join(str(item.get("file_hash", "")) for item in evidence.manifest.documents)
        corpus_fingerprint = hashlib.sha256(
            (source_fingerprints + evidence.manifest.child_manifest_hash + evidence.manifest.parent_snapshot_hash).encode("ascii")
        ).hexdigest()
        return cls(
            workspace=workspace,
            generation_id=evidence.manifest.generation_id,
            child_manifest_hash=evidence.manifest.child_manifest_hash,
            chunk_ids=chunk_ids,
            corpus_fingerprint=corpus_fingerprint,
        )


def build_variant_plan() -> dict[Variant, VariantConfig]:
    return {
        Variant.A0: VariantConfig(False, False, False),
        Variant.A1: VariantConfig(True, True, False),
        Variant.A2: VariantConfig(True, True, True),
    }


def assert_development_only(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if not rows:
        raise ValueError("Development dataset must not be empty")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        question_id = str(row.get("id") or row.get("question_id") or "").strip()
        split = str(row.get("split") or "").casefold().strip()
        if not question_id:
            raise ValueError("Development dataset rows require question id")
        if not split:
            raise ValueError("Development dataset rows require an explicit split")
        if split != "development":
            raise ValueError("Development-only evaluation rejects non-Development split")
        if question_id in seen:
            raise ValueError(f"duplicate Development question id: {question_id}")
        seen.add(question_id)
        item = dict(row)
        item["id"] = question_id
        item["split"] = split
        normalized.append(item)
    return tuple(normalized)


def map_expected_evidence(
    cases: Sequence[Mapping[str, Any]],
    mapping: Mapping[str, Sequence[str]],
    chunk_ids: set[str] | frozenset[str],
) -> tuple[dict[str, Any], ...]:
    mapped_cases: list[dict[str, Any]] = []
    for case in cases:
        expected: list[str] = []
        for value in case.get("relevant_chunk_ids", ()):
            source_id = str(value).strip()
            targets = [str(item).strip() for item in mapping.get(source_id, ()) if str(item).strip()]
            if not targets or any(item not in chunk_ids for item in targets):
                raise EvaluationBlocked(f"unmapped expected evidence: {source_id}")
            expected.extend(targets)
        if not expected:
            raise EvaluationBlocked(f"question {case.get('id')} has no mapped evidence")
        item = dict(case)
        item["relevant_chunk_ids"] = list(dict.fromkeys(expected))
        mapped_cases.append(item)
    return tuple(mapped_cases)


def audit_label_compatibility(
    cases: Sequence[Mapping[str, Any]],
    historical_targets: Mapping[str, Sequence[str]],
    historical_chunks: Mapping[str, Mapping[str, Any]],
    v2_records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Map labels by source location and text, never by retrieval results."""
    by_document: dict[str, list[Mapping[str, Any]]] = {}
    for record in v2_records:
        by_document.setdefault(str(record.get("document_name") or ""), []).append(record)
    audits: list[dict[str, Any]] = []
    for case in cases:
        historical_ids = [str(value) for value in case.get("relevant_chunk_ids", ())]
        candidates: list[dict[str, Any]] = []
        statuses: list[str] = []
        reasons: list[str] = []
        for historical_id in historical_ids:
            old = historical_chunks.get(historical_id)
            if old is None:
                statuses.append("MISSING")
                reasons.append(f"historical chunk {historical_id} not found")
                continue
            old_doc = str(old.get("document_name") or "")
            old_page = old.get("page_start")
            old_content = _normal_text(old.get("content"))
            pool = [
                record
                for record in by_document.get(old_doc, ())
                if _page_overlaps(old_page, record.get("page_start"), record.get("page_end"))
            ]
            exact = [record for record in pool if str(record.get("chunk_id")) == historical_id]
            equivalent = [record for record in pool if old_content and old_content == _normal_text(record.get("content"))]
            if exact:
                matched, status, reason = exact, "EXACT", "chunk identity is unchanged"
            elif equivalent:
                matched, status, reason = equivalent, "EQUIVALENT", "same document/page and identical evidence text"
            elif len(pool) == 1:
                matched, status, reason = pool, "EQUIVALENT", "single document/page candidate"
            elif not pool:
                matched, status, reason = [], "MISSING", "no candidate in same document/page"
            else:
                matched, status, reason = pool, "AMBIGUOUS", "multiple candidates in same document/page"
            statuses.append(status)
            reasons.append(reason)
            candidates.extend(
                {"historical_chunk_id": historical_id, "v2_chunk_id": str(item.get("chunk_id")), "status": status}
                for item in matched
            )
        overall = "MISSING" if "MISSING" in statuses else "AMBIGUOUS" if "AMBIGUOUS" in statuses else "EXACT" if statuses and all(item == "EXACT" for item in statuses) else "EQUIVALENT"
        audits.append({"question_id": str(case.get("id") or case.get("question_id") or ""), "historical_evidence_identity": historical_ids, "v2_candidate_evidence": candidates, "status": overall, "confidence": 1.0 if overall == "EXACT" else 0.85 if overall == "EQUIVALENT" else 0.0, "reason": "; ".join(reasons)})
    return tuple(audits)


def _normal_text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _page_overlaps(old_page: object, start: object, end: object) -> bool:
    if old_page is None or start is None or end is None:
        return False
    try:
        return int(start) <= int(old_page) <= int(end)
    except (TypeError, ValueError):
        return False


def load_development_cases(dataset_path: Path, manifest_path: Path) -> tuple[dict[str, Any], ...]:
    """Load six labeled cases through an explicit Development provenance manifest."""
    try:
        dataset_rows = [
            json.loads(line)
            for line in Path(dataset_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvaluationBlocked(f"Development dataset is unreadable: {error}") from error
    expected_sha = str(manifest.get("source_sha256") or "").strip().lower()
    actual_sha = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    if not expected_sha or expected_sha != actual_sha:
        raise EvaluationBlocked("Development dataset fingerprint does not match provenance manifest")
    if str(manifest.get("split") or "").casefold() != "development":
        raise ValueError("dataset provenance manifest is not Development")
    bindings = manifest.get("question_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(dataset_rows):
        raise EvaluationBlocked("Development provenance manifest does not bind every question")
    rows: list[dict[str, Any]] = []
    for row, binding in zip(dataset_rows, bindings, strict=True):
        if not isinstance(binding, Mapping) or str(binding.get("question") or "") != str(row.get("question") or ""):
            raise EvaluationBlocked("Development question text does not match provenance manifest")
        item = dict(row)
        item["id"] = str(binding.get("id") or "")
        item["split"] = "development"
        rows.append(item)
    return assert_development_only(rows)


LightRAGRetriever = Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]]]]


@dataclass(frozen=True, slots=True)
class _VariantRun:
    ranked_ids: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    latency_ms: float
    fallback_reason: str | None = None


async def run_ab_evaluation(
    *,
    cases: Sequence[Mapping[str, Any]],
    generation: FrozenGeneration,
    sparse_index: BM25Index,
    lightrag_retriever: LightRAGRetriever,
    reranker_provider: RerankProvider | None = None,
    reranker_provider_name: str = "external",
    reranker_model: str | None = None,
    reranker_timeout_seconds: float = 2.0,
    allow_reranker_fallback: bool = True,
    candidate_top_n: int = 20,
    final_top_k: int = 10,
    rrf_k: int = 60,
) -> dict[str, Any]:
    checked_cases = assert_development_only(cases)
    if candidate_top_n <= 0 or final_top_k <= 0:
        raise ValueError("candidate_top_n and final_top_k must be positive")
    variant_runs: dict[str, list[_VariantRun]] = {variant.value: [] for variant in Variant}
    rerank_fallback_count = 0
    for case in checked_cases:
        question = str(case.get("question") or "")
        dense_started = time.perf_counter()
        dense_raw = await lightrag_retriever(question, candidate_top_n)
        dense_latency = (time.perf_counter() - dense_started) * 1000
        dense_rows = _normalize_rows(dense_raw, generation.chunk_ids, source="lightrag")
        variant_runs[Variant.A0.value].append(
            _VariantRun(
                ranked_ids=tuple(row["child_chunk_id"] for row in dense_rows[:final_top_k]),
                rows=tuple(dense_rows[:final_top_k]),
                latency_ms=dense_latency,
            )
        )
        sparse_started = time.perf_counter()
        sparse_results = await asyncio.to_thread(sparse_index.search, question, limit=candidate_top_n)
        sparse_rows = [
            {"child_chunk_id": item.child_chunk_id, "score": item.score, "rank": item.rank, "source": "sparse"}
            for item in sparse_results
        ]
        fused = reciprocal_rank_fusion(
            {"lightrag": dense_rows, "sparse": sparse_rows}, k=rrf_k, limit=candidate_top_n
        )
        fused_rows = [
            {
                "child_chunk_id": item.child_chunk_id,
                "score": item.rrf_score,
                "rrf_score": item.rrf_score,
                "rank": item.rrf_rank,
                "source": "rrf",
                "contributions": [
                    {
                        "source": c.source,
                        "original_rank": c.original_rank,
                        "original_score": c.original_score,
                    }
                    for c in item.contributions
                ],
            }
            for item in fused
        ]
        a1_latency = (time.perf_counter() - sparse_started) * 1000 + dense_latency
        variant_runs[Variant.A1.value].append(
            _VariantRun(
                ranked_ids=tuple(row["child_chunk_id"] for row in fused_rows[:final_top_k]),
                rows=tuple(fused_rows[:final_top_k]),
                latency_ms=a1_latency,
            )
        )
        rerank_started = time.perf_counter()
        rerank_result = await RerankerRuntime(
            provider=reranker_provider,
            timeout_seconds=reranker_timeout_seconds,
            provider_name=reranker_provider_name,
            allow_fallback=allow_reranker_fallback,
        ).rerank(question, fused_rows, limit=final_top_k)
        rerank_fallback_count += int(rerank_result.fallback_reason is not None)
        a2_rows = [dict(row) for row in rerank_result.candidates]
        for rank, row in enumerate(a2_rows, 1):
            row["rank"] = rank
        if reranker_model:
            for row in a2_rows:
                row["rerank_model"] = reranker_model
        variant_runs[Variant.A2.value].append(
            _VariantRun(
                ranked_ids=tuple(row["child_chunk_id"] for row in a2_rows),
                rows=tuple(a2_rows),
                latency_ms=a1_latency + (time.perf_counter() - rerank_started) * 1000,
                fallback_reason=rerank_result.fallback_reason,
            )
        )
    return _build_report(
        checked_cases,
        generation,
        variant_runs,
        rerank_fallback_count,
        reranker_provider_name=reranker_provider_name,
        reranker_model=reranker_model,
        reranker_timeout_seconds=reranker_timeout_seconds,
        candidate_top_n=candidate_top_n,
        final_top_k=final_top_k,
        rrf_k=rrf_k,
    )


def _normalize_rows(rows: Sequence[Mapping[str, Any]], chunk_ids: frozenset[str], *, source: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rank, row in enumerate(rows, 1):
        child_id = str(row.get("child_chunk_id") or row.get("chunk_id") or "").strip()
        if not child_id or child_id in seen or child_id not in chunk_ids:
            continue
        seen.add(child_id)
        item = dict(row)
        item["child_chunk_id"] = child_id
        item["rank"] = rank
        item["source"] = source
        normalized.append(item)
    return normalized


def _build_report(
    cases: Sequence[Mapping[str, Any]],
    generation: FrozenGeneration,
    runs: Mapping[str, Sequence[_VariantRun]],
    fallback_count: int,
    *,
    reranker_provider_name: str,
    reranker_model: str | None,
    reranker_timeout_seconds: float,
    candidate_top_n: int,
    final_top_k: int,
    rrf_k: int,
) -> dict[str, Any]:
    from industrial_rag.services.retrieval_evaluation import evaluate_rankings

    rankings = {name: [run.ranked_ids for run in values] for name, values in runs.items()}
    case_metadata = [
        {
            "difficulty": case.get("difficulty"),
            "source_document": case.get("source_document") or case.get("source_document_id"),
            "evidence_pattern": case.get("evidence_pattern"),
            "question_type": case.get("question_type"),
        }
        for case in cases
    ]
    metrics = evaluate_rankings(cases, rankings, case_metadata=case_metadata)
    per_question: list[dict[str, Any]] = []
    invalid_trace_ids: set[str] = set()
    for index, case in enumerate(cases):
        relevant = set(str(item) for item in case.get("relevant_chunk_ids", ()))
        a0, a1, a2 = (runs[variant.value][index] for variant in Variant)
        for run in (a0, a1, a2):
            invalid_trace_ids.update(set(run.ranked_ids) - generation.chunk_ids)
        a0_rank = _best_rank(a0, relevant)
        a1_rank = _best_rank(a1, relevant)
        a2_rank = _best_rank(a2, relevant)
        classifications: list[str] = []
        sparse_contribution_ids = {
            row["child_chunk_id"]
            for row in a1.rows
            if any(item.get("source") == "sparse" for item in row.get("contributions", []))
        }
        if (
            not _hit(a0, relevant, 10)
            and _hit(a1, relevant, 10)
            and bool(sparse_contribution_ids & relevant)
        ):
            classifications.append("SPARSE_RECOVERY")
        if a0_rank is not None and a1_rank is not None and a1_rank < a0_rank:
            classifications.append("RRF_IMPROVEMENT")
        elif a0_rank is not None and (a1_rank is None or a1_rank > a0_rank):
            classifications.append("RRF_REGRESSION")
        rerank_classification = classify_rerank_delta(a1_rank, a2_rank)
        if rerank_classification is not None:
            classifications.append(rerank_classification)
        if not classifications:
            classifications.append("NO_MATERIAL_CHANGE")
        per_question.append(
            {
                "id": case["id"],
                "question": case.get("question"),
                "expected_evidence": sorted(relevant),
                "variants": {
                    variant.value: {
                        "top_results": list(run.rows),
                        "hit_at_5": _hit(run, relevant, 5),
                        "hit_at_10": _hit(run, relevant, 10),
                        "complete_coverage_at_5": relevant <= set(run.ranked_ids[:5]),
                        "complete_coverage_at_10": relevant <= set(run.ranked_ids[:10]),
                        "latency_ms": run.latency_ms,
                        "fallback_reason": run.fallback_reason,
                    }
                    for variant, run in zip(Variant, (a0, a1, a2), strict=True)
                },
                "sparse_only_contribution": sorted(set(a1.ranked_ids) - set(a0.ranked_ids)),
                "rrf_improved": _hit(a1, relevant, 10) and not _hit(a0, relevant, 10),
                "rrf_regressed": _hit(a0, relevant, 10) and not _hit(a1, relevant, 10),
                "reranker_improved": _hit(a2, relevant, 10) and not _hit(a1, relevant, 10),
                "reranker_regressed": _hit(a1, relevant, 10) and not _hit(a2, relevant, 10),
                "delta_classifications": classifications,
                "expected_evidence_ranks": {
                    "A0_lightrag": a0_rank,
                    "A1_lightrag_bm25_rrf": a1_rank,
                    "A2_lightrag_bm25_rrf_reranker": a2_rank,
                },
            }
        )
    latencies = {name: [run.latency_ms for run in values] for name, values in runs.items()}
    latency_report = {
        name: {"p50_ms": _percentile(values, 0.50), "p95_ms": _percentile(values, 0.95)}
        for name, values in latencies.items()
    }
    delta_summary: dict[str, int] = {}
    for item in per_question:
        for classification in item["delta_classifications"]:
            delta_summary[classification] = delta_summary.get(classification, 0) + 1
    final_status = "INCONCLUSIVE" if len(cases) < 30 or fallback_count == len(cases) else "PASS"
    return {
        "status": final_status,
        "final_status": final_status,
        "downstream_qa_allowed": final_status == "PASS",
        "scope": "development_only",
        "sample_size": len(cases),
        "sample_size_limitation": len(cases) < 30,
        "question_ids": [str(case["id"]) for case in cases],
        "generation": {
            "generation_id": generation.generation_id,
            "child_manifest_hash": generation.child_manifest_hash,
            "corpus_fingerprint": generation.corpus_fingerprint,
            "chunk_count": len(generation.chunk_ids),
        },
        "variant_configs": {
            variant.value: {
                "sparse_enabled": config.sparse_enabled,
                "rrf_enabled": config.rrf_enabled,
                "reranker_enabled": config.reranker_enabled,
            }
            for variant, config in build_variant_plan().items()
        },
        "metrics": metrics,
        "latency": latency_report,
        "reranker": {
            "calls": len(cases),
            "success_count": len(cases) - fallback_count,
            "provider": reranker_provider_name,
            "model": reranker_model,
            "timeout_seconds": reranker_timeout_seconds,
            "external_result_determinism": "not_guaranteed",
            "fallback_count": fallback_count,
            "fallback_rate": fallback_count / len(cases) if cases else 0.0,
        },
        "retrieval_config": {
            "candidate_top_n": candidate_top_n,
            "final_top_k": final_top_k,
            "rrf_k": rrf_k,
        },
        "per_question": per_question,
        "trace_integrity": {
            "generation_id": generation.generation_id,
            "checked_candidates": True,
            "invalid_chunk_ids": len(invalid_trace_ids),
            "invalid_ids": sorted(invalid_trace_ids),
        },
        "delta_summary": delta_summary,
        "tuning_applied": False,
        "validation_or_holdout_accessed": False,
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _hit(run: _VariantRun, relevant: set[str], k: int) -> bool:
    return bool(set(run.ranked_ids[:k]) & relevant)


def _best_rank(run: _VariantRun, relevant: set[str]) -> int | None:
    for rank, child_id in enumerate(run.ranked_ids, 1):
        if child_id in relevant:
            return rank
    return None


def classify_rerank_delta(before: int | None, after: int | None) -> str | None:
    """Classify evidence rank movement, including a miss-to-hit recovery."""

    if after is not None and (before is None or after < before):
        return "RERANK_IMPROVEMENT"
    if before is not None and (after is None or after > before):
        return "RERANK_REGRESSION"
    return None


__all__ = [
    "EvaluationBlocked",
    "FrozenGeneration",
    "Variant",
    "VariantConfig",
    "assert_development_only",
    "audit_label_compatibility",
    "build_variant_plan",
    "classify_rerank_delta",
    "load_development_cases",
    "map_expected_evidence",
    "run_ab_evaluation",
]
