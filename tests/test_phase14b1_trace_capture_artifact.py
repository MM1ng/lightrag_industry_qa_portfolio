from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_phase14b1_a2_trace_capture.py"
SPEC = importlib.util.spec_from_file_location("phase14b1_capture", SCRIPT)
assert SPEC and SPEC.loader
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def test_trace_accumulator_preserves_actual_stage_order_and_unavailable_failure() -> None:
    accumulator = capture.TraceAccumulator({"c1": "alpha"})
    accumulator.observe({
        "event": "pre_rerank", "question_id": "S1", "question": "q",
        "dense_candidates": [{"child_chunk_id": "c1", "rank": 1, "score": 0.3}],
        "sparse_candidates": [],
        "fusion_candidates": [{"child_chunk_id": "c1", "rank": 1, "rrf_score": 0.02, "contributions": [{"source": "lightrag", "original_rank": 1}]}],
        "rerank_candidates": [], "rerank_status": "unavailable", "rerank_failure_reason": None,
    })
    accumulator.mark_rerank_unavailable("timeout")
    trace = accumulator.traces["S1"]
    assert trace["retrieval_candidates"][0]["source"] == "dense"
    assert trace["retrieval_candidates"][0]["text_hash"] == capture.sha256_text("alpha")
    assert trace["fusion_candidates"][0]["source_ranks"] == {"lightrag": 1}
    assert trace["rerank_candidates"][0]["status"] == "unavailable"
    assert trace["stage_failures"] == [{"stage": "rerank", "status": "unavailable", "reason": "timeout"}]


def test_missing_evidence_classification_requires_complete_trace() -> None:
    assert capture.classify_missing_evidence({"raw_hit": False, "fusion_hit": "unavailable", "rerank_hit": "unavailable"}) == "UNRESOLVED"
    assert capture.classify_missing_evidence({"raw_hit": False, "fusion_hit": False, "rerank_hit": False}) == "CANDIDATE_RECALL_FAILURE"
    assert capture.classify_missing_evidence({"raw_hit": True, "fusion_hit": False, "rerank_hit": False}) == "FUSION_LOSS"
    assert capture.classify_missing_evidence({"raw_hit": True, "fusion_hit": True, "rerank_hit": False}) == "RERANKER_LOSS"
    assert capture.classify_missing_evidence({"raw_hit": True, "fusion_hit": True, "rerank_hit": True, "final_top10": False}) == "TOPK_SELECTION_LOSS"


def test_alignment_is_question_by_question_and_not_metric_based() -> None:
    canonical = {"S1": ["c1", "c2"]}
    assert capture.final_alignment({"S1": {"final": {"top10_ids": ["c1", "c2"]}}}, canonical) == []
    assert capture.final_alignment({"S1": {"final": {"top10_ids": ["c2", "c1"]}}}, canonical) == ["S1"]
    assert capture.final_alignment(
        {"S1": {"final": {"top5_ids": ["c1"]}}}, {"S1": ["c1"]}, final_key="top5_ids"
    ) == []


def test_capture_refuses_to_overwrite_canonical_artifact() -> None:
    canonical = Path("canonical.json")
    with pytest.raises(ValueError, match="canonical"):
        capture.assert_independent_output(canonical, canonical)


def test_historical_missing_denominator_remains_fixed_at_21() -> None:
    missing = capture._historical_missing()
    assert sum(len(values) for values in missing.values()) == 21
