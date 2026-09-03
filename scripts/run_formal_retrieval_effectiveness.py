"""Run the immutable 24-question Development A0/A1/A2 effectiveness evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402
from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.lightrag_service import QueryOptions, _extract_retrieved  # noqa: E402
from industrial_rag.services.expanded_development_dataset import (  # noqa: E402
    canonical_dataset_fingerprint,
    load_generation_snapshot,
)
from industrial_rag.services.generation_artifacts import generation_artifact_evidence  # noqa: E402
from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index  # noqa: E402
from industrial_rag.services.retrieval_ab_evaluation import (  # noqa: E402
    EvaluationBlocked,
    FrozenGeneration,
    run_ab_evaluation,
)
from industrial_rag.vector_collections import VectorBackend  # noqa: E402
from run_retrieval_foundation_dev_ab import _build_dashscope_runtime_provider  # noqa: E402

EXPECTED_FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"
EXPECTED_CHILD_COUNT = 453
EXPECTED_DOCS = {"doc-4ffb6df91a9a", "doc-6a9ea3ff1f42"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_dataset(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def preflight(dataset_path: Path, manifest_path: Path, mapping_path: Path, generation_path: Path) -> tuple[list[dict[str, Any]], FrozenGeneration, dict[str, Any]]:
    cases = _read_dataset(dataset_path)
    manifest = _read_json(manifest_path)
    mapping = _read_json(mapping_path)
    dataset_fingerprint = canonical_dataset_fingerprint(cases)
    checks = {
        "question_count": len(cases) == 24,
        "question_ids_unique": len({str(case.get("question_id")) for case in cases}) == len(cases),
        "dataset_fingerprint": dataset_fingerprint == EXPECTED_FINGERPRINT == manifest.get("dataset_fingerprint"),
        "evidence_mapping_complete": len(mapping) == 24 and all(mapping.get(str(case["question_id"])) for case in cases),
        "development_only": all(str(case.get("split")).casefold() == "development" for case in cases),
        "manifest_status": manifest.get("final_status") == "READY_FOR_EFFECTIVENESS_EVAL",
    }
    if not all(checks.values()):
        raise EvaluationBlocked(f"BLOCKED_EXPERIMENT_INTEGRITY dataset checks: {checks}")
    generation = FrozenGeneration.load(generation_path)
    snapshot = load_generation_snapshot(generation_path)
    evidence = generation_artifact_evidence(generation_path, expected_generation_id=EXPECTED_GENERATION)
    metadata = _read_json(generation_path / "generation_metadata.json")
    source_docs = {str(row["document_id"]) for row in evidence.records}
    checks.update({
        "generation_id": generation.generation_id == EXPECTED_GENERATION,
        "child_count": len(generation.chunk_ids) == EXPECTED_CHILD_COUNT == snapshot.children.__len__(),
        "corpus_fingerprint": metadata.get("corpus_fingerprint") == generation.corpus_fingerprint,
        "child_manifest_hash": generation.child_manifest_hash == manifest.get("child_manifest_hash"),
        "light_rag_workspace": bool(list(generation_path.joinpath("lightrag_workspace").glob("industrial_rag_index.json"))),
        "source_documents": source_docs == EXPECTED_DOCS,
        "lexical_index_identity": snapshot.lexical_index_fingerprint == manifest.get("lexical_index_fingerprint"),
        "mapping_children_exist": all(all(str(child) in generation.chunk_ids for child in case["expected_child_chunk_ids"]) for case in cases),
    })
    if not all(checks.values()):
        raise EvaluationBlocked(f"BLOCKED_EXPERIMENT_INTEGRITY generation checks: {checks}")
    return cases, generation, {"checks": checks, "dataset_fingerprint": dataset_fingerprint, "manifest": manifest}


def _cases_for_runner(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **case,
            "id": case["question_id"],
            "relevant_chunk_ids": list(case["expected_child_chunk_ids"]),
            "source_document": case["source_document_id"],
        }
        for case in cases
    ]


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases, generation, preflight_report = preflight(args.dataset, args.manifest, args.mapping, args.generation)
    runner_cases = _cases_for_runner(cases)
    lexical_payload = (generation.workspace / "retrieval" / "lexical_index.json").read_bytes()
    lexical = load_lexical_index(lexical_payload)
    sparse_index = BM25Index.from_artifact(lexical)
    env_file = ROOT.parent / "lightrag_industry_qa_portfolio" / ".env"
    load_dotenv(env_file, override=False)
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
    reranker_adapter, reranker_provider = _build_dashscope_runtime_provider(generation=generation, model="qwen3-rerank", timeout_seconds=2.0)

    async def live_lightrag(question: str, top_k: int):
        started = time.perf_counter()
        payload = await service._backend.aquery_data(question, QueryOptions(mode="mix", top_k=top_k, chunk_top_k=20, enable_rerank=False))
        rows = _extract_retrieved(payload)
        for row in rows:
            row["a0_latency_ms"] = (time.perf_counter() - started) * 1000
        return rows

    try:
        report = await run_ab_evaluation(
            cases=runner_cases,
            generation=generation,
            sparse_index=sparse_index,
            lightrag_retriever=live_lightrag,
            reranker_provider=reranker_adapter,
            reranker_provider_name="aliyun_model_studio",
            reranker_model="qwen3-rerank",
            allow_reranker_fallback=False,
        )
    finally:
        await service.close()
    calls = list(reranker_provider.calls)
    rerank_latencies = [float(call.get("latency", call.get("latency_ms", 0))) for call in calls if call.get("status") == "ok"]
    report.update({
        "experiment_metadata": {
            "name": "Formal Development Retrieval Effectiveness Evaluation",
            "run_type": "immutable_evaluation",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tuning_applied": False,
            "validation_or_holdout_accessed": False,
        },
        "dataset_identity": {"path": str(args.dataset), "fingerprint": preflight_report["dataset_fingerprint"], "question_count": len(cases)},
        "generation_identity": {"generation_id": generation.generation_id, "corpus_fingerprint": generation.corpus_fingerprint, "child_manifest_hash": generation.child_manifest_hash, "child_count": len(generation.chunk_ids), "lexical_index_fingerprint": lexical.artifact_hash},
        "integrity_checks": preflight_report["checks"],
        "reranker_runtime": {"provider": "aliyun_model_studio", "model": "qwen3-rerank", "calls": len(calls), "success_count": sum(call.get("status") == "ok" for call in calls), "fallback_count": sum(call.get("status") != "ok" for call in calls), "fallback_rate": sum(call.get("status") != "ok" for call in calls) / len(calls), "p50_latency_ms": median(rerank_latencies) if rerank_latencies else None, "p95_latency_ms": _percentile(rerank_latencies, 0.95) if rerank_latencies else None, "calls_detail": calls},
        "latency": {**report["latency"], "a2_incremental_over_a1_ms": _delta_latency(report["latency"]["A1_lightrag_bm25_rrf"], report["latency"]["A2_lightrag_bm25_rrf_reranker"])},
    })
    report["final_status"] = _final_status(report)
    report["status"] = report["final_status"]
    report["downstream_qa_allowed"] = report["final_status"] == "PASS_TO_DOWNSTREAM"
    report["regression_cases"] = [item for item in report["per_question"] if "RRF_REGRESSION" in item["delta_classifications"] or "RERANK_REGRESSION" in item["delta_classifications"]]
    report["missed_by_all"] = [item for item in report["per_question"] if not any(item["variants"][variant]["hit_at_10"] for variant in item["variants"])]
    return report


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _delta_latency(a1: dict[str, float], a2: dict[str, float]) -> dict[str, float]:
    return {key: a2[key] - a1[key] for key in ("p50_ms", "p95_ms")}


def _final_status(report: dict[str, Any]) -> str:
    if not all(report["integrity_checks"].values()):
        return "BLOCKED_EXPERIMENT_INTEGRITY"
    if report["reranker_runtime"]["fallback_count"] > 0:
        return "INCONCLUSIVE"
    if report["delta_summary"].get("RRF_REGRESSION", 0) > report["delta_summary"].get("RRF_IMPROVEMENT", 0):
        return "REGRESSION"
    if report["delta_summary"].get("RERANK_REGRESSION", 0) > report["delta_summary"].get("RERANK_IMPROVEMENT", 0):
        return "REGRESSION"
    return "PASS_TO_DOWNSTREAM"


def _markdown(report: dict[str, Any]) -> str:
    lines = ["# Formal Development Retrieval Effectiveness Evaluation", "", f"**Final status:** `{report['final_status']}`", f"**Sample size:** `{report['sample_size']}`", "", "## Aggregate metrics", "", "| Variant | Recall@5 | Recall@10 | MRR@5 | MRR@10 | Hit@5 | Hit@10 | Complete@5 | Complete@10 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for variant, values in report["metrics"].items():
        overall = values["overall"]
        lines.append(f"| {variant} | {overall['recall@5']:.3f} | {overall['recall@10']:.3f} | {overall['mrr@5']:.3f} | {overall['mrr@10']:.3f} | {overall['question_hit@5']:.3f} | {overall['question_hit@10']:.3f} | {overall['complete_evidence_coverage@5']:.3f} | {overall['complete_evidence_coverage@10']:.3f} |")
    lines += ["", "## Reranker runtime", "", json.dumps(report["reranker_runtime"], ensure_ascii=False, indent=2), "", "## Stratified metrics", "", "```json", json.dumps(report["metrics"], ensure_ascii=False, indent=2), "```", "", "## Delta analysis", "", json.dumps(report["delta_summary"], ensure_ascii=False, indent=2), "", "## Regression cases", "", json.dumps(report["regression_cases"], ensure_ascii=False, indent=2), "", "## Missed by all", "", json.dumps(report["missed_by_all"], ensure_ascii=False, indent=2), "", "## Integrity", "", json.dumps(report["integrity_checks"], ensure_ascii=False, indent=2), "", "## Answers", "", "1. A1 vs A0、A2 vs A1 的结论见 aggregate 与 delta；本报告不使用统计显著性表述。", "2. A1 的提升来源按 per-question RRF/sparse contributions 审计。", "3. Sparse Recovery 仅在 A0 miss→A1 hit 且 evidence 有 sparse contribution 时计入。", "4. RRF 与 reranker regression 已逐题列出。", "5. A2 额外延迟见 `latency.a2_incremental_over_a1_ms`。", "6. HARD 与 multi-evidence 分层见 stratified metrics。", "7. Missed by all 已单独输出。", "8. Full QA Downstream 仅在 `PASS_TO_DOWNSTREAM` 时允许。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_generation_v2")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json")
    parser.add_argument("--mapping", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except (EvaluationBlocked, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["final_status"], "sample_size": report["sample_size"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
