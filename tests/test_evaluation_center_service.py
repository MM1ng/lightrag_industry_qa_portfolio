from pathlib import Path

from industrial_rag.services.evaluation_center_service import EvaluationCenterService


def _service(tmp_path: Path) -> EvaluationCenterService:
    return EvaluationCenterService(None, tmp_path)  # type: ignore[arg-type]


def _row(question_id: str, *, positive: bool, hit: bool = True, trace: bool = True) -> dict:
    return {
        "id": question_id,
        "query": f"question {question_id}",
        "expects_evidence": positive,
        "answer_status": "success" if positive else "insufficient_evidence",
        "citation_match": hit,
        "negative_case_passed": not positive,
        "trace_complete": trace,
        "citation_trace": [{"document_name": "manual.pdf", "page": 2}] if hit and positive else [],
        "latency_ms": 10,
    }


def test_artifact_payload_exposes_gate_metrics(tmp_path: Path) -> None:
    payload = _service(tmp_path)._artifact_payload(
        [_row("Q1", positive=True), _row("Q2", positive=False)],
        tmp_path / "run.jsonl",
        None,
        "available",
    )

    assert payload["metrics"] == {
        "question_count": 2,
        "positive_match_count": 1,
        "positive_question_count": 1,
        "negative_passed_count": 1,
        "negative_question_count": 1,
        "citation_gate": True,
        "trace_gate": True,
        "negative_unsupported_gate": True,
        "latency_ms": 10,
    }


def test_compare_marks_question_and_metric_changes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    before = service._artifact_payload([_row("Q1", positive=True, hit=False)], tmp_path / "before.jsonl", None, "available")
    after = service._artifact_payload([_row("Q1", positive=True, hit=True)], tmp_path / "after.jsonl", None, "available")

    comparison = service._compare(after, before)

    assert comparison["status"] == "comparable"
    assert comparison["metrics"]["positive_match_count"] == 1
    assert comparison["questions"][0]["changed"] is True


def test_compare_rejects_different_question_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    before = service._artifact_payload([_row("Q1", positive=True)], tmp_path / "before.jsonl", None, "available")
    after = service._artifact_payload([_row("Q2", positive=True)], tmp_path / "after.jsonl", None, "available")

    comparison = service._compare(after, before)

    assert comparison["status"] == "not_comparable"
    assert "identity" in comparison["reason"]
