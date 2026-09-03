"""Paired Development-only reranker A/B on one immutable candidate bundle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.lightrag_service import QueryOptions, _extract_retrieved  # noqa: E402
from industrial_rag.vector_collections import VectorBackend  # noqa: E402
from evaluation.experiments.phase4.rerank.dashscope_reranker import DashScopeQwen3Reranker  # noqa: E402
from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index  # noqa: E402
from industrial_rag.services.paired_rerank_ab import candidate_fingerprint, validate_paired_inputs  # noqa: E402
from industrial_rag.services.reranker_runtime import RerankerRuntime  # noqa: E402
from industrial_rag.services.reranker_runtime_adapter import DashScopeRuntimeAdapter  # noqa: E402
from industrial_rag.services.retrieval_evaluation import evaluate_rankings  # noqa: E402
from industrial_rag.services.rrf_fusion import reciprocal_rank_fusion  # noqa: E402
from run_formal_retrieval_effectiveness import _cases_for_runner, preflight  # noqa: E402
from run_retrieval_foundation_dev_ab import _load_frozen_chunk_records  # noqa: E402

GENERATION = ROOT / "evaluation/retrieval_foundation/dev_generation_v2"
DATASET = ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl"
MANIFEST = ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json"
MAPPING = ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json"
BASELINE = ROOT / "evaluation/experiments/parser_backend/retrieval/pymupdf_qdrant/results.jsonl"
OUT_JSON = ROOT / "evaluation/retrieval_foundation/phase13f1_paired_rerank_ab_2026-09-03.json"
OLD_CACHE = ROOT / "evaluation/retrieval_foundation/qwen3_rerank_paired_cache.jsonl"
NEW_CACHE = ROOT / "evaluation/retrieval_foundation/qwen37_rerank_paired_cache.jsonl"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata(cases: list[dict]) -> list[dict]:
    return [
        {
            "difficulty": case.get("difficulty"),
            "source_document": case.get("source_document_id"),
            "evidence_pattern": case.get("evidence_pattern"),
            "question_type": case.get("question_type"),
        }
        for case in cases
    ]


def _multi_metrics(cases: list[dict], rankings: list[list[str]]) -> dict[str, float | int]:
    rows = [
        (case, ranking)
        for case, ranking in zip(cases, rankings, strict=True)
        if case.get("evidence_pattern") == "multi_evidence"
    ]
    return {
        "n": len(rows),
        "complete@5": sum(
            set(case["expected_child_chunk_ids"]) <= set(ranking[:5]) for case, ranking in rows
        ) / len(rows),
        "complete@10": sum(
            set(case["expected_child_chunk_ids"]) <= set(ranking[:10]) for case, ranking in rows
        ) / len(rows),
    }


def _runtime_summary(model: str, calls: list[dict]) -> dict:
    latencies = [float(call.get("latency_ms", float(call.get("latency", 0)) * 1000)) for call in calls]
    return {
        "model": model,
        "success": sum(call.get("status") == "ok" for call in calls),
        "total": len(calls),
        "timeout_count": sum("timeout" in str(call.get("error", "")).lower() for call in calls),
        "invalid_response_count": sum(call.get("error_type") == "invalid_response" for call in calls),
        "fallback_count": sum(bool(call.get("fallback_used")) for call in calls),
        "latency_mean_ms": mean(latencies) if latencies else None,
        "latency_p50_ms": median(latencies) if latencies else None,
        "latency_p95_ms": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None,
    }


async def _run() -> dict:
    cases, generation, preflight_report = preflight(DATASET, MANIFEST, MAPPING, GENERATION)
    runner_cases = _cases_for_runner(cases)
    lexical = load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes())
    sparse_index = BM25Index.from_artifact(lexical)
    records = _load_frozen_chunk_records(generation)
    load_dotenv(ROOT.parent / "lightrag_industry_qa_portfolio" / ".env", override=False)
    settings = Settings.from_env()
    settings = settings.__class__(**{
        **{field: getattr(settings, field) for field in settings.__dataclass_fields__},
        "working_dir": generation.workspace / "lightrag_workspace",
        "vector_workspace": None,
        "vector_backend": VectorBackend.nano,
        "qdrant_generation": None,
        "sparse_retrieval_enabled": False,
        "reranker_enabled": False,
    })
    from industrial_rag.lightrag_service import LightRAGService

    service = LightRAGService(settings)
    await service.initialize()

    bundles = []
    for case in runner_cases:
        query = str(case["question"])
        dense_payload = await service._backend.aquery_data(
            query, QueryOptions(mode="mix", top_k=20, chunk_top_k=20, enable_rerank=False)
        )
        dense_rows = [dict(row) for row in _extract_retrieved(dense_payload)]
        sparse_results = await asyncio.to_thread(sparse_index.search, query, limit=20)
        sparse_rows = [
            {"child_chunk_id": item.child_chunk_id, "score": item.score, "rank": item.rank, "source": "sparse"}
            for item in sparse_results
        ]
        fused = reciprocal_rank_fusion({"lightrag": dense_rows, "sparse": sparse_rows}, k=60, limit=20)
        rows = [
            {
                "child_chunk_id": item.child_chunk_id,
                "score": item.rrf_score,
                "rrf_score": item.rrf_score,
                "rank": item.rrf_rank,
                "source": "rrf",
                "contributions": [
                    {"source": c.source, "original_rank": c.original_rank, "original_score": c.original_score}
                    for c in item.contributions
                ],
            }
            for item in fused
        ]
        for row in rows:
            row["child_text_hash"] = _text_hash(str(records[row["child_chunk_id"]]["content"]))
        ids = [row["child_chunk_id"] for row in rows]
        bundles.append(
            {
                "question_id": case["id"],
                "query": query,
                "candidate_ids": ids,
                "candidate_order": list(range(1, len(ids) + 1)),
                "candidate_text_hashes": [_text_hash(str(records[item]["content"])) for item in ids],
                "candidate_fingerprint": candidate_fingerprint(query, rows),
                "retriever_metadata": rows,
            }
        )

    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is missing")
    commit = os.popen("git rev-parse HEAD").read().strip()
    arms = {}
    try:
        for model, cache in (("qwen3-rerank", OLD_CACHE), ("qwen3.7-text-rerank", NEW_CACHE)):
            provider = DashScopeQwen3Reranker(api_key=api_key, model=model, timeout=60.0, cache_path=cache, config_hash="phase13f1", commit=commit)
            adapter = DashScopeRuntimeAdapter(provider=provider, chunk_records=records)
            rankings = []
            per_question = []
            for case, bundle in zip(runner_cases, bundles, strict=True):
                started = time.perf_counter()
                result = await RerankerRuntime(provider=adapter, timeout_seconds=60.0, provider_name="aliyun_model_studio", allow_fallback=False).rerank(query=bundle["query"], candidates=bundle["retriever_metadata"], limit=20)
                ranked = [str(row["child_chunk_id"]) for row in result.candidates]
                rankings.append(ranked)
                call = dict(provider.calls[-1])
                call.update({"question_id": case["id"], "model_name": model, "query_hash": _text_hash(bundle["query"]), "elapsed_ms": (time.perf_counter() - started) * 1000})
                per_question.append({"question_id": case["id"], "output_candidate_ids": ranked, "output_order": list(range(1, len(ranked) + 1)), "candidate_fingerprint": bundle["candidate_fingerprint"], "call": call})
            arms[model] = {"rankings": rankings, "per_question": per_question, "runtime": _runtime_summary(model, provider.calls)}
    finally:
        await service.close()

    for index, (old, new) in enumerate(zip(arms["qwen3-rerank"]["per_question"], arms["qwen3.7-text-rerank"]["per_question"], strict=True)):
        validate_paired_inputs(
            {"candidate_fingerprint": old["candidate_fingerprint"], "candidate_ids": bundles[index]["candidate_ids"]},
            {"candidate_fingerprint": new["candidate_fingerprint"], "candidate_ids": bundles[index]["candidate_ids"]},
        )

    metrics = {
        model: evaluate_rankings(runner_cases, {model: arms[model]["rankings"]}, case_metadata=_metadata(cases))[model]
        for model in arms
    }
    details = []
    for index, case in enumerate(cases):
        gold = set(case["expected_child_chunk_ids"])
        old_rank = {item: rank for rank, item in enumerate(arms["qwen3-rerank"]["rankings"][index], 1)}
        new_rank = {item: rank for rank, item in enumerate(arms["qwen3.7-text-rerank"]["rankings"][index], 1)}
        details.append({
            "question_id": case["question_id"], "difficulty": case["difficulty"], "evidence_pattern": case["evidence_pattern"],
            "gold_evidence": [{"evidence_id": item, "old_rank": old_rank.get(item), "new_rank": new_rank.get(item), "rank_delta": (None if old_rank.get(item) is None or new_rank.get(item) is None else old_rank[item] - new_rank[item])} for item in sorted(gold)],
            "old_complete@5": gold <= set(arms["qwen3-rerank"]["rankings"][index][:5]), "new_complete@5": gold <= set(arms["qwen3.7-text-rerank"]["rankings"][index][:5]),
            "old_hit@5": bool(gold & set(arms["qwen3-rerank"]["rankings"][index][:5])), "new_hit@5": bool(gold & set(arms["qwen3.7-text-rerank"]["rankings"][index][:5])),
        })
    multi = [item for item in details if item["evidence_pattern"] == "multi_evidence"]
    return {
        "final_status": "PAIRED_AB_COMPLETE",
        "dataset_identity": {"fingerprint": preflight_report["dataset_fingerprint"], "question_count": len(cases), "split": "development", "question_ids": [case["question_id"] for case in cases]},
        "generation_identity": {"generation_id": generation.generation_id, "corpus_fingerprint": generation.corpus_fingerprint, "child_manifest_hash": generation.child_manifest_hash},
        "fixed_config": {"candidate_top_n": 20, "final_top_k": 10, "rrf_k": 60, "top_n": 20, "timeout_seconds": 60.0, "strict_no_fallback": True},
        "candidate_bundle": {"question_count": len(bundles), "bundles": bundles, "fingerprints_identical_across_arms": True},
        "metrics": metrics,
        "multi_evidence": {"old": _multi_metrics(cases, arms["qwen3-rerank"]["rankings"]), "new": _multi_metrics(cases, arms["qwen3.7-text-rerank"]["rankings"]), "newly_completed": [item["question_id"] for item in multi if not item["old_complete@5"] and item["new_complete@5"]], "regressed": [item["question_id"] for item in multi if item["old_complete@5"] and not item["new_complete@5"]]},
        "runtime": {model: arms[model]["runtime"] for model in arms},
        "per_question": details,
        "artifacts": {model: arms[model]["per_question"] for model in arms},
        "validation_or_holdout_accessed": False,
        "regression_count": sum(item["old_complete@5"] and not item["new_complete@5"] for item in details),
    }


def main() -> int:
    report = asyncio.run(_run())
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["final_status"], "questions": 24, "regression_count": report["regression_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
