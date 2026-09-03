from __future__ import annotations

from scripts.run_phase13e0_a2_trace_capture import classify_missing_evidence


def test_classifies_missing_evidence_without_either_retriever_as_candidate_recall() -> None:
    assert classify_missing_evidence({"lightrag_hit": False, "bm25_hit": False, "fusion_rank": None, "rerank_rank": None, "final_top10_rank": None}) == "CANDIDATE_RECALL"


def test_classifies_retrieved_but_not_fused_as_fusion_loss() -> None:
    assert classify_missing_evidence({"lightrag_hit": True, "bm25_hit": False, "fusion_rank": None, "rerank_rank": None, "final_top10_rank": None}) == "FUSION_LOSS"


def test_marks_missing_rerank_stage_as_unresolved_instead_of_guessing() -> None:
    assert classify_missing_evidence({"lightrag_hit": True, "bm25_hit": False, "fusion_rank": 18, "rerank_rank": None, "final_top10_rank": None}) == "UNRESOLVED"
