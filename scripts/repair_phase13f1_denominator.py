"""Offline repair of Phase 13F-1 multi-evidence denominator semantics."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.paired_rerank_ab import multi_evidence_cases  # noqa: E402
from industrial_rag.services.retrieval_evaluation import evaluate_rankings  # noqa: E402

DATASET = ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl"
ARTIFACT = ROOT / "evaluation/retrieval_foundation/phase13f1_paired_rerank_ab_2026-09-03.json"
OUT = ROOT / "evaluation/retrieval_foundation/phase13f1r_denominator_repair_2026-09-03.json"


def main() -> int:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    runner_cases = [
        {**case, "id": case["question_id"], "relevant_chunk_ids": case["expected_child_chunk_ids"]}
        for case in cases
    ]
    metadata = [
        {"difficulty": case["difficulty"], "source_document": case["source_document_id"], "evidence_pattern": case["evidence_pattern"], "question_type": case["question_type"]}
        for case in cases
    ]
    rankings = {
        model: [row["output_candidate_ids"] for row in artifact["artifacts"][model]]
        for model in ("qwen3-rerank", "qwen3.7-text-rerank")
    }
    metrics = {
        model: evaluate_rankings(runner_cases, {model: rows}, case_metadata=metadata)[model]
        for model, rows in rankings.items()
    }
    multi = multi_evidence_cases(cases)
    multi_report = {}
    for model, rows in rankings.items():
        complete5 = sum(
            set(case["expected_child_chunk_ids"]) <= set(rows[index][:5])
            for index, case in enumerate(cases)
            if case in multi
        )
        complete10 = sum(
            set(case["expected_child_chunk_ids"]) <= set(rows[index][:10])
            for index, case in enumerate(cases)
            if case in multi
        )
        multi_report[model] = {"numerator@5": complete5, "numerator@10": complete10, "denominator": len(multi), "complete@5": complete5 / len(multi), "complete@10": complete10 / len(multi)}
    result = {
        "status": "EVALUATION_CONTRACT_RESTORED",
        "source_artifact": str(ARTIFACT),
        "dataset_question_count": len(cases),
        "canonical_multi_evidence_question_ids": [case["question_id"] for case in multi],
        "canonical_multi_evidence_denominator": len(multi),
        "metrics": metrics,
        "multi_evidence": multi_report,
        "hard_complete@5": {model: metrics[model]["difficulty=HARD"]["complete_evidence_coverage@5"] for model in rankings},
        "core_metrics_recomputed_from_artifact": True,
        "models_called": False,
        "retrieval_rerun": False,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "multi_denominator": len(multi)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
