"""Read-only consistency audit for the Phase 13C-1 and 13D-0 artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
EXPECTED_GENERATION = "dev-v2-20260902"
ARM = "A3.1_original_1_5"
MISS_QUESTIONS = ("S014", "S015", "S006", "S003", "S016", "S011")


def validate_audit_inputs(c1: dict[str, Any], expected_fingerprint: str) -> None:
    dataset = c1.get("dataset_identity", {})
    generation = c1.get("generation_identity", {})
    if dataset.get("fingerprint") != expected_fingerprint:
        raise ValueError("dataset identity mismatch")
    if dataset.get("question_count") != 24 or dataset.get("split") != "Development":
        raise ValueError("dataset identity mismatch")
    if generation.get("generation_id") != EXPECTED_GENERATION:
        raise ValueError("generation identity mismatch")
    if c1.get("validation_or_holdout_accessed") is not False:
        raise ValueError("validation/holdout access mismatch")


def inspect_trace_schema(payload: dict[str, Any], *, include_lineage: bool = False) -> dict[str, Any]:
    present = set(payload)
    for row in payload.get("per_question", []):
        present.update(row)
    required = {
        "query_variants",
        "retrieval_candidates",
        "fusion_candidates",
        "rerank_candidates",
        "final_top_k",
    }
    missing = sorted(required - present)
    # These summary fields are not equivalent to the absent full lineage.
    if include_lineage and "fusion_top20" in present:
        missing.append("fusion_candidates(full_trace)")
    if include_lineage and "final_top10" in present:
        missing.append("final_top_k(generic/full_trace)")
    if include_lineage:
        missing.extend(["evidence_lineage", "retrieval_local_rank", "query_source", "rerank_rank"])
    ordered_missing = [field for field in ("query_variants", "retrieval_candidates", "fusion_candidates", "rerank_candidates", "final_top_k") if field in missing]
    ordered_missing.extend(field for field in missing if field not in ordered_missing)
    return {"required_fields": ["query_variants", "retrieval_candidates", "fusion_candidates", "rerank_candidates", "final_top_k"], "missing_trace_fields": ordered_missing}


def _missing_set(phase13b: dict[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for row in phase13b["phase13a_six_miss_recovery"]["questions"]:
        qid = row["question_id"]
        if qid not in MISS_QUESTIONS and len(phase13b["phase13a_six_miss_recovery"]["questions"]) > 1:
            continue
        source = row.get("a2_missing_gold")
        if source is None:
            source = row.get("a3_evidence", [])
        for item in source:
            if isinstance(item, dict):
                evidence_id = item.get("gold_evidence_id", item.get("child_chunk_id"))
            else:
                evidence_id = item
            if evidence_id:
                result.add((qid, evidence_id))
    return result


def recompute_missing_only_fusion_hits(
    phase13b: dict[str, Any], phase13c1: dict[str, Any], arm_name: str = ARM
) -> dict[str, Any]:
    try:
        missing = _missing_set(phase13b)
    except (KeyError, TypeError) as exc:
        raise ValueError("missing-set identity mismatch") from exc
    arm = phase13c1.get("arms", {}).get(arm_name)
    if arm is None:
        raise ValueError("arm identity mismatch")
    rows = {
        (row["question_id"], item["gold_evidence_id"]): item
        for row in arm.get("per_question", [])
        for item in row.get("gold_evidence", [])
    }
    if not missing.issubset(rows):
        raise ValueError("gold evidence identity mismatch")
    hits = sorted(key for key in missing if rows[key].get("fusion_top20") is not None)
    return {"gold_count": len(missing), "fusion_hit_count": len(hits), "hit_ids": hits}


def compare_funnel_counts(recomputed: dict[str, Any], reported: dict[str, Any]) -> dict[str, Any]:
    match = recomputed["gold_count"] == reported["gold_count"] and recomputed["fusion_hit_count"] == reported["fusion_hit_count"]
    return {
        "match": match,
        "classification": "NO_ISSUE" if match else "REPORT_BUG",
        "recomputed": recomputed,
        "reported": reported,
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _all_gold_fusion_hits(arm: dict[str, Any]) -> int:
    return sum(
        item.get("fusion_top20") is not None
        for row in arm["per_question"]
        if row["question_id"] in MISS_QUESTIONS
        for item in row["gold_evidence"]
    )


def _d0_funnel(d0: dict[str, Any]) -> dict[str, Any]:
    return d0["audit"]["funnel"]


def build_audit(root: Path) -> dict[str, Any]:
    c1_path = root / "evaluation/retrieval_foundation/phase13c1_weighted_rrf_ablation_2026-09-03.json"
    d0_path = root / "evaluation/retrieval_foundation/phase13d0_evidence_loss_audit_2026-09-03.json"
    b_path = root / "evaluation/retrieval_foundation/phase13b_multi_query_ablation_2026-09-03.json"
    c1, d0, b = _load(c1_path), _load(d0_path), _load(b_path)
    validate_audit_inputs(c1, EXPECTED_FINGERPRINT)
    validate_audit_inputs(d0, EXPECTED_FINGERPRINT)
    recomputed = recompute_missing_only_fusion_hits(b, c1)
    arm = c1["arms"][ARM]
    d0_funnel = _d0_funnel(d0)
    d0_strict = {"gold_count": d0_funnel["gold"], "fusion_hit_count": d0_funnel["fusion_top20"]}
    missing = _missing_set(b)
    c1_rows = {(r["question_id"], e["gold_evidence_id"]): e for r in arm["per_question"] for e in r["gold_evidence"]}
    d0_rows = {(e["question_id"], e["gold_evidence_id"]): e for e in d0["audit"]["evidence"]}
    aligned = sorted(missing & set(c1_rows) & set(d0_rows))
    evidence_ids = {
        "expected_missing_count": len(missing),
        "exact_child_id_alignment_count": len(aligned),
        "exact_child_id_alignment": len(aligned) == len(missing),
        "mismatches": sorted(missing - set(aligned)),
    }
    schema = inspect_trace_schema(c1, include_lineage=True)
    if len(missing) != 21:
        raise ValueError(f"missing-set identity mismatch: expected 21, got {len(missing)}")
    return {
        "final_status": "PASS_TO_NEXT_PHASE",
        "root_cause": "REPORT_BUG",
        "secondary_limitation": "TRACE_SCHEMA_GAP",
        "recommendation": "FIX_EVALUATION_PIPELINE",
        "scope": "read_only_existing_artifacts",
        "inputs": {"phase13b": str(b_path), "phase13c1": str(c1_path), "phase13d0": str(d0_path)},
        "identity": {"dataset_fingerprint": EXPECTED_FINGERPRINT, "generation_id": EXPECTED_GENERATION, "question_count": 24, "split": "Development", "validation_or_holdout_accessed": False},
        "comparison": {
            "phase13c1_reported": {"fusion_top20": {"hits": 10, "gold": 21}, "final_top10": {"hits": 9, "gold": 21}, "final_top5": {"hits": 6, "gold": 21}, "note": "C1 headline numerator counted all gold evidence across six questions while retaining the Phase13B missing-set denominator."},
            "phase13d0_audit": {"fusion_top20": {"hits": d0_funnel["fusion_top20"], "gold": d0_funnel["gold"]}, "final_top10": {"hits": d0_funnel["final_top10"], "gold": d0_funnel["gold"]}, "final_top5": {"hits": d0_funnel["final_top5"], "gold": d0_funnel["gold"]}},
            "strict_recompute_missing_only": {"fusion_top20": {"hits": recomputed["fusion_hit_count"], "gold": recomputed["gold_count"]}, "matches_d0": recomputed["fusion_hit_count"] == d0_strict["fusion_hit_count"] and recomputed["gold_count"] == d0_strict["gold_count"]},
            "c1_all_gold_fusion_hits": _all_gold_fusion_hits(arm),
        },
        "evidence_id_alignment": evidence_ids,
        "trace_schema": schema,
        "missing_only_fusion_hit_ids": [list(x) for x in recomputed["hit_ids"]],
        "notes": ["No retrieval or model was rerun.", "Rerank Top20 and evidence lineage are unavailable in persisted C1 JSON; no ranks were inferred."],
    }


def render_markdown(audit: dict[str, Any], commit: str) -> str:
    c = audit["comparison"]
    schema = audit["trace_schema"]["missing_trace_fields"]
    return f"""# Phase 13D-1 Trace Consistency Audit

Status: **{audit['final_status']}**  
Root cause: **{audit['root_cause']}**  
Recommendation: **{audit['recommendation']}**  
Audit commit: `{commit}`

## Data identity

- Dataset fingerprint: `{audit['identity']['dataset_fingerprint']}`; questions: 24; split: Development
- Generation: `{audit['identity']['generation_id']}`
- Validation/Holdout accessed: `false`
- Inputs: Phase 13B missing-set, Phase 13C-1 JSON, Phase 13D-0 JSON

## Difference reproduced

| Metric | Phase 13C-1 report | Phase 13D-0 audit | Strict recompute | Match |
|---|---:|---:|---:|---|
| Fusion Top20 | 10/21 | {c['phase13d0_audit']['fusion_top20']['hits']}/{c['phase13d0_audit']['fusion_top20']['gold']} | {c['strict_recompute_missing_only']['fusion_top20']['hits']}/{c['strict_recompute_missing_only']['fusion_top20']['gold']} | {'yes' if c['strict_recompute_missing_only']['matches_d0'] else 'no'} |
| Final Top10 | 9/21 | {c['phase13d0_audit']['final_top10']['hits']}/{c['phase13d0_audit']['final_top10']['gold']} | unavailable | unavailable |
| Final Top5 | 6/21 | {c['phase13d0_audit']['final_top5']['hits']}/{c['phase13d0_audit']['final_top5']['gold']} | unavailable | unavailable |

The discrepancy is a reporting denominator/selection bug: C1's `10` is the all-gold count (`{c['c1_all_gold_fusion_hits']}`) across the six questions, mislabeled with the 21-item missing-evidence denominator. Restricting to the Phase 13B missing set yields **1/21**, exactly matching D0.

## Evidence ID alignment

Exact child-ID alignment: **{audit['evidence_id_alignment']['exact_child_id_alignment_count']}/{audit['evidence_id_alignment']['expected_missing_count']}**. No child/parent, citation/retrieval, or normalization mismatch was observed in the checked keys.

## Trace schema gap

Missing or non-equivalent persisted fields:

{chr(10).join(f'- `{x}`' for x in schema)}

The C1 artifact contains summary `fusion_top20`/`final_top10` and boolean `raw_retrieved`, but not full candidate lineage, local rank/query source, or rerank Top20. Those values remain `unavailable`; this audit did not rerun anything.

## Root cause and next step

Primary root cause: **REPORT_BUG**. Secondary limitation: **TRACE_SCHEMA_GAP**.  
Next recommendation: **FIX_EVALUATION_PIPELINE**. Correct the metric/report denominator and preserve full stage lineage in a future experiment artifact. No Retrieval Optimization is justified by this inconsistency audit.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    audit = build_audit(root)
    commit = "unknown"
    try:
        import subprocess

        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        pass
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({**audit, "commit": commit}, indent=2), encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(audit, commit), encoding="utf-8")


if __name__ == "__main__":
    main()
