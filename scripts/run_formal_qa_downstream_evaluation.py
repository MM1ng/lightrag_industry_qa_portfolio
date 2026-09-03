"""Run the frozen Development A2 QA downstream evaluation through LightRAGService."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.services.expanded_development_dataset import (  # noqa: E402
    canonical_dataset_fingerprint,
    load_generation_snapshot,
    validate_dataset,
)
from industrial_rag.services.generation_artifacts import GenerationArtifactResolver  # noqa: E402
from industrial_rag.services.qa_downstream_evaluation import (  # noqa: E402
    aggregate_cases,
    evaluate_case,
)
from industrial_rag.services.retrieval_ab_evaluation import FrozenGeneration  # noqa: E402
from industrial_rag.vector_collections import VectorBackend  # noqa: E402
from run_formal_retrieval_effectiveness import (  # noqa: E402
    EXPECTED_FINGERPRINT,
    EXPECTED_GENERATION,
    _build_dashscope_runtime_provider,
    _read_dataset,
    _read_json,
)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _load_env() -> None:
    for path in (ROOT / ".env.local_staging", ROOT.parent / "lightrag_industry_qa_portfolio" / ".env"):
        if path.exists():
            load_dotenv(path, override=False)


def preflight(dataset_path: Path, manifest_path: Path, mapping_path: Path, generation_path: Path) -> tuple[list[dict[str, Any]], FrozenGeneration, dict[str, Any]]:
    cases = _read_dataset(dataset_path)
    manifest = _read_json(manifest_path)
    mapping = _read_json(mapping_path)
    snapshot = load_generation_snapshot(generation_path)
    errors = validate_dataset(cases, snapshot)
    checks = {
        "question_count": len(cases) == 24,
        "question_ids_match_manifest": [str(row["question_id"]) for row in cases] == manifest.get("question_ids"),
        "dataset_fingerprint": canonical_dataset_fingerprint(cases) == EXPECTED_FINGERPRINT == manifest.get("dataset_fingerprint"),
        "evidence_mapping_complete": len(mapping) == 24 and all(mapping.get(str(row["question_id"])) for row in cases),
        "development_only": all(str(row.get("split")).casefold() == "development" for row in cases),
        "dataset_manifest_ready": manifest.get("final_status") == "READY_FOR_EFFECTIVENESS_EVAL",
        "dataset_validation": not errors,
        "generation_id": snapshot.generation_id == EXPECTED_GENERATION,
        "generation_manifest_hash": bool(snapshot.child_manifest_hash),
        "source_documents": len({str(row["source_document_id"]) for row in cases}) == 2,
    }
    if not all(checks.values()):
        raise RuntimeError(f"BLOCKED_EXPERIMENT_INTEGRITY: {checks}; validation_errors={errors}")
    generation = FrozenGeneration.load(generation_path)
    checks["generation_chunk_count"] = len(generation.chunk_ids) == len(snapshot.children) == 453
    checks["generation_identity"] = generation.generation_id == snapshot.generation_id and generation.child_manifest_hash == snapshot.child_manifest_hash
    if not all(checks.values()):
        raise RuntimeError(f"BLOCKED_EXPERIMENT_INTEGRITY: {checks}")
    return cases, generation, {"checks": checks, "manifest": manifest, "fingerprint": EXPECTED_FINGERPRINT}


async def _run_case(service: Any, case: dict[str, Any], settings: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await service.query(
            str(case["question"]),
            mode=settings.phase10b_query_mode,
            top_k=settings.phase10b_top_k,
            chunk_top_k=settings.phase10b_chunk_top_k,
        )
        trace = result.retrieval_trace.to_payload() if result.retrieval_trace is not None else {}
        return {
            "question_id": case["question_id"],
            "question": case["question"],
            "difficulty": case["difficulty"],
            "question_type": case["question_type"],
            "evidence_pattern": case["evidence_pattern"],
            "source_document_id": case["source_document_id"],
            "expected_child_chunk_ids": list(case["expected_child_chunk_ids"]),
            "expected_parent_chunk_ids": list(case["expected_parent_chunk_ids"]),
            "a2": {
                "retrieved_chunk_ids": list(result.retrieval_chunk_ids),
                "selected_chunk_ids": [str(item.get("chunk_id")) for item in trace.get("final_selected_chunks", ()) if item.get("chunk_id")],
                "citations": _json_safe(list(result.citations)),
                "answer": result.answer,
                "answer_status": result.answer_status,
                "answer_points": _json_safe(list(result.answer_points)),
                "grounding_failure_categories": list(result.grounding_failure_categories),
                "metric_error": None,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "trace": trace,
            },
        }
    except Exception as error:
        return {
            "question_id": case["question_id"],
            "question": case["question"],
            "difficulty": case["difficulty"],
            "question_type": case["question_type"],
            "evidence_pattern": case["evidence_pattern"],
            "source_document_id": case["source_document_id"],
            "expected_child_chunk_ids": list(case["expected_child_chunk_ids"]),
            "expected_parent_chunk_ids": list(case["expected_parent_chunk_ids"]),
            "a2": {
                "retrieved_chunk_ids": [], "selected_chunk_ids": [], "citations": [], "answer": "",
                "answer_status": "error", "answer_points": [], "grounding_failure_categories": [],
                "metric_error": f"{type(error).__name__}: {error}", "latency_ms": (time.perf_counter() - started) * 1000,
                "trace": {},
            },
        }


def _multi_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question_id": row["question_id"],
            "expected_evidence_count": row["expected_evidence_count"],
            "retrieved_evidence_count": row["retrieved_evidence_count"],
            "retrieved_evidence_ids": row["retrieved_evidence_ids"],
            "selected_evidence_ids": row["selected_evidence_ids"],
            "cited_evidence_ids": row["cited_evidence_ids"],
            "answer_claims": row["answer_claims"],
            "unsupported_claims": row["unsupported_claims"],
            "missing_evidence": sorted(set(row["expected_evidence_ids"]) - set(row["cited_evidence_ids"])),
            "final_answer_status": row["answer_status"],
            "failure_category": row["failure"],
        }
        for row in rows
        if row["expected_evidence_count"] > 1
    ]


def _render(report: dict[str, Any]) -> str:
    overall = report["aggregate"]["overall"]
    lines = ["# QA Downstream Evaluation — Frozen Development V2", "", f"**Final status:** `{report['final_status']}`", f"**Questions:** `{report['question_count']}`", "", "## Identity and runtime", "", f"- Dataset fingerprint: `{report['dataset']['fingerprint']}`", f"- Generation: `{report['generation']['generation_id']}`", f"- A2 config: `{report['runtime_config']}`", f"- Validation/Holdout accessed: `{report['integrity']['validation_or_holdout_accessed']}`", "", "## Aggregate metrics", "", "| Metric | Value |", "|---|---:|"]
    for key, value in overall.items():
        if isinstance(value, dict) and "value" in value:
            lines.append(f"| {key} | {value['value']} |")
        elif isinstance(value, (int, float)):
            lines.append(f"| {key} | {value} |")
    lines += ["", "## Stratified metrics", "", "```json", json.dumps(report["aggregate"]["stratified"], ensure_ascii=False, indent=2), "```", "", "## Failure taxonomy", "", "```json", json.dumps(report["aggregate"]["failure_taxonomy"], ensure_ascii=False, indent=2), "```", "", "## Multi-evidence analysis", "", "```json", json.dumps(report["multi_evidence"], ensure_ascii=False, indent=2), "```", "", "## Root-cause decisions", "", "1. Retrieval A2 的提升是否传导到 QA：见 citation / supporting recall 与 per-question chain；不使用无 reference answer 的伪造 correctness。", "2. 最大失败源按 trace-based primary cause 统计，不把 evidence 已入选后的失败归因给 retrieval。", "3. False refusal 单独统计；若 evidence 已选中仍拒答，归为 FALSE_REFUSAL。", "4. Multi-evidence 必要证据未完整引用时归为 CITATION_MAPPING_FAILURE，并保留 retrieval / selection 证据。", "5. 下一阶段优先建立 QA Error Set，优先修复 evidence selection/context completeness 与 citation mapping。", "6. 当前应停止 Retrieval tuning，转入 downstream QA failure remediation；本报告不自动修复。", "", "## Per-question chain", "", "完整逐题链路（retrieval → selected context → claims → citations → final attribution）保存在同目录 JSON。", ""]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    _load_env()
    cases, generation, preflight_report = preflight(args.dataset, args.manifest, args.mapping, args.generation)
    settings = Settings.from_env()
    reranker_adapter, reranker_provider = _build_dashscope_runtime_provider(generation=generation, model="qwen3-rerank", timeout_seconds=2.0)
    from industrial_rag.lightrag_service import LightRAGService
    # The frozen evaluation bundle keeps retrieval/ and lightrag_workspace/ as
    # siblings, while LightRAGService's production contract expects both below
    # one working_dir.  Build an ephemeral, hash-validated view; never mutate
    # the frozen source directory.
    with tempfile.TemporaryDirectory(prefix="qa-downstream-runtime-") as temporary:
        runtime_view = Path(temporary)
        shutil.copytree(generation.workspace / "retrieval", runtime_view / "retrieval")
        for source in (generation.workspace / "lightrag_workspace").iterdir():
            target = runtime_view / source.name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        settings = settings.__class__(**{
            **{field: getattr(settings, field) for field in settings.__dataclass_fields__},
            "working_dir": runtime_view,
            "vector_workspace": None,
            "vector_backend": VectorBackend.nano,
            "qdrant_generation": generation.generation_id,
            "sparse_retrieval_enabled": True,
            "reranker_enabled": True,
            "reranker_timeout_seconds": 2.0,
        })
        resolver = GenerationArtifactResolver()
        registry = resolver.resolve_registry(runtime_view, expected_generation_id=generation.generation_id, expected_child_manifest_hash=generation.child_manifest_hash)
        service = LightRAGService(settings, chunk_registry=registry, reranker_provider=reranker_adapter)
        await service.initialize()
        raw: list[dict[str, Any]] = []
        try:
            for index, case in enumerate(cases, 1):
                print(f"QA_CASE {index}/{len(cases)} {case['question_id']}", flush=True)
                raw.append(await _run_case(service, case, settings))
        finally:
            await service.close()
    evaluated = [evaluate_case(row) for row in raw]
    aggregate = aggregate_cases(evaluated)
    calls = list(reranker_provider.calls)
    runtime_failures = sum(bool(row["a2"].get("metric_error")) for row in raw)
    report = {
        "final_status": "QA_BASELINE_ESTABLISHED" if len(evaluated) == 24 and runtime_failures == 0 else "BLOCKED_QA_RUNTIME",
        "question_count": len(evaluated),
        "dataset": {"path": str(args.dataset), "fingerprint": preflight_report["fingerprint"], "split": "development", "question_ids": [row["question_id"] for row in cases]},
        "generation": {"generation_id": generation.generation_id, "corpus_fingerprint": generation.corpus_fingerprint, "child_manifest_hash": generation.child_manifest_hash, "child_count": len(generation.chunk_ids)},
        "runtime_config": {"retrieval": "A2", "mode": settings.phase10b_query_mode, "top_k": settings.phase10b_top_k, "chunk_top_k": settings.phase10b_chunk_top_k, "reranker": "qwen3-rerank", "reranker_timeout_seconds": 2.0, "grounding_enabled": settings.answer_grounding_enabled, "structured_citation_enabled": settings.structured_citation_output_enabled},
        "integrity": {**preflight_report["checks"], "validation_or_holdout_accessed": False, "retrieval_tuning_applied": False, "generation_modified": False, "dataset_modified": False, "qa_path": "industrial_rag.lightrag_service.LightRAGService.query"},
        "aggregate": aggregate,
        "reranker_runtime": {"provider": "aliyun_model_studio", "model": "qwen3-rerank", "attempts": 24, "success": sum(call.get("status") == "ok" for call in calls), "fallback": sum(call.get("status") != "ok" for call in calls), "call_records": calls},
        "multi_evidence": _multi_evidence(evaluated),
        "per_question": evaluated,
        "raw_qa_records": raw,
        "failure_attribution": {"primary_causes": aggregate["failure_taxonomy"], "runtime_failures": runtime_failures},
        "answer_correctness": {"status": "unavailable", "reason": "Frozen dataset has evidence labels but no trusted reference answer labels"},
        "supported_answer_rate": {"status": "proxy", "definition": "PASS primary cause / all questions", "value": aggregate["overall"]["supported_answer_rate"]["value"]},
    }
    return report


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
        report = asyncio.run(run(args))
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render(report), encoding="utf-8")
    print(json.dumps({"status": report["final_status"], "question_count": report["question_count"]}, ensure_ascii=False))
    return 0 if report["final_status"] == "QA_BASELINE_ESTABLISHED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
