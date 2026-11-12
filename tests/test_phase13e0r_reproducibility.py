from __future__ import annotations

from scripts.run_phase13e0r_baseline_reproducibility_audit import (
    classify_drift,
    compare_rankings,
)


def test_classifies_matching_identity_but_changed_outputs_as_nondeterministic_runtime() -> None:
    assert classify_drift({"identity_match": True, "config_match": True, "ranking_match": False, "evaluator_match": True}) == "NONDETERMINISTIC_RUNTIME"


def test_locates_question_hit_at5_change() -> None:
    result = compare_rankings(
        [{"id": "S003", "gold": ["g1"], "old": ["g1"], "new": ["x"]}],
    )
    assert result[0]["question_id"] == "S003"
    assert result[0]["old_hit_at5"] is True
    assert result[0]["new_hit_at5"] is False
