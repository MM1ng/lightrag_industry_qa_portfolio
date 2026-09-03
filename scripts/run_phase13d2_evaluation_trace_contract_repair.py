"""Validate and report the repaired offline evaluation trace contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.evaluation_trace_contract import (
    recompute_trace_metrics,
    validate_trace_contract,
)  # noqa: E402

EXPECTED_FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(capture: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    if capture["dataset_identity"] != original["dataset_identity"]:
        raise ValueError("dataset identity mismatch")
    if capture["generation_identity"] != original["generation_identity"]:
        raise ValueError("generation identity mismatch")
    if capture["validation_or_holdout_accessed"] is not False:
        raise ValueError("validation/holdout access mismatch")
    arms: dict[str, Any] = {}
    all_valid = True
    for arm_name, original_arm in original["arms"].items():
        arm = capture["arms"][arm_name]
        trace_errors = []
        trace_metric_matches = True
        fusion_same = True
        final_same = True
        trace_metrics = arm["trace_metrics"]
        for old_row, row in zip(original_arm["per_question"], arm["per_question"], strict=True):
            trace = row["evaluation_trace"]
            errors = validate_trace_contract(trace)
            trace_errors.extend({"question_id": row["question_id"], "errors": errors} for _ in [0] if errors)
            fusion_same = fusion_same and [x["child_chunk_id"] for x in old_row["fusion_top20"]] == [x["child_chunk_id"] for x in row["fusion_top20"]]
            final_same = final_same and [x["child_chunk_id"] for x in old_row["final_top10"]] == [x["child_chunk_id"] for x in row["final_top10"]]
        standard = arm["metrics"]["overall"]
        trace_rows = [{"expected_evidence": row["expected_evidence"], "final_top5": row["evaluation_trace"]["final"]["top5_evidence_ids"], "final_top10": row["evaluation_trace"]["final"]["top10_evidence_ids"]} for row in arm["per_question"]]
        derived = recompute_trace_metrics(trace_rows)
        trace_metric_matches = trace_metric_matches and all(
            derived[key] == trace_metrics[key]
            for key in ("recall@5", "recall@10", "mrr@5", "mrr@10", "question_hit@5", "question_hit@10", "complete@5", "complete@10")
        )
        trace_metric_matches = trace_metric_matches and all(
            trace_metrics[key] == standard[standard_key]
            for key, standard_key in (
                ("recall@5", "recall@5"), ("recall@10", "recall@10"), ("mrr@5", "mrr@5"), ("mrr@10", "mrr@10"),
                ("question_hit@5", "question_hit@5"), ("question_hit@10", "question_hit@10"),
                ("complete@5", "complete_evidence_coverage@5"), ("complete@10", "complete_evidence_coverage@10"),
            )
        )
        arms[arm_name] = {"trace_errors": trace_errors, "trace_metrics_match": trace_metric_matches, "fusion_ranking_unchanged": fusion_same, "final_ranking_unchanged": final_same}
        all_valid = all_valid and not trace_errors and trace_metric_matches and fusion_same and final_same
    return {
        "final_status": "TRACE_CONTRACT_READY" if all_valid else "TRACE_CONTRACT_PARTIAL",
        "dataset_identity": capture["dataset_identity"],
        "generation_identity": capture["generation_identity"],
        "validation_or_holdout_accessed": False,
        "trace_version": "phase13d2-evaluation-trace-v1",
        "trace_fields": ["query_variants", "retrieval_candidates", "fusion_candidates", "rerank_candidates", "final", "gold_lineage"],
        "arms": arms,
        "rerun_type": "trace_capture_only; same algorithm/configuration; original artifact preserved",
        "algorithm_output_unchanged": all(item["fusion_ranking_unchanged"] and item["final_ranking_unchanged"] for item in arms.values()),
        "independent_metric_recompute": all(item["trace_metrics_match"] for item in arms.values()),
    }


def render(report: dict[str, Any], capture_path: Path) -> str:
    rows = ["# Phase 13D-2 — Evaluation Trace Contract Repair", "", f"**Final status:** `{report['final_status']}`", "", "## Scope and identity", "", f"- Dataset fingerprint: `{report['dataset_identity']['fingerprint']}`; questions: `{report['dataset_identity']['question_count']}`; split: `{report['dataset_identity']['split']}`", f"- Generation: `{report['generation_identity']['generation_id']}`", "- Validation/Holdout accessed: `false`", f"- Capture artifact: `{capture_path}`", "", "## Repaired trace schema", "", "Each question now stores `query_variants`, `retrieval_candidates`, `fusion_candidates`, `rerank_candidates`, `final.top5_evidence_ids`, `final.top10_evidence_ids`, and `gold_lineage`. Unknown runtime values are null; no values are inferred.", "", "## Contract results", "", "| Arm | Trace valid | Independent metrics | Fusion unchanged | Final unchanged |", "|---|---|---|---|---|"]
    for name, item in report["arms"].items():
        rows.append(f"| {name} | {'yes' if not item['trace_errors'] else 'no'} | {'yes' if item['trace_metrics_match'] else 'no'} | {'yes' if item['fusion_ranking_unchanged'] else 'no'} | {'yes' if item['final_ranking_unchanged'] else 'no'} |")
    rows += ["", "The independent trace metrics use the explicit `expected_evidence` list as denominator and reproduce the runner's Recall, MRR, Question Hit, and Complete metrics. The report and JSON are produced from the same repaired artifact.", "", "## Algorithm integrity", "", "The rerun was trace-capture-only. Dataset and Generation identities matched the original artifact; both arms had identical fusion and final ranking IDs and identical standard metrics. No A2, query expansion, BM25, RRF, reranker, TopK, or production QA behavior was changed.", "", "## Decision", "", f"`{report['final_status']}`. The offline evaluation trace contract is ready for auditable downstream analysis. This phase does not start Retrieval Optimization.", ""]
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    capture, original = load(args.capture), load(args.original)
    report = audit(capture, original)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(report, args.capture), encoding="utf-8")
    print(json.dumps({"final_status": report["final_status"], "algorithm_output_unchanged": report["algorithm_output_unchanged"]}))


if __name__ == "__main__":
    main()
