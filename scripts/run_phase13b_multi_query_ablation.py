"""Run the offline Phase 13B multi-query candidate recall ablation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402
from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.lightrag_service import QueryOptions, _extract_retrieved  # noqa: E402
from industrial_rag.services.expanded_development_dataset import canonical_dataset_fingerprint  # noqa: E402
from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index  # noqa: E402
from industrial_rag.services.multi_query_ablation import QueryVariant, run_a3_candidates  # noqa: E402
from industrial_rag.services.retrieval_ab_evaluation import EvaluationBlocked, FrozenGeneration  # noqa: E402
from industrial_rag.services.retrieval_evaluation import evaluate_rankings  # noqa: E402
from industrial_rag.vector_collections import VectorBackend  # noqa: E402
from run_formal_retrieval_effectiveness import EXPECTED_FINGERPRINT, EXPECTED_GENERATION, _build_dashscope_runtime_provider, preflight  # noqa: E402

OUTPUT_DATE = "2026-09-03"
PHASE13A_MULTI_MISS_IDS = ("S014", "S015", "S006", "S003", "S016", "S011")


def parse_variant_response(raw: str) -> tuple[QueryVariant, ...]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    payload = json.loads(text)
    queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(queries, list) or not 2 <= len(queries) <= 3:
        raise ValueError("query expansion must return 2 to 3 queries")
    cleaned: list[str] = []
    for query in queries:
        value = str(query or "").strip()
        if value and value not in cleaned:
            cleaned.append(value)
    if not 2 <= len(cleaned) <= 3:
        raise ValueError("query expansion must return 2 to 3 unique queries")
    return tuple(QueryVariant(f"variant_{index}", query) for index, query in enumerate(cleaned, 1))


def validate_experiment_identity(
    identity: dict[str, Any], *, expected_fingerprint: str, expected_count: int
) -> None:
    if identity.get("fingerprint") != expected_fingerprint:
        raise ValueError("dataset fingerprint mismatch")
    if identity.get("question_count") != expected_count:
        raise ValueError("dataset question count mismatch")


def select_phase13a_multi_misses(report: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for item in report.get("per_question", []):
        variant = item.get("variants", {}).get("A2_lightrag_bm25_rrf_reranker", {})
        if not variant.get("complete_coverage_at_10", False):
            selected.append(str(item["id"]))
    return [item for item in PHASE13A_MULTI_MISS_IDS if item in selected]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _a2_rankings(saved: dict[str, Any]) -> list[list[str]]:
    return [
        [str(row["child_chunk_id"]) for row in item["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"]]
        for item in saved["per_question"]
    ]


def _cases_for_metrics(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **case,
            "question_type": case.get("question_type"),
            "relevant_chunk_ids": list(case["expected_child_chunk_ids"]),
            "source_document": case["source_document_id"],
        }
        for case in cases
    ]


def _multi_complete(rankings: list[list[str]], cases: list[dict[str, Any]], k: int) -> dict[str, Any]:
    multi = [(case, ranking) for case, ranking in zip(cases, rankings, strict=True) if len(case["expected_child_chunk_ids"]) > 1]
    complete = sum(set(case["expected_child_chunk_ids"]) <= set(ranking[:k]) for case, ranking in multi)
    return {"n": len(multi), "complete": complete, "rate": complete / len(multi) if multi else 0.0}


def _expansion_prompt(case: dict[str, Any]) -> str:
    question_type = str(case.get("question_type") or "工业手册检索")
    return (
        "你是工业设备技术文档检索词生成器。仅为下面问题生成 2 到 3 条不同检索角度的中文 query，"
        "不要回答问题，不要添加文档不存在的事实。优先覆盖参数/限制、操作步骤/前置条件、故障原因/措施、"
        "相关组件或术语表达中的不同角度。保持专有型号、零件号、单位和数字不变。"
        '只返回 JSON：{"queries":["...","..."]}。\n'
        f"问题类型：{question_type}\n问题：{case['question']}"
    )


async def _expand(service: Any, case: dict[str, Any]) -> tuple[QueryVariant, ...]:
    raw = await service._backend.generate(
        case["question"], "", _expansion_prompt(case), response_format={"type": "json_object"}
    )
    return (QueryVariant("original", str(case["question"])),) + parse_variant_response(raw)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases, generation, preflight_report = preflight(args.dataset, args.manifest, args.mapping, args.generation)
    saved = _read_json(args.a2_report)
    validate_experiment_identity(saved["dataset_identity"], expected_fingerprint=EXPECTED_FINGERPRINT, expected_count=24)
    if saved["generation_identity"]["generation_id"] != EXPECTED_GENERATION or saved.get("validation_or_holdout_accessed", False):
        raise EvaluationBlocked("saved A2 identity or split integrity mismatch")
    if select_phase13a_multi_misses(saved) != list(PHASE13A_MULTI_MISS_IDS):
        raise EvaluationBlocked("saved A2 does not identify the expected six Phase 13A multi-evidence misses")
    metrics_cases = _cases_for_metrics(cases)
    lexical = load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes())
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
    reranker_adapter, reranker_provider = _build_dashscope_runtime_provider(
        generation=generation, model="qwen3-rerank", timeout_seconds=2.0
    )
    a3_runs: list[Any] = []
    expansion_records: list[dict[str, Any]] = []

    async def dense(query: str, limit: int) -> list[dict[str, Any]]:
        payload = await service._backend.aquery_data(
            query, QueryOptions(mode="mix", top_k=limit, chunk_top_k=20, enable_rerank=False)
        )
        return _extract_retrieved(payload)

    try:
        for case in cases:
            variants = await _expand(service, case)
            run = await run_a3_candidates(
                query_variants=variants,
                dense_retriever=dense,
                sparse_index=sparse_index,
                reranker_provider=reranker_adapter,
                generation_chunk_ids=generation.chunk_ids,
                candidate_top_n=20,
                final_top_k=10,
                rrf_k=60,
            )
            a3_runs.append(run)
            expansion_records.append({"question_id": case["question_id"], "variants": [variant.__dict__ if hasattr(variant, "__dict__") else {"variant_id": variant.variant_id, "query": variant.query} for variant in variants]})
    finally:
        await service.close()
    a3_rankings = [[row["child_chunk_id"] for row in run.final_rows] for run in a3_runs]
    a2_rankings = _a2_rankings(saved)
    ranking_metrics = evaluate_rankings(
        metrics_cases, {"A2": a2_rankings, "A3": a3_rankings},
        case_metadata=[{key: case.get(key) for key in ("difficulty", "source_document", "evidence_pattern", "question_type")} for case in metrics_cases],
    )
    missing_analysis: list[dict[str, Any]] = []
    for case, run, a2_ranking in zip(cases, a3_runs, a2_rankings, strict=True):
        if case["question_id"] not in PHASE13A_MULTI_MISS_IDS:
            continue
        expected = list(case["expected_child_chunk_ids"])
        fused_ids = [row["child_chunk_id"] for row in run.fused_rows]
        final_ids = [row["child_chunk_id"] for row in run.final_rows]
        missing_analysis.append({
            "question_id": case["question_id"],
            "a2_missing_gold": [child for child in expected if child not in a2_ranking[:10]],
            "a3_evidence": [
                {
                    "child_chunk_id": child,
                    "recovered_by_a3": child in fused_ids,
                    "first_variant": run.first_seen_by_child.get(child),
                    "pre_rerank_rank": next((index + 1 for index, value in enumerate(fused_ids) if value == child), None),
                    "final_rank": next((index + 1 for index, value in enumerate(final_ids) if value == child), None),
                    "in_final_top5": child in final_ids[:5],
                    "in_final_top10": child in final_ids[:10],
                }
                for child in expected if child not in a2_ranking[:10]
            ],
        })
    regression_count = sum(
        bool(set(a2[:10]) & set(case["expected_child_chunk_ids"])) and not bool(set(a3[:10]) & set(case["expected_child_chunk_ids"]))
        for case, a2, a3 in zip(cases, a2_rankings, a3_rankings, strict=True)
    )
    regression_count_at5 = sum(
        bool(set(a2[:5]) & set(case["expected_child_chunk_ids"])) and not bool(set(a3[:5]) & set(case["expected_child_chunk_ids"]))
        for case, a2, a3 in zip(cases, a2_rankings, a3_rankings, strict=True)
    )
    recovered = sum(item["recovered_by_a3"] for row in missing_analysis for item in row["a3_evidence"])
    total_missing = sum(len(row["a3_evidence"]) for row in missing_analysis)
    if regression_count > 0:
        final_status = "INCONCLUSIVE"
    elif recovered > 0 and _multi_complete(a3_rankings, cases, 10)["rate"] > _multi_complete(a2_rankings, cases, 10)["rate"]:
        final_status = "MULTI_QUERY_PROMISING"
    elif recovered > 0:
        final_status = "PASS_TO_EVIDENCE_DIVERSITY"
    else:
        final_status = "MULTI_QUERY_INEFFECTIVE"
    calls = list(reranker_provider.calls)
    return {
        "final_status": final_status,
        "scope": "development_only_offline_ablation",
        "dataset_identity": {"fingerprint": canonical_dataset_fingerprint(cases), "question_count": len(cases), "split": "Development"},
        "generation_identity": {"generation_id": generation.generation_id, "corpus_fingerprint": generation.corpus_fingerprint, "child_manifest_hash": generation.child_manifest_hash},
        "a2_source_artifact": str(args.a2_report),
        "a2_definition": "frozen LightRAG + BM25 + RRF + qwen3-rerank",
        "a3_definition": "original query + 2-3 variants; candidate union/dedup; one RRF; one qwen3-rerank",
        "runtime_config": {"candidate_top_n": 20, "final_top_k": 10, "rrf_k": 60, "reranker_model": "qwen3-rerank", "reranker_calls": len(calls), "reranker_success": sum(call.get("status") == "ok" for call in calls), "reranker_fallback": sum(call.get("status") != "ok" for call in calls)},
        "metrics": ranking_metrics,
        "multi_evidence": {"A2": {"at5": _multi_complete(a2_rankings, cases, 5), "at10": _multi_complete(a2_rankings, cases, 10)}, "A3": {"at5": _multi_complete(a3_rankings, cases, 5), "at10": _multi_complete(a3_rankings, cases, 10)}},
        "query_count": {"average": mean(len(item["variants"]) for item in expansion_records), "min": min(len(item["variants"]) for item in expansion_records), "max": max(len(item["variants"]) for item in expansion_records)},
        "candidate_count": {"average_union": mean(len(run.union_child_ids) for run in a3_runs), "average_fused": mean(len(run.fused_rows) for run in a3_runs)},
        "latency_ms": {"average_total": mean(run.latency_ms for run in a3_runs), "p50_total": sorted(run.latency_ms for run in a3_runs)[len(a3_runs) // 2]},
        "regression_count": regression_count,
        "regression_count_at5": regression_count_at5,
        "phase13a_six_miss_recovery": {"questions": missing_analysis, "missing_gold_total": total_missing, "recovered_gold_total": recovered, "recovery_rate": recovered / total_missing if total_missing else 0.0},
        "query_expansions": expansion_records,
        "validation_or_holdout_accessed": False,
        "a2_retrieval_rerun": False,
        "preflight_checks": preflight_report["checks"],
    }


def _markdown(report: dict[str, Any]) -> str:
    a2 = report["metrics"]["A2"]["overall"]
    a3 = report["metrics"]["A3"]["overall"]
    lines = ["# Phase 13B — Multi-query Candidate Recall Ablation", "", f"**Final status:** `{report['final_status']}`", f"**Scope:** `{report['scope']}`", "", "## Core metrics", "", "| Metric | A2 | A3 |", "|---|---:|---:|",]
    for label, key in (("Recall@5", "recall@5"), ("Recall@10", "recall@10"), ("MRR@5", "mrr@5"), ("MRR@10", "mrr@10"), ("Question Hit@5", "question_hit@5"), ("Complete Evidence Coverage@5", "complete_evidence_coverage@5"), ("Complete Evidence Coverage@10", "complete_evidence_coverage@10")):
        lines.append(f"| {label} | {a2[key]:.3f} | {a3[key]:.3f} |")
    lines += [f"| Multi-evidence Complete@5 | {report['multi_evidence']['A2']['at5']['rate']:.3f} | {report['multi_evidence']['A3']['at5']['rate']:.3f} |", f"| Multi-evidence Complete@10 | {report['multi_evidence']['A2']['at10']['rate']:.3f} | {report['multi_evidence']['A3']['at10']['rate']:.3f} |", "", f"Regression count: `{report['regression_count']}`", f"Average query count: `{report['query_count']['average']:.2f}`", f"Average union candidates: `{report['candidate_count']['average_union']:.2f}`", f"Average latency: `{report['latency_ms']['average_total']:.1f} ms`", "", "## Phase 13A six multi-evidence misses", "", f"Recovered missing gold evidence: `{report['phase13a_six_miss_recovery']['recovered_gold_total']}/{report['phase13a_six_miss_recovery']['missing_gold_total']}`", "", "| Question | Missing gold → A3 evidence status |", "|---|---|"]
    for row in report["phase13a_six_miss_recovery"]["questions"]:
        details = "; ".join(f"{item['child_chunk_id'][:12]}…: {'recovered' if item['recovered_by_a3'] else 'MISS'} / pre-rerank={item['pre_rerank_rank'] or 'MISS'} / final={item['final_rank'] or 'MISS'} / variant={((item.get('first_variant') or {}).get('variant_id') or 'MISS')}" for item in row["a3_evidence"])
        lines.append(f"| {row['question_id']} | {details} |")
    lines += ["", "## Integrity", "", f"- Dataset fingerprint: `{report['dataset_identity']['fingerprint']}`", f"- Generation: `{report['generation_identity']['generation_id']}`", f"- Reranker calls/success/fallback: `{report['runtime_config']['reranker_calls']}/{report['runtime_config']['reranker_success']}/{report['runtime_config']['reranker_fallback']}`", "- Validation/Holdout accessed: `False`", "- A2 rerun: `False`; saved A2 artifact reused", "", "## Decision", "", "A3 uses the original query plus variants, performs candidate union/dedup and one RRF before exactly one qwen3-rerank call. No production integration or Evidence Diversity work was started.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_generation_v2")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json")
    parser.add_argument("--mapping", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json")
    parser.add_argument("--a2-report", type=Path, default=ROOT / "evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except (EvaluationBlocked, OSError, ValueError, json.JSONDecodeError, RuntimeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"final_status": report["final_status"], "recovered_gold": report["phase13a_six_miss_recovery"]["recovered_gold_total"], "regression_count": report["regression_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
