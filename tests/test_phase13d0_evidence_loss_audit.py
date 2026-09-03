from __future__ import annotations

import pytest
from scripts.run_phase13d0_evidence_loss_audit import (
    classify_evidence_loss,
    evidence_funnel_counts,
    validate_audit_inputs,
)


def test_classify_evidence_loss_distinguishes_fusion_loss() -> None:
    evidence = {
        "raw_retrieved": True,
        "fusion_top20": None,
        "final_rank": None,
    }
    result = classify_evidence_loss(evidence)
    assert result["primary_cause"] == "A"
    assert result["final_status"] == "lost_in_fusion"


def test_classify_evidence_loss_marks_rerank_top20_as_unavailable() -> None:
    evidence = {
        "raw_retrieved": True,
        "fusion_top20": 4,
        "final_rank": None,
    }
    result = classify_evidence_loss(evidence)
    assert result["primary_cause"] == "UNAVAILABLE_B_OR_C"
    assert "rerank_top20" in result["unavailable_fields"]


def test_evidence_funnel_counts_only_uses_observed_fields() -> None:
    rows = [
        {"raw_retrieved": True, "fusion_top20": 2, "final_rank": 4},
        {"raw_retrieved": True, "fusion_top20": None, "final_rank": None},
        {"raw_retrieved": False, "fusion_top20": None, "final_rank": None},
    ]
    result = evidence_funnel_counts(rows)
    assert result == {"gold": 3, "retrieval": 2, "fusion_top20": 1, "rerank_top20": None, "final_top10": 1, "final_top5": 1}


def test_validate_audit_inputs_rejects_wrong_identity() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        validate_audit_inputs({"dataset_identity": {"fingerprint": "wrong"}}, "expected")
