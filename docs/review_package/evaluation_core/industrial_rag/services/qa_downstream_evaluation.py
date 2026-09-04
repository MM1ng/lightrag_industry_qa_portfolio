"""Deterministic metrics and trace-based attribution for the QA downstream gate."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

REFUSAL_STATUSES = {"insufficient_evidence", "safety_blocked"}
ANSWERED_STATUSES = {"success", "partial_answer"}


def _citation_ids(values: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("chunk_id")) for item in values if item.get("chunk_id")]


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None}


def _classify_failure(case: dict[str, Any], arm: dict[str, Any], expected: set[str], retrieved: set[str], selected: set[str], cited: set[str]) -> dict[str, Any]:
    status = str(arm.get("answer_status") or "error")
    secondary: list[str] = []
    if arm.get("metric_error"):
        return {"primary_cause": "QA_RUNTIME_FAILURE", "secondary_causes": secondary}
    if not arm.get("trace"):
        return {"primary_cause": "RETRIEVAL_FAILURE", "secondary_causes": secondary}
    if not expected & retrieved:
        return {"primary_cause": "RETRIEVAL_FAILURE", "secondary_causes": secondary}
    if expected - retrieved:
        secondary.append("RETRIEVAL_INCOMPLETE")
    if status in REFUSAL_STATUSES:
        if expected & selected:
            return {"primary_cause": "FALSE_REFUSAL", "secondary_causes": secondary}
        return {"primary_cause": "RETRIEVAL_INCOMPLETE", "secondary_causes": secondary}
    if not expected & selected:
        return {"primary_cause": "EVIDENCE_SELECTION_FAILURE", "secondary_causes": secondary}
    if expected - cited:
        return {"primary_cause": "CITATION_MAPPING_FAILURE", "secondary_causes": secondary}
    if arm.get("grounding_failure_categories"):
        return {"primary_cause": "GROUNDING_FAILURE", "secondary_causes": secondary}
    if not arm.get("answer") and status in ANSWERED_STATUSES:
        return {"primary_cause": "GENERATION_FAILURE", "secondary_causes": secondary}
    return {"primary_cause": "PASS", "secondary_causes": secondary}


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one already-captured production QA result without model calls."""
    arm = case["a2"]
    expected = {str(item) for item in case.get("expected_child_chunk_ids", ())}
    retrieved_ids = [str(item) for item in arm.get("retrieved_chunk_ids", ())]
    selected_ids = [str(item) for item in arm.get("selected_chunk_ids", ())]
    cited_ids = _citation_ids(list(arm.get("citations", ())))
    retrieved, selected, cited = set(retrieved_ids), set(selected_ids), set(cited_ids)
    answered = str(arm.get("answer_status")) in ANSWERED_STATUSES
    refusal = str(arm.get("answer_status")) in REFUSAL_STATUSES
    correct_cited = expected & cited
    unsupported_claims = [
        dict(point) for point in arm.get("answer_points", ())
        if point.get("support_status") == "unsupported"
    ]
    failure = _classify_failure(case, arm, expected, retrieved, selected, cited)
    return {
        "question_id": str(case["question_id"]),
        "question": case.get("question"),
        "difficulty": case.get("difficulty"),
        "question_type": case.get("question_type"),
        "source_document_id": case.get("source_document_id"),
        "evidence_pattern": case.get("evidence_pattern"),
        "expected_evidence_count": len(expected),
        "expected_evidence_ids": sorted(expected),
        "retrieved_evidence_count": len(expected & retrieved),
        "retrieved_evidence_ids": [{"chunk_id": item, "rank": retrieved_ids.index(item) + 1} for item in retrieved_ids if item in expected],
        "selected_evidence_ids": selected_ids,
        "cited_evidence_ids": cited_ids,
        "answer": arm.get("answer", ""),
        "answer_status": arm.get("answer_status"),
        "claim_count": len(arm.get("answer_points", ())),
        "answer_claims": list(arm.get("answer_points", ())),
        "unsupported_claims": unsupported_claims,
        "citation": {
            "citation_accuracy": bool(correct_cited == expected and expected),
            "citation_precision": len(correct_cited) / len(cited) if cited else None,
            "citation_recall": len(correct_cited) / len(expected) if expected else None,
            "supporting_evidence_recall": len(correct_cited) / len(expected) if expected else None,
            "citation_coverage": bool(cited),
            "correct_citation_count": len(correct_cited),
            "citation_count": len(cited),
            "supporting_evidence_count": len(correct_cited),
            "expected_evidence_count": len(expected),
        },
        "refusal": {
            "is_refusal": refusal,
            "false_refusal": refusal and bool(expected & selected),
            "insufficient_evidence": str(arm.get("answer_status")) == "insufficient_evidence",
        },
        "grounding": {
            "failure_count": len(arm.get("grounding_failure_categories", ())),
            "failure_categories": list(arm.get("grounding_failure_categories", ())),
        },
        "retrieval": {
            "a2_ranks": [{"chunk_id": item, "rank": index + 1} for index, item in enumerate(retrieved_ids)],
            "hit_at_5": bool(expected & set(retrieved_ids[:5])),
            "complete_at_5": expected <= set(retrieved_ids[:5]),
            "hit_at_10": bool(expected & set(retrieved_ids[:10])),
            "complete_at_10": expected <= set(retrieved_ids[:10]),
        },
        "trace": arm.get("trace", {}),
        "failure": failure,
        "final_outcome": "PASS" if failure["primary_cause"] == "PASS" else "FAIL",
        "answered": answered,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "n": n,
        "question_citation_accuracy": _rate(sum(item["citation"]["citation_accuracy"] for item in rows), n),
        "citation_precision": _rate(sum(item["citation"]["correct_citation_count"] for item in rows), sum(item["citation"]["citation_count"] for item in rows)),
        "citation_recall": _rate(sum(item["citation"]["supporting_evidence_count"] for item in rows), sum(item["citation"]["expected_evidence_count"] for item in rows)),
        "supporting_evidence_recall": _rate(sum(item["citation"]["supporting_evidence_count"] for item in rows), sum(item["citation"]["expected_evidence_count"] for item in rows)),
        "citation_coverage": _rate(sum(item["citation"]["citation_coverage"] for item in rows), n),
        "supported_answer_rate": {"status": "proxy", "value": sum(item["final_outcome"] == "PASS" for item in rows) / n if n else None},
        "correct_refusal": _rate(sum(item["refusal"]["is_refusal"] and not item["refusal"]["false_refusal"] for item in rows), n),
        "false_refusal": _rate(sum(item["refusal"]["false_refusal"] for item in rows), n),
        "false_refusal_rate": _rate(sum(item["refusal"]["false_refusal"] for item in rows), n),
        "insufficient_evidence": _rate(sum(item["refusal"]["insufficient_evidence"] for item in rows), n),
        "grounding_failure_count": sum(item["grounding"]["failure_count"] for item in rows),
    }


def aggregate_cases(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for key in (f"difficulty={row['difficulty']}", f"source_document={row['source_document_id']}", f"question_type={row['question_type']}", f"evidence_pattern={row['evidence_pattern']}"):
            groups[key].append(row)
    taxonomy_names = {
        "PASS": "pass",
        "RETRIEVAL_FAILURE": "retrieval_failure",
        "RETRIEVAL_INCOMPLETE": "retrieval_incomplete",
        "EVIDENCE_SELECTION_FAILURE": "evidence_selection_failure",
        "CONTEXT_CONSTRUCTION_FAILURE": "context_failure",
        "GENERATION_FAILURE": "generation_failure",
        "CITATION_MAPPING_FAILURE": "citation_failure",
        "GROUNDING_FAILURE": "grounding_failure",
        "FALSE_REFUSAL": "false_refusal",
        "QA_RUNTIME_FAILURE": "qa_runtime_failure",
    }
    taxonomy = Counter(taxonomy_names.get(item["failure"]["primary_cause"], item["failure"]["primary_cause"].lower()) for item in rows)
    taxonomy.setdefault("pass", 0)
    return {"overall": _aggregate(rows), "stratified": {key: _aggregate(value) for key, value in sorted(groups.items())}, "failure_taxonomy": dict(taxonomy)}
