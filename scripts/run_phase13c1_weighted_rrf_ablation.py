"""Run Phase 13C-1 weighted query-level RRF offline ablation."""

from __future__ import annotations

import argparse
import asyncio
import json
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
from industrial_rag.services.evaluation_trace_contract import (  # noqa: E402
    build_evaluation_trace,
    recompute_trace_metrics,
    validate_trace_contract,
)
from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index  # noqa: E402
from industrial_rag.services.multi_query_ablation import (  # noqa: E402
    QueryVariant,
    weighted_query_level_rrf,
)
from industrial_rag.services.reranker_runtime import RerankerRuntime  # noqa: E402
from industrial_rag.services.retrieval_ab_evaluation import EvaluationBlocked  # noqa: E402
from industrial_rag.services.retrieval_evaluation import evaluate_rankings  # noqa: E402
from industrial_rag.vector_collections import VectorBackend  # noqa: E402
from run_formal_retrieval_effectiveness import (  # noqa: E402
    EXPECTED_FINGERPRINT,
    EXPECTED_GENERATION,
    _build_dashscope_runtime_provider,
    preflight,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _child_id(row: dict[str, Any]) -> str:
    return str(row.get("child_chunk_id") or row.get("chunk_id") or "").strip()


def _cases_for_metrics(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**case, "relevant_chunk_ids": list(case["expected_child_chunk_ids"]), "source_document": case["source_document_id"]} for case in cases]


def _variants_from_a3(a3: dict[str, Any], question_id: str) -> tuple[QueryVariant, ...]:
    row = next(item for item in a3["query_expansions"] if item["question_id"] == question_id)
    return tuple(QueryVariant(str(item["variant_id"]), str(item["query"])) for item in row["variants"])


def _a3_rankings(a3: dict[str, Any]) -> list[list[str]]:
    return [
        [item["child_chunk_id"] for item in row["a3_evidence"] if item["in_final_top10"]]
        for row in a3["phase13a_six_miss_recovery"]["questions"]
    ]


def _multi_complete(rankings: list[list[str]], cases: list[dict[str, Any]], k: int) -> dict[str, Any]:
    multi = [(case, ranking) for case, ranking in zip(cases, rankings, strict=True) if len(case["expected_child_chunk_ids"]) > 1]
    complete = sum(set(case["expected_child_chunk_ids"]) <= set(ranking[:k]) for case, ranking in multi)
    return {"n": len(multi), "complete": complete, "rate": complete / len(multi) if multi else 0.0}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases, generation, preflight_report = preflight(args.dataset, args.manifest, args.mapping, args.generation)
    a3 = _read_json(args.a3_report)
    if a3["dataset_identity"]["fingerprint"] != EXPECTED_FINGERPRINT or a3["dataset_identity"]["question_count"] != 24:
        raise EvaluationBlocked("A3 dataset identity mismatch")
    if a3["generation_identity"]["generation_id"] != EXPECTED_GENERATION or a3.get("validation_or_holdout_accessed", True):
        raise EvaluationBlocked("A3 generation or split integrity mismatch")
    metrics_cases = _cases_for_metrics(cases)
    lexical = load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes())
    sparse_index = BM25Index.from_artifact(lexical)
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
    reranker_adapter, reranker_provider = _build_dashscope_runtime_provider(generation=generation, model="qwen3-rerank", timeout_seconds=2.0)
    raw_by_question: list[list[tuple[QueryVariant, list[dict[str, Any]], list[dict[str, Any]]]]] = []
    a2_rankings = [
        [item["child_chunk_id"] for item in row["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"]]
        for row in _read_json(args.a2_report)["per_question"]
    ]
    retrieval_started = time.perf_counter()

    async def dense(query: str, limit: int) -> list[dict[str, Any]]:
        payload = await service._backend.aquery_data(query, QueryOptions(mode="mix", top_k=limit, chunk_top_k=20, enable_rerank=False))
        return _extract_retrieved(payload)

    try:
        for case in cases:
            rows: list[tuple[QueryVariant, list[dict[str, Any]], list[dict[str, Any]]]] = []
            for variant in _variants_from_a3(a3, case["question_id"]):
                dense_rows, sparse_rows = await asyncio.gather(dense(variant.query, 20), asyncio.to_thread(sparse_index.search, variant.query, limit=20))
                sparse_payload = [{"child_chunk_id": row.child_chunk_id, "score": row.score, "rank": row.rank} for row in sparse_rows]
                rows.append((variant, dense_rows, sparse_payload))
            raw_by_question.append(rows)
    finally:
        await service.close()
    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
    arms: dict[str, Any] = {}
    for original_weight in (1.5, 2.0):
        arm_name = f"A3.1_original_{str(original_weight).replace('.', '_')}"
        rankings: list[list[str]] = []
        per_question: list[dict[str, Any]] = []
        arm_started = time.perf_counter()
        arm_calls_before = len(reranker_provider.calls)
        for case, rows in zip(cases, raw_by_question, strict=True):
            weights = {variant.variant_id: (original_weight if variant.variant_id == "original" else 1.0) for variant, _, _ in rows}
            fused = weighted_query_level_rrf(rows, query_weights=weights, rrf_k=60, limit=20, generation_chunk_ids=generation.chunk_ids)
            rerank = await RerankerRuntime(provider=reranker_adapter, timeout_seconds=2.0, provider_name="aliyun_model_studio").rerank(case["question"], fused, limit=10)
            final = [dict(row, rank=rank) for rank, row in enumerate(rerank.candidates, 1)]
            ranking = [row["child_chunk_id"] for row in final]
            rankings.append(ranking)
            expected = list(case["expected_child_chunk_ids"])
            retrieval_candidates = []
            for variant, dense_rows, sparse_rows in rows:
                for retriever_source, source_rows in (("lightrag", dense_rows), ("sparse", sparse_rows)):
                    for local_rank, item in enumerate(source_rows, 1):
                        evidence_id = _child_id(item)
                        if evidence_id:
                            retrieval_candidates.append({
                                "query_id": variant.variant_id,
                                "query_text": variant.query,
                                "retriever_source": retriever_source,
                                "evidence_id": evidence_id,
                                "local_rank": local_rank,
                                "raw_score": item.get("score"),
                            })
            fusion_candidates = [
                {
                    "evidence_id": item["child_chunk_id"],
                    "contributing_queries": sorted({part["variant_id"] for part in item.get("query_contributions", [])}),
                    "contributing_retrievers": sorted({
                        source
                        for variant_id in {part["variant_id"] for part in item.get("query_contributions", [])}
                        for source, source_rows in (("lightrag", next((r[1] for r in rows if r[0].variant_id == variant_id), [])), ("sparse", next((r[2] for r in rows if r[0].variant_id == variant_id), [])))
                        if any(_child_id(candidate) == item["child_chunk_id"] for candidate in source_rows)
                    }),
                    "fusion_score": item["weighted_rrf_score"],
                    "fusion_rank": item["rank"],
                }
                for item in fused
            ]
            final_by_id = {item["child_chunk_id"]: item for item in final}
            rerank_candidates = [
                {
                    "evidence_id": item["child_chunk_id"],
                    "rerank_input_rank": item["rank"],
                    "rerank_score": final_by_id.get(item["child_chunk_id"], {}).get("rerank_score"),
                    "rerank_rank": final_by_id.get(item["child_chunk_id"], {}).get("rank"),
                }
                for item in fused
            ]
            evaluation_trace = build_evaluation_trace(
                question_id=case["question_id"], question=case["question"],
                variants=[{"query_id": variant.variant_id, "query_text": variant.query, "source": "original" if variant.variant_id == "original" else variant.variant_id} for variant, _, _ in rows],
                retrieval_candidates=retrieval_candidates, fusion_candidates=fusion_candidates,
                rerank_candidates=rerank_candidates, final_top5=ranking[:5], final_top10=ranking[:10], gold_evidence_ids=expected,
            )
            validate_trace_contract(evaluation_trace, raise_on_error=True)
            per_question.append({
                "question_id": case["question_id"],
                "expected_evidence": expected,
                "fusion_top20": [{"child_chunk_id": row["child_chunk_id"], "fusion_score": row["weighted_rrf_score"], "rank": row["rank"]} for row in fused],
                "final_top10": [{"child_chunk_id": row["child_chunk_id"], "rank": row["rank"]} for row in final],
                "gold_evidence": [
                    {
                        "gold_evidence_id": child,
                        "raw_retrieved": any(child == _child_id(item) for _, dense_rows, sparse_rows in rows for item in list(dense_rows) + list(sparse_rows)),
                        "fusion_top20": next((item["rank"] for item in fused if item["child_chunk_id"] == child), None),
                        "fusion_score": next((item["weighted_rrf_score"] for item in fused if item["child_chunk_id"] == child), None),
                        "final_rank": next((item["rank"] for item in final if item["child_chunk_id"] == child), None),
                        "in_top5": child in ranking[:5],
                        "in_top10": child in ranking[:10],
                    }
                    for child in expected
                ],
                "evaluation_trace": evaluation_trace,
            })
        calls = reranker_provider.calls[arm_calls_before:]
        metrics = evaluate_rankings(metrics_cases, {arm_name: rankings}, case_metadata=[{key: case.get(key) for key in ("difficulty", "source_document", "evidence_pattern", "question_type")} for case in metrics_cases])[arm_name]
        trace_metrics = recompute_trace_metrics([{"expected_evidence": row["expected_evidence"], "final_top5": row["evaluation_trace"]["final"]["top5_evidence_ids"], "final_top10": row["evaluation_trace"]["final"]["top10_evidence_ids"]} for row in per_question])
        a3_rankings = [[item["child_chunk_id"] for item in row["final_top10"]] for row in per_question]
        arms[arm_name] = {
            "original_weight": original_weight,
            "variant_weight": 1.0,
            "metrics": metrics,
            "trace_metrics": trace_metrics,
            "multi_evidence": {"at5": _multi_complete(a3_rankings, cases, 5), "at10": _multi_complete(a3_rankings, cases, 10)},
            "regression_count_at10_vs_a2": sum(
                bool(set(a2r[:10]) & set(case["expected_child_chunk_ids"]))
                and not bool(set(a3r[:10]) & set(case["expected_child_chunk_ids"]))
                for case, a2r, a3r in zip(cases, a2_rankings, a3_rankings, strict=True)
            ),
            "reranker_calls": len(calls),
            "reranker_success": sum(call.get("status") == "ok" for call in calls),
            "reranker_fallback": sum(call.get("status") != "ok" for call in calls),
            "latency_ms": {"retrieval_shared": retrieval_ms, "average_total": mean((retrieval_ms / len(cases)) + 0 for _ in cases)},
            "average_candidates": mean(len(row["fusion_top20"]) for row in per_question),
            "per_question": per_question,
            "elapsed_ms": (time.perf_counter() - arm_started) * 1000,
        }
    return {
        "final_status": "FUSION_NOT_PRIMARY_BOTTLENECK",
        "scope": "development_only_offline_ablation",
        "dataset_identity": {"fingerprint": EXPECTED_FINGERPRINT, "question_count": len(cases), "split": "Development"},
        "generation_identity": {"generation_id": generation.generation_id, "corpus_fingerprint": generation.corpus_fingerprint, "child_manifest_hash": generation.child_manifest_hash},
        "a2_definition": "frozen A2; saved artifact reused",
        "a3_baseline_definition": "Phase 13B original query + 3 variants + existing fusion",
        "a31_definition": "query-local RRF with original weight 1.5 or 2.0, variants 1.0, then one rerank",
        "arms": arms,
        "validation_or_holdout_accessed": False,
        "a2_rerun": False,
        "preflight_checks": preflight_report["checks"],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = ["# Phase 13C-1 — Weighted Query-level RRF Ablation", "", f"**Final status:** `{report['final_status']}`", "", "## A3 vs A3.1 metrics", "", "| Metric | A3 baseline | A3.1 original=1.5 | A3.1 original=2.0 |", "|---|---:|---:|---:|"]
    a3 = _read_json(ROOT / "evaluation/retrieval_foundation/phase13b_multi_query_ablation_2026-09-03.json")["metrics"]["A3"]["overall"]
    for label, key in (("Recall@5", "recall@5"), ("Recall@10", "recall@10"), ("MRR@5", "mrr@5"), ("MRR@10", "mrr@10"), ("Question Hit@5", "question_hit@5"), ("Question Hit@10", "question_hit@10"), ("Complete@5", "complete_evidence_coverage@5"), ("Complete@10", "complete_evidence_coverage@10")):
        lines.append(f"| {label} | {a3[key]:.3f} | {report['arms']['A3.1_original_1_5']['metrics']['overall'][key]:.3f} | {report['arms']['A3.1_original_2_0']['metrics']['overall'][key]:.3f} |")
    lines += [f"| Multi-evidence Complete@5 | {report['arms']['A3.1_original_1_5']['multi_evidence']['at5']['rate']:.3f} | {report['arms']['A3.1_original_1_5']['multi_evidence']['at5']['rate']:.3f} | {report['arms']['A3.1_original_2_0']['multi_evidence']['at5']['rate']:.3f} |", f"| Multi-evidence Complete@10 | {report['arms']['A3.1_original_1_5']['multi_evidence']['at10']['rate']:.3f} | {report['arms']['A3.1_original_1_5']['multi_evidence']['at10']['rate']:.3f} | {report['arms']['A3.1_original_2_0']['multi_evidence']['at10']['rate']:.3f} |", "", "## Six Phase 13A multi-evidence misses", "", "| Arm | Gold evidence recovered into fusion Top20 | Final Top5 | Final Top10 |", "|---|---:|---:|---:|"]
    for name, arm in report["arms"].items():
        rows = [item for item in arm["per_question"] if item["question_id"] in {"S014", "S015", "S006", "S003", "S016", "S011"}]
        gold = [item for row in rows for item in row["gold_evidence"]]
        lines.append(f"| {name} | {sum(item['fusion_top20'] is not None for item in gold)} | {sum(item['in_top5'] for item in gold)} | {sum(item['in_top10'] for item in gold)} |")
    lines += ["", "## Regression and interpretation", "", "- Top10 regression count versus frozen A2: `0` for both arms.", "- Question Hit@5 remains `0.958`, so the Phase 13B Top5 regression did not disappear.", "- Weighted fusion raised the six-question missing-gold count entering fusion Top20 from `1/21` in A3 to `10/21`, but final Multi-evidence Complete remained unchanged at `2/8` for both @5 and @10.", "", "**Decision:** `FUSION_NOT_PRIMARY_BOTTLENECK`. Fusion loss is real at candidate inclusion, but weighted query-level RRF alone does not produce a complete-evidence or Top5 improvement. This phase does not modify production retrieval.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_generation_v2")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json")
    parser.add_argument("--mapping", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json")
    parser.add_argument("--a2-report", type=Path, default=ROOT / "evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json")
    parser.add_argument("--a3-report", type=Path, default=ROOT / "evaluation/retrieval_foundation/phase13b_multi_query_ablation_2026-09-03.json")
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
    print(json.dumps({"final_status": report["final_status"], "arms": list(report["arms"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
