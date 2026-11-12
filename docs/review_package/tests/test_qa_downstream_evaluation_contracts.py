from __future__ import annotations

from industrial_rag.services.qa_downstream_evaluation import (
    aggregate_cases,
    evaluate_case,
)


def _case(*, expected: list[str], retrieved: list[str], selected: list[str], cited: list[str], status: str = "success") -> dict:
    return {
        "question_id": "Q1",
        "question": "问题",
        "difficulty": "HARD",
        "question_type": "procedure",
        "evidence_pattern": "multi_evidence" if len(expected) > 1 else "single_evidence",
        "expected_child_chunk_ids": expected,
        "a2": {
            "retrieved_chunk_ids": retrieved,
            "selected_chunk_ids": selected,
            "citations": [{"chunk_id": item} for item in cited],
            "answer_status": status,
            "answer": "回答" if status != "insufficient_evidence" else "手册中未检索到充分依据，无法可靠回答该问题。",
            "answer_points": [{"point_id": "P1", "content": "回答", "evidence_ids": ["E1"], "support_status": "supported"}],
            "grounding_failure_categories": [],
            "metric_error": None,
            "trace": {"generation_id": "dev-v2-20260902"},
        },
    }


def test_multi_evidence_citation_metrics_require_complete_support() -> None:
    result = evaluate_case(_case(expected=["c1", "c2"], retrieved=["c1", "c2"], selected=["c1", "c2"], cited=["c1"]))
    assert result["citation"]["supporting_evidence_recall"] == 0.5
    assert result["citation"]["citation_precision"] == 1.0
    assert result["citation"]["citation_accuracy"] is False
    assert result["failure"]["primary_cause"] == "CITATION_MAPPING_FAILURE"


def test_false_refusal_is_not_retrieval_failure_when_evidence_was_selected() -> None:
    result = evaluate_case(_case(expected=["c1"], retrieved=["c1"], selected=["c1"], cited=[], status="insufficient_evidence"))
    assert result["refusal"]["false_refusal"] is True
    assert result["failure"]["primary_cause"] == "FALSE_REFUSAL"


def test_aggregate_stratifies_and_counts_failure_taxonomy() -> None:
    rows = [
        evaluate_case(_case(expected=["c1"], retrieved=["c1"], selected=["c1"], cited=["c1"])),
        evaluate_case(_case(expected=["c1"], retrieved=[], selected=[], cited=[])),
    ]
    report = aggregate_cases(rows)
    assert report["overall"]["question_citation_accuracy"]["value"] == 0.5
    assert report["stratified"]["difficulty=HARD"]["n"] == 2
    assert report["failure_taxonomy"]["retrieval_failure"] == 1


def test_aggregate_citation_precision_is_weighted_by_citation_count() -> None:
    rows = [
        evaluate_case(_case(expected=["c1"], retrieved=["c1"], selected=["c1"], cited=["c1"])),
        evaluate_case(_case(expected=["c1"], retrieved=["c1"], selected=["c1"], cited=["n1", "n2", "n3"])),
    ]
    assert rows[0]["citation"]["citation_precision"] == 1.0
    assert rows[1]["citation"]["citation_precision"] == 0.0
    assert aggregate_cases(rows)["overall"]["citation_precision"]["value"] == 0.25
