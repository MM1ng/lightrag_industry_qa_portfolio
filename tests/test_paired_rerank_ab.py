from __future__ import annotations

import pytest
from industrial_rag.services.paired_rerank_ab import (
    candidate_fingerprint,
    multi_evidence_cases,
    validate_paired_inputs,
)


def test_candidate_fingerprint_is_stable_and_order_sensitive() -> None:
    candidates = [
        {"child_chunk_id": "c1", "child_text_hash": "h1"},
        {"child_chunk_id": "c2", "child_text_hash": "h2"},
    ]
    assert candidate_fingerprint("问题", candidates) == candidate_fingerprint("问题", candidates)
    assert candidate_fingerprint("问题", candidates) != candidate_fingerprint("问题", list(reversed(candidates)))
    assert candidate_fingerprint("问题", candidates) != candidate_fingerprint("另一个问题", candidates)


def test_validate_paired_inputs_requires_identical_candidate_bundle() -> None:
    bundle = {"question_id": "S003", "candidate_fingerprint": "fp", "candidate_ids": ["c1", "c2"]}
    validate_paired_inputs(bundle, {"candidate_fingerprint": "fp", "candidate_ids": ["c1", "c2"]})
    with pytest.raises(ValueError, match="candidate fingerprint"):
        validate_paired_inputs(bundle, {"candidate_fingerprint": "other", "candidate_ids": ["c1", "c2"]})
    with pytest.raises(ValueError, match="candidate order"):
        validate_paired_inputs(bundle, {"candidate_fingerprint": "fp", "candidate_ids": ["c2", "c1"]})


def test_multi_evidence_denominator_uses_gold_evidence_count_not_pattern_label() -> None:
    cases = [
        {"question_id": "multi", "expected_child_chunk_ids": ["c1", "c2"], "evidence_pattern": "adjacent_chunk_evidence"},
        {"question_id": "single", "expected_child_chunk_ids": ["c3"], "evidence_pattern": "single_evidence"},
        {"question_id": "miss", "expected_child_chunk_ids": ["c4", "c5"], "evidence_pattern": "multi_evidence"},
    ]
    assert [case["question_id"] for case in multi_evidence_cases(cases)] == ["multi", "miss"]


def test_multi_evidence_denominator_keeps_retrieval_and_rerank_misses() -> None:
    cases = [
        {
            "question_id": "miss",
            "expected_child_chunk_ids": ["c1", "c2"],
            "retrieval_hit": False,
            "rerank_success": False,
            "complete": False,
        }
    ]
    selected = multi_evidence_cases(cases)
    assert len(selected) == 1
    assert selected[0]["question_id"] == "miss"
