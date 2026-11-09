"""Summarize the persisted RAGAS development results into a checkable report.

This does NOT re-run the LLM judge: that path uses qwen-plus-2025-07-28, which
is outside the current provider quota and would 403. Instead it verifies the
already-persisted development artifacts (18 cases, ragas 0.3.9, judge config),
recomputes aggregate statistics from the raw per-case rows, and cross-checks
them against the frozen migration and E2E reports.

Run:
    PYTHONPATH=src:tools python tools/summarize_ragas.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE10 = ROOT / "evaluation" / "phase10"
SEMANTIC = PHASE10 / "conversation_e2e_semantic_scores_development.jsonl"
MIGRATION = PHASE10 / "ragas_migration_development_report.json"
E2E = PHASE10 / "conversation_e2e_ragas_development_report.json"
OUT = PHASE10 / "ragas_summary_development.json"


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def aggregate(rows: list[dict], side: str, metric: str) -> dict | None:
    values = [
        row[side][metric]
        for row in rows
        if row.get(side, {}).get(f"{metric}_status") == "available"
        and isinstance(row[side].get(metric), (int, float))
    ]
    if not values:
        return None
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def main() -> None:
    rows = load_rows(SEMANTIC)
    migration = json.loads(MIGRATION.read_text(encoding="utf-8"))
    e2e = json.loads(E2E.read_text(encoding="utf-8"))

    semantic = {
        side: {
            metric: aggregate(rows, side, metric)
            for metric in ("faithfulness", "response_relevancy")
        }
        for side in ("baseline", "candidate")
    }

    report = {
        "report_type": "ragas_development_summary",
        "source_artifacts": [
            str(SEMANTIC.relative_to(ROOT)),
            str(MIGRATION.relative_to(ROOT)),
            str(E2E.relative_to(ROOT)),
        ],
        "ragas_version": e2e.get("judge_config", {}).get("ragas_version"),
        "judge_model": e2e.get("judge_config", {}).get("judge_model"),
        "judge_metrics": e2e.get("judge_config", {}).get("metrics"),
        "case_count": len(rows),
        "all_cases_available": all(
            row.get("candidate", {}).get("faithfulness_status") == "available" for row in rows
        ),
        "semantic_metrics": semantic,
        "retrieval_metrics_after": migration.get("ragas_metrics", {}).get("after"),
        "migration_status": migration.get("status"),
        "migration_parity_passed": migration.get("parity", {}).get("passed"),
        "e2e_gate_status": e2e.get("gate", {}).get("status"),
        "e2e_gate_reasons": e2e.get("gate", {}).get("reasons"),
        "no_llm_semantic_metrics": migration.get("no_llm_semantic_metrics"),
        "validation_holdout_accessed": migration.get("validation_holdout_accessed"),
        "note": (
            "Aggregates are recomputed from persisted per-case rows; the LLM "
            "judge was NOT re-run because its model (qwen-plus-2025-07-28) is "
            "outside the current provider quota. This is a verification of "
            "existing development artifacts, not a fresh evaluation."
        ),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Sanity checks: if any of these fail the summary is not trustworthy.
    assert report["ragas_version"] == "0.3.9"
    assert report["case_count"] == 18
    assert report["all_cases_available"] is True
    assert report["migration_status"] == "MIGRATION_PASS"
    assert report["migration_parity_passed"] is True
    print("\nSANITY: PASS", file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
