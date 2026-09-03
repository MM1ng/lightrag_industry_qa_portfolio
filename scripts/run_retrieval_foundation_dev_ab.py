"""Run the Development-only A0/A1/A2 retrieval smoke evaluation."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
from evaluation.experiments.phase4.rerank.dashscope_reranker import DashScopeQwen3Reranker
from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index
from industrial_rag.services.reranker_runtime_adapter import DashScopeRuntimeAdapter
from industrial_rag.services.retrieval_ab_evaluation import (
    EvaluationBlocked,
    FrozenGeneration,
    load_development_cases,
    map_expected_evidence,
    run_ab_evaluation,
)

def _load_mapping(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, list[str]] = {}
    for entry in raw.get("entries", []):
        if entry.get("mapped"):
            mapping[str(entry["gold_chunk_id"])] = [str(item) for item in entry.get("mapped_child_ids", [])]
    return mapping


def _load_v2_label_mapping(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for audit in raw.get("label_audits", []):
        if str(audit.get("status")) not in {"EXACT", "EQUIVALENT"}:
            raise EvaluationBlocked(f"label audit is not reliable for {audit.get('question_id')}")
        for item in audit.get("v2_candidate_evidence", []):
            historical_id = str(item["historical_chunk_id"])
            result.setdefault(historical_id, []).append(str(item["v2_chunk_id"]))
    return {key: list(dict.fromkeys(values)) for key, values in result.items()}


def _load_frozen_chunk_records(generation: FrozenGeneration) -> dict[str, dict[str, object]]:
    path = generation.workspace / "retrieval" / "child_chunks.jsonl"
    records: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        child_id = str(row.get("chunk_id") or "").strip()
        if not child_id or child_id in records:
            raise EvaluationBlocked(f"invalid frozen child identity: {child_id}")
        records[child_id] = row
    if frozenset(records) != generation.chunk_ids:
        raise EvaluationBlocked("frozen child records do not match generation chunk universe")
    return records


def _build_dashscope_runtime_provider(
    *, generation: FrozenGeneration, model: str | None, timeout_seconds: float
) -> tuple[DashScopeRuntimeAdapter, DashScopeQwen3Reranker]:
    env_file = ROOT.parent / "lightrag_industry_qa_portfolio" / ".env"
    load_dotenv(env_file, override=False)
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise EvaluationBlocked("reranker configuration missing: DASHSCOPE_API_KEY")
    resolved_model = (model or "qwen3-rerank").strip()
    if resolved_model != "qwen3-rerank":
        raise EvaluationBlocked(
            f"reranker model is not the existing exact model qwen3-rerank: {resolved_model}"
        )
    provider = DashScopeQwen3Reranker(
        api_key=api_key,
        timeout=timeout_seconds,
        config_hash="development-ab-runtime",
        commit="unknown",
    )
    return (
        DashScopeRuntimeAdapter(
            provider=provider,
            chunk_records=_load_frozen_chunk_records(generation),
        ),
        provider,
    )


def _load_baseline(path: Path) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        result[str(row["case_id"])] = [
            {
                "child_chunk_id": item.get("chunk_id"),
                "score": item.get("score"),
                "rank": item.get("rank"),
                "source_file": item.get("file"),
                "page": item.get("page"),
            }
            for item in row.get("retrieved", [])
        ]
    return result


def _translate_baseline_ids(
    baseline: dict[str, list[dict[str, object]]], generation: FrozenGeneration
) -> dict[str, list[dict[str, object]]]:
    """Translate historical P0 IDs to the frozen V2 IDs without silent drops."""
    historical_by_key: dict[tuple[str, str, int | None], list[dict[str, object]]] = defaultdict(list)
    for path in (ROOT / "evaluation/experiments/parser_backend/P0").glob("*/child_chunks.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            historical_by_key[(str(row.get("chunk_id")), str(row.get("document_name")), row.get("page_start"))].append(row)
    frozen_records = [
        json.loads(line)
        for line in (generation.workspace / "retrieval" / "child_chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    def normalized(value: object) -> str:
        return " ".join(str(value or "").casefold().split())
    frozen_by_content: dict[tuple[str, int | None, str], list[str]] = defaultdict(list)
    for row in frozen_records:
        frozen_by_content[(str(row.get("document_name")), row.get("page_start"), normalized(row.get("content")))].append(str(row["chunk_id"]))
    translated: dict[str, list[dict[str, object]]] = {}
    for question, rows in baseline.items():
        output: list[dict[str, object]] = []
        for row in rows:
            old_id = str(row.get("child_chunk_id") or "")
            if old_id in generation.chunk_ids:
                candidates = [old_id]
            else:
                historical = historical_by_key.get((old_id, str(row.get("source_file")), row.get("page")), [])
                candidates = []
                for old in historical:
                    candidates.extend(
                        frozen_by_content.get(
                            (str(old.get("document_name")), old.get("page_start"), normalized(old.get("content"))),
                            [],
                        )
                    )
                candidates = list(dict.fromkeys(candidates))
            if len(candidates) != 1:
                raise EvaluationBlocked(
                    f"baseline candidate {old_id} for {question} maps to {len(candidates)} frozen chunks"
                )
            item = dict(row)
            item["historical_child_chunk_id"] = old_id
            item["child_chunk_id"] = candidates[0]
            output.append(item)
        translated[question] = output
    return translated


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# Retrieval Foundation Development A/B Evaluation",
        "",
        f"**Status:** `{report['status']}`  ",
        f"**Downstream QA allowed:** `{report['downstream_qa_allowed']}`  ",
        f"**Scope:** `{report['scope']}`  ",
        f"**Question count:** `{report['sample_size']}` (sample-size limitation: `{report['sample_size_limitation']}`)",
        f"**Baseline mode:** `{report['baseline_mode']}`",
        f"**Latency note:** {report['latency_measurement_note']}",
        "",
        "## Aggregate metrics",
        "",
        "| Variant | Recall@5 | Recall@10 | MRR@5 | MRR@10 | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    metrics = report["metrics"]
    latency = report["latency"]
    for variant in ("A0_lightrag", "A1_lightrag_bm25_rrf", "A2_lightrag_bm25_rrf_reranker"):
        overall = metrics[variant]["overall"]
        lines.append(
            f"| {variant} | {overall['recall@5']:.3f} | {overall['recall@10']:.3f} | {overall['mrr@5']:.3f} | {overall['mrr@10']:.3f} | {latency[variant]['p50_ms']:.1f} | {latency[variant]['p95_ms']:.1f} |"
        )
    lines.extend(["", "## Reranker", "", json.dumps(report["reranker"], ensure_ascii=False, indent=2)])
    runtime = report.get("reranker_runtime", {})
    calls = runtime.get("calls", [])
    lines.extend([
        "",
        "### Runtime evidence",
        "",
        f"- Provider/model: `{runtime.get('provider')}` / `{runtime.get('model')}`",
        f"- Endpoint mode: `{runtime.get('endpoint_mode')}`",
        f"- Fallback-free calls: `{sum(1 for call in calls if call.get('status') == 'ok')}/{len(calls)}`",
        f"- Candidate identity check: `{runtime.get('candidate_identity_check', {}).get('passed')}`",
        "",
        "```json",
        json.dumps(calls, ensure_ascii=False, indent=2),
        "```",
    ])
    lines.extend(["", "## Delta classification", "", "| Classification | Count |", "|---|---:|"])
    for name, count in sorted(report["delta_summary"].items()):
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## Baseline consistency", "", json.dumps(report.get("baseline_consistency", {}), ensure_ascii=False, indent=2), "", "## Trace integrity", "", f"- Invalid chunk IDs: `{report['trace_integrity']['invalid_chunk_ids']}`", "", "## Question IDs", "", ", ".join(report["question_ids"])])
    lines.extend(["", "Raw per-question details are stored in the adjacent JSON report. Results from six questions are pipeline smoke evidence, not a stable effectiveness claim (Sample-size limitation: n=6).", ""])
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    generation = FrozenGeneration.load(args.generation)
    cases = load_development_cases(args.dataset, args.dataset_manifest)
    historical_mapping = _load_mapping(args.evidence_mapping)
    v2_mapping = _load_v2_label_mapping(args.label_audit)
    generation_mapping = {
        gold_id: list(
            dict.fromkeys(
                v2_id
                for historical_id in historical_ids
                for v2_id in v2_mapping.get(historical_id, ())
            )
        )
        for gold_id, historical_ids in historical_mapping.items()
    }
    cases = map_expected_evidence(cases, generation_mapping, generation.chunk_ids)
    lexical = load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes())
    if lexical.child_manifest_hash != generation.child_manifest_hash:
        raise EvaluationBlocked("lexical index and generation chunk manifest differ")
    sparse_index = BM25Index.from_artifact(lexical)
    raw_baseline = _load_baseline(args.lightrag_results)
    baseline_by_question: dict[str, list[dict[str, object]]] = {}
    for case in cases:
        case_id = str(case["id"])
        if case_id not in raw_baseline:
            raise EvaluationBlocked(f"LightRAG baseline is missing case: {case_id}")
        baseline_by_question[str(case.get("question") or "")] = raw_baseline[case_id]
    baseline = _translate_baseline_ids(
        baseline_by_question,
        generation,
    )
    reranker_adapter, reranker_provider = _build_dashscope_runtime_provider(
        generation=generation,
        model=args.reranker_model,
        timeout_seconds=2.0,
    )

    async def retriever(question: str, _top_k: int):
        if question not in baseline:
            raise EvaluationBlocked(f"LightRAG baseline is missing question: {question}")
        return baseline[question]

    report = await run_ab_evaluation(
        cases=cases,
        generation=generation,
        sparse_index=sparse_index,
        lightrag_retriever=retriever,
        reranker_provider=reranker_adapter,
        reranker_provider_name=reranker_provider.summary()["provider"],
        reranker_model=reranker_provider.model,
    )
    calls = list(reranker_provider.calls)
    invalid_call_ids = sorted(
        {
            str(child_id)
            for call in calls
            for child_id in call.get("candidate_ids", [])
            if str(child_id) not in generation.chunk_ids
        }
    )
    report["reranker_runtime"] = {
        "provider": reranker_provider.summary()["provider"],
        "model": reranker_provider.model,
        "endpoint": reranker_provider.endpoint,
        "endpoint_mode": reranker_provider.endpoint_mode,
        "config_source": str((ROOT.parent / "lightrag_industry_qa_portfolio" / ".env").resolve()),
        "api_key_present": True,
        "timeout_seconds": 2.0,
        "request_schema": "DashScope text-rerank: model/input.query/input.documents/parameters.top_n",
        "response_schema": reranker_provider.schema_summary,
        "calls": calls,
        "candidate_identity_check": {
            "generation_id": generation.generation_id,
            "invalid_candidate_ids": invalid_call_ids,
            "passed": not invalid_call_ids,
        },
    }
    if invalid_call_ids:
        raise EvaluationBlocked("reranker returned candidates outside frozen generation")
    for item in report["per_question"]:
        ranks = item["expected_evidence_ranks"]
        a1_rank = ranks["A1_lightrag_bm25_rrf"]
        a2_rank = ranks["A2_lightrag_bm25_rrf_reranker"]
        item["a1_vs_a2_rank_delta"] = (
            None if a1_rank is None or a2_rank is None else a1_rank - a2_rank
        )
    previous_path = ROOT / "evaluation/retrieval_foundation/development_ab_evaluation_v2_2026-09-02.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else None
    baseline_consistency = {"checked": previous is not None, "passed": None, "mismatches": []}
    if previous is not None:
        for variant in ("A0_lightrag", "A1_lightrag_bm25_rrf"):
            if report["metrics"][variant] != previous["metrics"].get(variant):
                baseline_consistency["mismatches"].append(variant)
        baseline_consistency["passed"] = not baseline_consistency["mismatches"]
    report["baseline_consistency"] = baseline_consistency
    if baseline_consistency["passed"] is False:
        report["status"] = "REGRESSION"
        report["final_status"] = "REGRESSION"
    elif report["reranker"]["fallback_count"] == 0 and all(
        call.get("status") == "ok" for call in calls
    ):
        report["status"] = "RERANKER_READY_AND_AB_COMPLETE"
        report["final_status"] = "RERANKER_READY_AND_AB_COMPLETE"
        report["downstream_qa_allowed"] = False
    report["baseline_mode"] = "original_lightrag_result_replay"
    report["latency_measurement_note"] = (
        "A0 latency is local replay overhead from the frozen original LightRAG result file; "
        "it is not a live remote LightRAG service latency benchmark."
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_cases.jsonl")
    parser.add_argument("--dataset-manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/development_dataset_manifest.json")
    parser.add_argument("--evidence-mapping", type=Path, default=ROOT / "evaluation/experiments/parser_backend/fixed_model/comparison/evidence_mapping_p0.json")
    parser.add_argument("--label-audit", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_label_audit_v2.json")
    parser.add_argument("--lightrag-results", type=Path, default=ROOT / "evaluation/experiments/parser_backend/retrieval/pymupdf_qdrant/results.jsonl")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--reranker-provider", default="external")
    parser.add_argument("--reranker-model")
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except (EvaluationBlocked, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "question_ids": report["question_ids"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
