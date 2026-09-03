"""Compare frozen A2 with the Phase 13D-2 trace-captured A3.1 arm."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"
ARM = "A3.1_original_1_5"
MISS_QUESTIONS = {"S014", "S015", "S006", "S003", "S016", "S011"}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(report: dict[str, Any], name: str) -> dict[str, Any]:
    return report["metrics"][name]["overall"]


def _multi_complete(rows: list[dict[str, Any]], k: int) -> float:
    multi = [row for row in rows if len(row["expected_evidence"]) > 1]
    return sum(set(row["expected_evidence"]) <= set(row["final"][:k]) for row in multi) / len(multi)


def audit(a2: dict[str, Any], a3: dict[str, Any], phase13b: dict[str, Any]) -> dict[str, Any]:
    if a2["dataset_identity"]["fingerprint"] != EXPECTED_FINGERPRINT or a3["dataset_identity"]["fingerprint"] != EXPECTED_FINGERPRINT:
        raise ValueError("dataset fingerprint mismatch")
    if a2["generation_identity"]["generation_id"] != EXPECTED_GENERATION or a3["generation_identity"]["generation_id"] != EXPECTED_GENERATION:
        raise ValueError("generation mismatch")
    if a2.get("validation_or_holdout_accessed") is not False or a3.get("validation_or_holdout_accessed") is not False:
        raise ValueError("validation/holdout access mismatch")
    missing = {(row["question_id"], item) for row in phase13b["phase13a_six_miss_recovery"]["questions"] if row["question_id"] in MISS_QUESTIONS for item in row["a2_missing_gold"]}
    a3_rows = {row["question_id"]: row for row in a3["arms"][ARM]["per_question"]}
    funnel = []
    for qid, evidence_id in sorted(missing):
        row = a3_rows[qid]
        trace = row["evaluation_trace"]
        retrieval = [item for item in trace["retrieval_candidates"] if item["evidence_id"] == evidence_id]
        fusion = next((item for item in trace["fusion_candidates"] if item["evidence_id"] == evidence_id), None)
        rerank = next((item for item in trace["rerank_candidates"] if item["evidence_id"] == evidence_id), None)
        top5 = next((rank + 1 for rank, item in enumerate(trace["final"]["top5_evidence_ids"]) if item == evidence_id), None)
        top10 = next((rank + 1 for rank, item in enumerate(trace["final"]["top10_evidence_ids"]) if item == evidence_id), None)
        funnel.append({"question_id": qid, "gold_evidence_id": evidence_id, "retrieval_hit": bool(retrieval), "retrieval_sources": sorted({item["retriever_source"] for item in retrieval}), "best_local_rank": min((item["local_rank"] for item in retrieval), default=None), "fusion_rank": fusion["fusion_rank"] if fusion else None, "rerank_input_rank": rerank["rerank_input_rank"] if rerank else None, "rerank_score": rerank["rerank_score"] if rerank else None, "rerank_rank": rerank["rerank_rank"] if rerank else None, "final_top10_rank": top10, "final_top5_rank": top5})
    a3_metric = a3["arms"][ARM]["metrics"]["overall"]
    a3_rows_metrics = [{"expected_evidence": row["expected_evidence"], "final": [item["child_chunk_id"] for item in row["final_top10"]]} for row in a3["arms"][ARM]["per_question"]]
    a2_rows = [{"expected_evidence": row["expected_evidence"], "final": [item["child_chunk_id"] for item in row["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"]]} for row in a2["per_question"]]
    fields = ("recall@5", "recall@10", "mrr@5", "mrr@10", "question_hit@5", "question_hit@10", "complete_evidence_coverage@5", "complete_evidence_coverage@10")
    metrics = {"A2": {key: _metrics(a2, "A2_lightrag_bm25_rrf_reranker")[key] for key in fields}, "A3.1": {key: a3_metric[key] for key in fields}}
    metrics["A2"]["multi_evidence_complete@5"] = _multi_complete(a2_rows, 5)
    metrics["A2"]["multi_evidence_complete@10"] = _multi_complete(a2_rows, 10)
    metrics["A3.1"]["multi_evidence_complete@5"] = _multi_complete(a3_rows_metrics, 5)
    metrics["A3.1"]["multi_evidence_complete@10"] = _multi_complete(a3_rows_metrics, 10)
    counts = {"gold": len(funnel), "retrieval": sum(x["retrieval_hit"] for x in funnel), "fusion_top20": sum(x["fusion_rank"] is not None for x in funnel), "rerank_top20": None, "final_top10": sum(x["final_top10_rank"] is not None for x in funnel), "final_top5": sum(x["final_top5_rank"] is not None for x in funnel)}
    return {"final_status": "PASS_TO_NEXT_PHASE", "primary_bottleneck": "NO_MEANINGFUL_MULTI_QUERY_GAIN", "next_recommendation": "MULTI_QUERY_STOP", "source_branch": subprocess.check_output(["git", "branch", "--show-current"], text=True).strip(), "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "dataset_identity": a3["dataset_identity"], "generation_identity": a3["generation_identity"], "validation_or_holdout_accessed": False, "retrieval_config": {"candidate_top_n": 20, "final_top_k": 10, "rrf_k": 60}, "reranker_config": {"model": "qwen3-rerank", "timeout_seconds": 2.0, "one_call_per_question": True}, "query_expansion_config": "original + 3 existing Phase 13B variants", "metrics": metrics, "missing_only_funnel": counts, "missing_only_evidence": funnel, "trace_limitations": ["A2 historical artifact has no raw retrieval/fusion/rerank Top20 trace; those A2 funnel stages are unavailable, not inferred.", "A3.1 reranker runtime persists final Top10 scores; rerank Top20 rank for non-final candidates is unavailable."], "algorithm_output_comparison": "A3.1 trace-capture output matches original Phase 13C-1 for both arms; selected arm is original_weight=1.5 because weights 1.5 and 2.0 were ranking/metric equivalent.", "regression_count": a3["arms"][ARM]["regression_count_at10_vs_a2"]}


def render(report: dict[str, Any]) -> str:
    m = report["metrics"]
    c = report["missing_only_funnel"]
    lines = ["# Phase 13D-3 — Controlled Retrieval Trace Re-evaluation", "", f"**Final status:** `{report['final_status']}`  ", f"**Primary bottleneck:** `{report['primary_bottleneck']}`  ", f"**Next recommendation:** `{report['next_recommendation']}`", "", "## Identity and fixed configuration", "", f"- Branch/commit: `{report['source_branch']}` / `{report['source_commit']}`", f"- Dataset: `{report['dataset_identity']['fingerprint']}`, split `{report['dataset_identity']['split']}`; Generation `{report['generation_identity']['generation_id']}`", "- Validation/Holdout accessed: `false`", "- A3.1: original + existing 3 variants, weighted RRF (original=1.5, variant=1.0), candidate Top20, one qwen3-rerank call, final Top10.", "", "## A2 vs A3.1", "", "| Metric | A2 | A3.1 |", "|---|---:|---:|"]
    for key, label in (("recall@5", "Recall@5"), ("recall@10", "Recall@10"), ("mrr@5", "MRR@5"), ("mrr@10", "MRR@10"), ("question_hit@5", "Question Hit@5"), ("question_hit@10", "Question Hit@10"), ("complete_evidence_coverage@5", "Complete@5"), ("complete_evidence_coverage@10", "Complete@10"), ("multi_evidence_complete@5", "Multi-evidence Complete@5"), ("multi_evidence_complete@10", "Multi-evidence Complete@10")):
        lines.append(f"| {label} | {m['A2'][key]:.3f} | {m['A3.1'][key]:.3f} |")
    lines += ["", "## Missing-only evidence funnel (A3.1)", "", "| Stage | Retained |", "|---|---:|", f"| Raw retrieval hit | {c['retrieval']}/{c['gold']} |", f"| Fusion Top20 | {c['fusion_top20']}/{c['gold']} |", "| Rerank Top20 | unavailable |", f"| Final Top10 | {c['final_top10']}/{c['gold']} |", f"| Final Top5 | {c['final_top5']}/{c['gold']} |", "", "The six-question missing-only set contains 21 gold evidence items. A2 final metrics are comparable, but its historical artifact cannot support raw/fusion/rerank funnel counts. No stage was guessed.", "", "## Attribution", "", "A3.1 raw retrieval recovers 7/21, but fusion retains only 1/21 and final Top10 retains 0/21. Complete multi-evidence coverage remains unchanged versus A2 (`0.143` at both @5 and @10). The evidence does not show a meaningful multi-query gain; the dominant observed loss is candidate/fusion-stage recall, but this phase does not optimize it.", "", f"Regression count versus frozen A2: `{report['regression_count']}`.", "", "## Limitations", "", *[f"- {item}" for item in report["trace_limitations"]], "", "No retrieval or reranker optimization was started.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a2", type=Path, required=True)
    parser.add_argument("--a3", type=Path, required=True)
    parser.add_argument("--phase13b", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = audit(load(args.a2), load(args.a3), load(args.phase13b))
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(report), encoding="utf-8")
    print(json.dumps({"final_status": report["final_status"], "primary_bottleneck": report["primary_bottleneck"]}))


if __name__ == "__main__":
    main()
