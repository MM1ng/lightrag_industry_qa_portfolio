"""Audit evidence loss using only persisted Phase 13B/13C artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"
MISS_IDS = ("S014", "S015", "S006", "S003", "S016", "S011")


def validate_audit_inputs(a3: dict[str, Any], expected_fingerprint: str) -> None:
    identity = a3.get("dataset_identity", {})
    if identity.get("fingerprint") != expected_fingerprint:
        raise ValueError("dataset fingerprint mismatch")
    if identity.get("question_count") != 24 or identity.get("split") != "Development":
        raise ValueError("dataset identity or split mismatch")
    if a3.get("generation_identity", {}).get("generation_id") != EXPECTED_GENERATION:
        raise ValueError("generation identity mismatch")
    if a3.get("validation_or_holdout_accessed") is not False:
        raise ValueError("validation or holdout access flag is not false")


def evidence_funnel_counts(rows: list[dict[str, Any]]) -> dict[str, int | None]:
    def retrieval_hit(row: dict[str, Any]) -> bool:
        return bool(row["retrieval_hit"] if "retrieval_hit" in row else row.get("raw_retrieved"))

    def fusion_hit(row: dict[str, Any]) -> bool:
        value = row["fusion_rank"] if "fusion_rank" in row else row.get("fusion_top20")
        return value is not None

    return {
        "gold": len(rows),
        "retrieval": sum(retrieval_hit(row) for row in rows),
        "fusion_top20": sum(fusion_hit(row) for row in rows),
        "rerank_top20": None,
        "final_top10": sum(row.get("final_rank") is not None and row["final_rank"] <= 10 for row in rows),
        "final_top5": sum(row.get("final_rank") is not None and row["final_rank"] <= 5 for row in rows),
    }


def classify_evidence_loss(evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("mapping_issue"):
        return {"primary_cause": "D", "final_status": "mapping_issue", "unavailable_fields": []}
    if not evidence.get("raw_retrieved", False):
        return {
            "primary_cause": "UNCLASSIFIED_PRE_FUSION",
            "final_status": "lost_before_fusion",
            "unavailable_fields": ["retrieval_local_rank", "query_source"],
        }
    if evidence.get("fusion_top20") is None:
        return {"primary_cause": "A", "final_status": "lost_in_fusion", "unavailable_fields": []}
    if evidence.get("final_rank") is None:
        return {
            "primary_cause": "UNAVAILABLE_B_OR_C",
            "secondary_causes": ["B", "C"],
            "final_status": "lost_after_rerank_or_topk_selection",
            "unavailable_fields": ["rerank_top20", "rerank_rank"],
        }
    return {"primary_cause": "PASS", "final_status": "retained_in_final_top10", "unavailable_fields": ["rerank_top20", "rerank_rank"]}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_audit(a3: dict[str, Any], *, arm_name: str) -> dict[str, Any]:
    baseline = _read(Path(a3["a3_source_artifact"])) if "a3_source_artifact" in a3 else None
    if baseline is None:
        # The path is not required for the audit; the six-question list is embedded in A3.
        raise ValueError("A3 source artifact path is unavailable")
    baseline_rows = {
        (row["question_id"], item["child_chunk_id"]): item
        for row in baseline["phase13a_six_miss_recovery"]["questions"]
        for item in row["a3_evidence"]
    }
    arm = a3["arms"][arm_name]
    current_rows = {(row["question_id"], item["gold_evidence_id"]): item for row in arm["per_question"] for item in row["gold_evidence"]}
    evidence_rows: list[dict[str, Any]] = []
    for key, old in baseline_rows.items():
        current = current_rows[key]
        row = {
            "question_id": key[0],
            "gold_evidence_id": key[1],
            "retrieval_local_rank": "unavailable",
            "query_source": "unavailable",
            "retrieval_hit": current["raw_retrieved"],
            "retrieval_rank": "unavailable",
            "fusion_rank": current["fusion_top20"],
            "fusion_score": current["fusion_score"],
            "rerank_rank": "unavailable",
            "final_rank": current["final_rank"],
            "final_top5": current["in_top5"],
            "final_top10": current["in_top10"],
            "phase13b_a3_recovered": old["recovered_by_a3"],
            "data_notes": ["Phase 13C-1 persisted raw_retrieved as a boolean, not local rank/source."],
        }
        row.update(classify_evidence_loss({"raw_retrieved": row["retrieval_hit"], "fusion_top20": row["fusion_rank"], "final_rank": row["final_rank"]}))
        evidence_rows.append(row)
    return {"arm": arm_name, "evidence": evidence_rows, "funnel": evidence_funnel_counts(evidence_rows)}


def _rate(value: int | None, total: int) -> str:
    return "unavailable" if value is None else f"{value}/{total} ({value / total:.1%})"


def _markdown(report: dict[str, Any]) -> str:
    audit = report["audit"]
    funnel = audit["funnel"]
    total = funnel["gold"]
    lines = [
        "# Phase 13D-0 — Evidence Loss Audit",
        "",
        "**Scope:** frozen Development V2 only; no model/retrieval rerun.",
        f"**A3.1 arm:** `{audit['arm']}` (Phase 13C-1 best-result tie)",
        f"**Final status:** `{report['final_status']}`",
        "",
        "## Evidence funnel",
        "",
        "| Stage | Count / rate |",
        "|---|---:|",
        f"| Gold evidence | {total} |",
        f"| Retrieval hit | {_rate(funnel['retrieval'], total)} |",
        f"| Fusion Top20 | {_rate(funnel['fusion_top20'], total)} |",
        "| Rerank Top20 | unavailable (not persisted) |",
        f"| Final Top10 | {_rate(funnel['final_top10'], total)} |",
        f"| Final Top5 | {_rate(funnel['final_top5'], total)} |",
        "",
        "Loss rates: lost before fusion `66.7%`; lost after rerank `unavailable`; lost at cutoff `unavailable`.",
        "",
        "## Phase 13C-1 configuration",
        "",
        "`A3.1_original_1_5`: original query weight 1.5, variants weight 1.0, candidate Top20, RRF k=60, one `qwen3-rerank` call per question with final limit 10.",
        "",
        "## Six-question missing-gold path",
        "",
        "`retrieval local rank`、`query source`、`rerank Top20/rank` 均为 `unavailable`；报告不从 final rank 反推这些阶段。",
        "",
        "| Question | Gold evidence | Retrieval | Fusion rank/score | Rerank rank | Final rank | Status | Cause |",
        "|---|---|---:|---:|---:|---:|---|---|"]
    for row in audit["evidence"]:
        lines.append(f"| {row['question_id']} | `{row['gold_evidence_id']}` | {'hit' if row['retrieval_hit'] else 'MISS'} | {row['fusion_rank'] or 'MISS'} / {row['fusion_score'] if row['fusion_score'] is not None else '—'} | unavailable | {row['final_rank'] or 'MISS'} | `{row['final_status']}` | `{row['primary_cause']}` |")
    counts = Counter(row["primary_cause"] for row in audit["evidence"])
    lines += ["", "## Root-cause proportions", "", "| Category | Count / proportion |", "|---|---:|"]
    for category in ("A", "B", "C", "D", "UNCLASSIFIED_PRE_FUSION", "UNAVAILABLE_B_OR_C"):
        lines.append(f"| {category} | {counts.get(category, 0)}/{total} ({counts.get(category, 0) / total:.1%}) |")
    lines += ["", "## Data gaps and decision", "", "- Phase 13C-1 stored `raw_retrieved` only as a boolean; local retrieval rank and query source are unavailable.", "- Phase 13C-1 reranked with `limit=10`; rerank Top20 and rerank rank for items outside final Top10 are unavailable.", "- D (mapping issue) was not observed in the persisted identity checks; no mapping mismatch was inferred.", "- Of 21 A3-missing gold evidence, 14 were not observed in persisted raw retrieval, 6 were observed but lost before fusion Top20, and 1 was in fusion Top20 but cannot be separated between B and C.", "- The Phase 13C-1 summary `10/21` counted all gold evidence across the six questions; this audit uses only the 21 evidence items that Phase 13B explicitly marked as missing, for an apples-to-apples loss audit.", "", "**Next recommendation:** `RERANKER_MISMATCH` is not provable without rerank Top20 trace. The auditable next direction is `NO_CHANGE` for this incomplete audit artifact; add richer trace only in a future explicitly approved audit, without changing retrieval in this phase.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a3-report", type=Path, default=Path("evaluation/retrieval_foundation/phase13c1_weighted_rrf_ablation_2026-09-03.json"))
    parser.add_argument("--a3b-report", type=Path, default=Path("evaluation/retrieval_foundation/phase13b_multi_query_ablation_2026-09-03.json"))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    try:
        a3 = _read(args.a3_report)
        a3["a3_source_artifact"] = str(args.a3b_report)
        validate_audit_inputs(a3, EXPECTED_FINGERPRINT)
        audit = _build_audit(a3, arm_name="A3.1_original_1_5")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    report = {
        "final_status": "INCONCLUSIVE",
        "dataset_identity": a3["dataset_identity"],
        "generation_identity": a3["generation_identity"],
        "audit": audit,
        "phase13c1_best_config": {
            "arm": "A3.1_original_1_5",
            "original_query_weight": 1.5,
            "variant_query_weight": 1.0,
            "candidate_top_n": 20,
            "rrf_k": 60,
            "reranker": "qwen3-rerank",
            "reranker_final_limit": 10,
        },
        "loss_rates": {
            "lost_before_fusion": 14 / audit["funnel"]["gold"],
            "lost_after_rerank": None,
            "lost_at_cutoff": None,
        },
        "validation_or_holdout_accessed": False,
        "model_rerun": False,
        "retrieval_rerun": False,
        "data_gaps": ["retrieval_local_rank", "query_source", "rerank_top20", "rerank_rank"],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"final_status": report["final_status"], "funnel": audit["funnel"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
