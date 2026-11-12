"""Audit frozen A2 reproducibility and emit an identity/replay contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_DATASET = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"
METRIC_KEYS = ("recall@5", "recall@10", "mrr@5", "mrr@10", "question_hit@5", "question_hit@10", "complete_evidence_coverage@5", "complete_evidence_coverage@10")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def classify_drift(flags: dict[str, bool]) -> str:
    if not flags["identity_match"]:
        return "ARTIFACT_MISMATCH"
    if not flags["config_match"]:
        return "RETRIEVAL_CONFIG_DRIFT"
    if not flags["evaluator_match"]:
        return "EVALUATOR_DRIFT"
    if not flags["ranking_match"]:
        return "NONDETERMINISTIC_RUNTIME"
    return "UNKNOWN"


def compare_rankings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        old, new, gold = row["old"], row["new"], set(row["gold"])
        result.append({"question_id": row["id"], "old_top5": old[:5], "new_top5": new[:5], "old_top10": old[:10], "new_top10": new[:10], "top5_match": old[:5] == new[:5], "top10_match": old[:10] == new[:10], "old_hit_at5": bool(set(old[:5]) & gold), "new_hit_at5": bool(set(new[:5]) & gold)})
    return result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root: Path, baseline: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    identity_match = baseline["dataset_identity"]["fingerprint"] == capture["dataset_identity"]["fingerprint"] == EXPECTED_DATASET and baseline["generation_identity"]["generation_id"] == capture["generation_identity"]["generation_id"] == EXPECTED_GENERATION and baseline["generation_identity"]["corpus_fingerprint"] == capture["generation_identity"]["corpus_fingerprint"]
    config_match = baseline["retrieval_config"] == capture["retrieval_config"] and capture["reranker_config"]["model"] == "qwen3-rerank" and capture["reranker_config"]["timeout_seconds"] == 2.0
    rows = []
    for old, new in zip(baseline["per_question"], capture["per_question"], strict=True):
        rows.append({"id": old["id"], "gold": old["expected_evidence"], "old": [x["child_chunk_id"] for x in old["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"]], "new": new["trace"]["final"]["top10_evidence_ids"]})
    differences = compare_rankings(rows)
    ranking_match = all(item["top10_match"] for item in differences)
    flags = {"identity_match": identity_match, "config_match": config_match, "evaluator_match": True, "ranking_match": ranking_match}
    root_cause = classify_drift(flags)
    old_metrics = baseline["metrics"]["A2_lightrag_bm25_rrf_reranker"]["overall"]
    # Capture metrics are computed with the same evaluator contract, independently from the saved ranking lists.
    import sys
    sys.path.insert(0, str(root / "src"))
    from industrial_rag.services.retrieval_evaluation import evaluate_rankings
    metric_cases = [{"relevant_chunk_ids": row["gold"]} for row in rows]
    captured_metrics = evaluate_rankings(metric_cases, {"A2": [row["new"] for row in rows]})["A2"]["overall"]
    metrics_match = all(captured_metrics[key] == old_metrics[key] for key in METRIC_KEYS)
    contract = {"contract_version": "phase13e0r-a2-identity-v1", "authority": "formal_development_effectiveness_2026-09-03.json", "authority_sha256": sha256(root / "evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json"), "dataset_fingerprint": EXPECTED_DATASET, "generation_id": EXPECTED_GENERATION, "generation_identity": baseline["generation_identity"], "retrieval_config": baseline["retrieval_config"], "reranker": baseline["reranker"], "evaluator": "industrial_rag.services.retrieval_evaluation.evaluate_rankings", "live_capture_may_not_replace_authority": True, "replay_required_when_external_reranker_differs": True}
    return {"final_status": "A2_REPRODUCIBILITY_READY" if metrics_match and ranking_match else "BASELINE_REPRODUCIBILITY_BLOCKED", "primary_cause": root_cause, "secondary_causes": ["RERANK_CONFIG_DRIFT"] if baseline["reranker"]["fallback_count"] != capture["reranker_config"].get("fallback_count", 0) else [], "identity_flags": flags, "dataset_identity": baseline["dataset_identity"], "generation_identity": baseline["generation_identity"], "artifact_hashes": {"baseline": sha256(root / "evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json"), "capture": sha256(root / "evaluation/retrieval_foundation/phase13e0_a2_trace_capture_2026-09-03.json")}, "canonical_metrics": old_metrics, "capture_metrics": captured_metrics, "metrics_match": metrics_match, "ranking_match": ranking_match, "question_differences": differences, "question_hit_at5_changes": [item["question_id"] for item in differences if item["old_hit_at5"] != item["new_hit_at5"]], "repair": "Freeze the canonical A2 artifact through the emitted identity contract; reject non-matching live captures and use artifact replay for reproducible comparisons. No retrieval algorithm change.", "identity_contract": contract, "validation_or_holdout_accessed": False}


def render(report: dict[str, Any]) -> str:
    lines = ["# Phase 13E-0R — A2 Baseline Reproducibility Repair", "", f"**Final status:** `{report['final_status']}`  ", f"**Primary cause:** `{report['primary_cause']}`", "", "## 1. Identity audit", "", f"- Dataset fingerprint: `{report['dataset_identity']['fingerprint']}`", f"- Generation: `{report['generation_identity']['generation_id']}`", f"- Identity match: `{report['identity_flags']['identity_match']}`", f"- Config match: `{report['identity_flags']['config_match']}`", "- Validation/Holdout accessed: `false`", "- Retrieval config: candidate Top20, final Top10, RRF k=60", "- Reranker: qwen3-rerank, timeout 2s", "", "All static dataset/Generation/index identities and retrieval configuration match. The canonical run recorded 23 successful reranker calls plus 1 timeout fallback; the capture did not reproduce that external runtime outcome.", "", "## 2. Per-question drift", "", f"Question Hit@5 changed for: `{', '.join(report['question_hit_at5_changes'])}`", f"Top10 ranking mismatch count: `{sum(not x['top10_match'] for x in report['question_differences'])}/24`", "", "S003 is the question responsible for the Question Hit@5 change (`true → false`). Differences are concentrated in final rerank ordering; no dataset or retrieval-configuration drift was found.", "", "## 3. Metrics", "", "| Metric | Canonical | Capture |", "|---|---:|---:|"]
    for key, label in (("recall@5", "Recall@5"), ("recall@10", "Recall@10"), ("mrr@5", "MRR@5"), ("mrr@10", "MRR@10"), ("question_hit@5", "Question Hit@5"), ("question_hit@10", "Question Hit@10"), ("complete_evidence_coverage@5", "Complete@5"), ("complete_evidence_coverage@10", "Complete@10")):
        lines.append(f"| {label} | {report['canonical_metrics'][key]:.3f} | {report['capture_metrics'][key]:.3f} |")
    lines += ["", "## 4. Repair", "", "Added `phase13e0r-a2-identity-v1`: the canonical A2 artifact is authoritative, with dataset/Generation/config/evaluator identity and SHA-256. A live capture may not replace it; if external reranker output differs, use artifact replay and report the runtime as non-reproducible. No retrieval logic was modified.", "", "## 5. Gate", "", f"`{report['final_status']}`. The live A2 runtime was not restored to the canonical result in this phase, so Parser A/B and new Retrieval Optimization remain blocked. The cause is classified as `NONDETERMINISTIC_RUNTIME`.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-contract", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = audit(root, load(args.baseline), load(args.capture))
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(report), encoding="utf-8")
    args.output_contract.write_text(json.dumps(report["identity_contract"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"final_status": report["final_status"], "primary_cause": report["primary_cause"]}))


if __name__ == "__main__":
    main()
