"""Audit all frozen-A2 missing gold evidence from a saved trace capture."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.retrieval_evaluation import evaluate_rankings  # noqa: E402

EXPECTED_FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def classify_missing_evidence(row: dict[str, Any]) -> str:
    if row.get("representation_issue"):
        return "EVIDENCE_REPRESENTATION"
    if not row.get("lightrag_hit") and not row.get("bm25_hit"):
        return "CANDIDATE_RECALL"
    if row.get("fusion_rank") is None:
        return "FUSION_LOSS"
    if row.get("rerank_rank") is None:
        return "UNRESOLVED"
    if row.get("final_top10_rank") is None:
        return "TOPK_SELECTION"
    return "UNRESOLVED"


def audit(capture: dict[str, Any], phase13b: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if capture["dataset_identity"]["fingerprint"] != EXPECTED_FINGERPRINT or capture["generation_identity"]["generation_id"] != EXPECTED_GENERATION:
        raise ValueError("identity mismatch")
    if capture.get("validation_or_holdout_accessed") is not False:
        raise ValueError("validation/holdout access mismatch")
    missing = {(row["question_id"], item) for row in phase13b["phase13a_six_miss_recovery"]["questions"] for item in row["a2_missing_gold"]}
    if len(missing) != 21:
        raise ValueError(f"missing evidence identity mismatch: {len(missing)}")
    rows = {row["question_id"]: row for row in capture["per_question"]}
    metric_cases = [{"relevant_chunk_ids": row["expected_evidence"]} for row in capture["per_question"]]
    captured_metrics = evaluate_rankings(metric_cases, {"A2": [row["trace"]["final"]["top10_evidence_ids"] for row in capture["per_question"]]})["A2"]["overall"]
    baseline_metrics = baseline["metrics"]["A2_lightrag_bm25_rrf_reranker"]["overall"]
    metric_keys = ("recall@5", "recall@10", "mrr@5", "mrr@10", "question_hit@5", "question_hit@10", "complete_evidence_coverage@5", "complete_evidence_coverage@10")
    metrics_match = all(captured_metrics[key] == baseline_metrics[key] for key in metric_keys)
    evidence: list[dict[str, Any]] = []
    for qid, evidence_id in sorted(missing):
        gold = next(item for item in rows[qid]["gold_lineage"] if item["gold_evidence_id"] == evidence_id)
        item = {"question_id": qid, "gold_evidence_id": evidence_id, "lightrag_hit": None, "lightrag_local_rank": None, "lightrag_score": None, "bm25_hit": None, "bm25_local_rank": None, "bm25_score": None, "fusion_rank": gold.get("fusion_rank"), "fusion_score": next((x.get("fusion_score") for x in rows[qid]["trace"]["fusion_candidates"] if x["evidence_id"] == evidence_id), None), "rerank_input_rank": gold.get("fusion_rank"), "rerank_score": None, "rerank_rank": gold.get("rerank_rank"), "final_top10_rank": gold.get("final_top10_rank"), "final_top5_rank": gold.get("final_top5_rank")}
        raw = [x for x in rows[qid]["trace"]["retrieval_candidates"] if x["evidence_id"] == evidence_id]
        light = [x for x in raw if x["retriever_source"] == "lightrag"]
        sparse = [x for x in raw if x["retriever_source"] == "bm25"]
        item.update({"lightrag_hit": bool(light), "lightrag_local_rank": min((x["local_rank"] for x in light), default=None), "lightrag_score": next((x.get("raw_score") for x in light), None), "bm25_hit": bool(sparse), "bm25_local_rank": min((x["local_rank"] for x in sparse), default=None), "bm25_score": next((x.get("raw_score") for x in sparse), None)})
        if gold.get("rerank_rank") is not None:
            rerank = next(x for x in rows[qid]["trace"]["rerank_candidates"] if x["evidence_id"] == evidence_id)
            item["rerank_score"] = rerank.get("rerank_score")
        item["primary_cause"] = classify_missing_evidence(item)
        evidence.append(item)
    funnel = {"missing_gold_total": len(evidence), "retrieval_hit": sum(x["lightrag_hit"] or x["bm25_hit"] for x in evidence), "fusion_retained": sum(x["fusion_rank"] is not None for x in evidence), "reranker_retained": None, "final_top10": sum(x["final_top10_rank"] is not None for x in evidence), "final_top5": sum(x["final_top5_rank"] is not None for x in evidence)}
    causes = Counter(x["primary_cause"] for x in evidence)
    questions = []
    for qid in sorted({x["question_id"] for x in evidence}):
        qrows = [x for x in evidence if x["question_id"] == qid]
        questions.append({"question_id": qid, "gold_evidence_total": len(rows[qid]["expected_evidence"]), "missing_gold_total": len(qrows), "retrieval_hit": sum(x["lightrag_hit"] or x["bm25_hit"] for x in qrows), "primary_causes": dict(Counter(x["primary_cause"] for x in qrows)), "secondary_causes": [], "dominant_failure_cause": Counter(x["primary_cause"] for x in qrows).most_common(1)[0][0]})
    return {"final_status": "TRACE_CAPTURE_COMPLETE" if metrics_match else "BLOCKED", "dataset_identity": capture["dataset_identity"], "generation_identity": capture["generation_identity"], "validation_or_holdout_accessed": False, "metrics_match_baseline": metrics_match, "captured_metrics": captured_metrics, "baseline_metrics": baseline_metrics, "funnel": funnel, "cause_counts": {key: {"count": value, "rate": value / len(evidence)} for key, value in sorted(causes.items())}, "evidence": evidence, "questions": questions, "next_recommendation": "MORE_TRACE_REQUIRED" if not metrics_match or causes["UNRESOLVED"] >= max(causes.values()) else {"CANDIDATE_RECALL": "QUERY_DECOMPOSITION_ABLATION", "FUSION_LOSS": "FUSION_ABLATION", "RERANKER_MISMATCH": "RERANKER_ABLATION", "TOPK_SELECTION": "EVIDENCE_DIVERSITY_ABLATION", "EVIDENCE_REPRESENTATION": "EVIDENCE_REPRESENTATION_FIX"}.get(causes.most_common(1)[0][0], "MORE_TRACE_REQUIRED"), "trace_limitations": ["RerankerRuntime persists only final Top10; rerank rank/score for candidates outside final Top10 is unavailable and classified UNRESOLVED.", "Capture metrics do not match frozen A2 canonical metrics; this funnel is diagnostic-only and cannot support root-cause decisions."] if not metrics_match else ["RerankerRuntime persists only final Top10; rerank rank/score for candidates outside final Top10 is unavailable and classified UNRESOLVED."]}


def render(report: dict[str, Any]) -> str:
    f, c = report["funnel"], report["cause_counts"]
    lines = ["# Phase 13E-0 — A2 Missing Evidence Root-Cause Trace", "", f"**Final status:** `{report['final_status']}`  ", f"**Next recommendation:** `{report['next_recommendation']}`", "", "## Identity", "", f"- Dataset fingerprint: `{report['dataset_identity']['fingerprint']}`; Generation: `{report['generation_identity']['generation_id']}`; split: `{report['dataset_identity']['split']}`", "- Validation/Holdout accessed: `false`", f"- Capture metrics match frozen A2 canonical metrics: `{report['metrics_match_baseline']}`", "", "## Missing-only evidence funnel (diagnostic capture)", "", "| Stage | Retained |", "|---|---:|", f"| Missing gold total | {f['missing_gold_total']} |", f"| Retrieval hit | {f['retrieval_hit']}/{f['missing_gold_total']} |", f"| Fusion retained | {f['fusion_retained']}/{f['missing_gold_total']} |", "| Reranker retained | unavailable |", f"| Final Top10 | {f['final_top10']}/{f['missing_gold_total']} |", f"| Final Top5 | {f['final_top5']}/{f['missing_gold_total']} |", "", "## Evidence-level primary causes", "", "| Cause | Count | Rate |", "|---|---:|---:|"]
    for key, item in c.items():
        lines.append(f"| {key} | {item['count']} | {item['rate']:.3f} |")
    lines += ["", "## Question-level attribution", "", "| Question | Gold total | Missing | Retrieval hit | Dominant cause |", "|---|---:|---:|---:|---|"]
    for row in report["questions"]:
        lines.append(f"| {row['question_id']} | {row['gold_evidence_total']} | {row['missing_gold_total']} | {row['retrieval_hit']} | {row['dominant_failure_cause']} |")
    lines += ["", "## Interpretation", "", "Per-evidence details, including LightRAG/BM25 local rank and score, RRF rank/score, rerank fields, and final ranks, are stored in the JSON artifact. The reranker Top20 stage is unavailable in the current runtime trace; no rerank mismatch or TopK selection cause is asserted without that field.", "", *[f"- {x}" for x in report["trace_limitations"]], "", "Because the trace-capture metrics do not match the frozen A2 canonical metrics, this run is blocked and the cause counts are not decision-grade. No optimization recommendation is authorized.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--phase13b", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = audit(load(args.capture), load(args.phase13b), load(args.baseline))
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(report), encoding="utf-8")
    print(json.dumps({"status": report["final_status"], "next_recommendation": report["next_recommendation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
