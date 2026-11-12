from __future__ import annotations

import pytest
from industrial_rag.services.evaluation_trace_contract import (
    build_evaluation_trace,
    recompute_trace_metrics,
    validate_trace_contract,
)


def test_trace_contract_preserves_canonical_identity_and_lineage() -> None:
    trace = build_evaluation_trace(
        question_id="S1",
        question="q",
        variants=[{"query_id": "original", "query_text": "q", "source": "original"}],
        retrieval_candidates=[
            {"query_id": "original", "retriever_source": "sparse", "evidence_id": "c1", "local_rank": 2, "raw_score": 0.4}
        ],
        fusion_candidates=[{"evidence_id": "c1", "contributing_queries": ["original"], "contributing_retrievers": ["sparse"], "fusion_score": 0.1, "fusion_rank": 1}],
        rerank_candidates=[{"evidence_id": "c1", "rerank_input_rank": 1, "rerank_score": 0.9, "rerank_rank": 1}],
        final_top5=["c1"],
        final_top10=["c1"],
        gold_evidence_ids=["c1"],
    )
    assert validate_trace_contract(trace) == []
    assert trace["gold_lineage"][0]["gold_evidence_id"] == "c1"
    assert trace["gold_lineage"][0]["retrieval_sources"] == ["sparse"]
    assert trace["gold_lineage"][0]["final_top5_rank"] == 1


def test_trace_metrics_use_explicit_gold_denominator() -> None:
    rows = [
        {"expected_evidence": ["c1", "c2"], "final_top5": ["c1"], "final_top10": ["c1", "c2"]},
    ]
    metrics = recompute_trace_metrics(rows)
    assert metrics == {"gold_count": 2, "recall@5": 0.5, "recall@10": 1.0, "mrr@5": 1.0, "mrr@10": 1.0, "question_hit@5": 1.0, "question_hit@10": 1.0, "complete@5": 0.0, "complete@10": 1.0}


def test_trace_contract_rejects_duplicate_identity() -> None:
    trace = build_evaluation_trace(
        question_id="S1", question="q", variants=[], retrieval_candidates=[], fusion_candidates=[], rerank_candidates=[], final_top5=["c1", "c1"], final_top10=["c1"], gold_evidence_ids=[]
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_trace_contract(trace, raise_on_error=True)
