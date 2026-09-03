from __future__ import annotations

import pytest
from scripts.run_phase13d1_trace_consistency_audit import (
    compare_funnel_counts,
    inspect_trace_schema,
    recompute_missing_only_fusion_hits,
)


def test_recompute_missing_only_fusion_hits_uses_phase13b_missing_set() -> None:
    phase13b = {
        "phase13a_six_miss_recovery": {
            "questions": [
                {"question_id": "S1", "a3_evidence": [{"child_chunk_id": "g1"}]},
            ]
        }
    }
    phase13c = {
        "arms": {
            "A3.1_original_1_5": {
                "per_question": [
                    {"question_id": "S1", "gold_evidence": [{"gold_evidence_id": "g1", "fusion_top20": 2}]}
                ]
            }
        }
    }
    result = recompute_missing_only_fusion_hits(phase13b, phase13c, "A3.1_original_1_5")
    assert result == {"gold_count": 1, "fusion_hit_count": 1, "hit_ids": [("S1", "g1")]}


def test_compare_funnel_counts_identifies_denominator_mismatch() -> None:
    result = compare_funnel_counts(
        {"gold_count": 21, "fusion_hit_count": 1},
        {"gold_count": 29, "fusion_hit_count": 10},
    )
    assert result["match"] is False
    assert result["classification"] == "REPORT_BUG"


def test_trace_schema_reports_missing_stage_fields() -> None:
    result = inspect_trace_schema({"query_variants": [], "fusion_candidates": [], "final_top_k": []})
    assert result["missing_trace_fields"] == ["retrieval_candidates", "rerank_candidates"]


def test_compare_rejects_unverifiable_identity() -> None:
    with pytest.raises(ValueError, match="identity"):
        recompute_missing_only_fusion_hits({}, {"arms": {}}, "missing")
