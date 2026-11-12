"""Deterministic citation binding checks against the frozen multi-evidence set."""

from __future__ import annotations

from typing import Any

from industrial_rag.evidence_identity import stable_evidence_identity


def check_citation_binding(case: dict[str, Any]) -> dict[str, Any]:
    expected = case["golden"].get("expected_evidence", [])
    citations = case.get("response", {}).get("citations", [])
    expected_by_id = {item.get("evidence_id"): item for item in expected if item.get("evidence_id")}
    expected_chunk_ids = {str(item.get("chunk_id")) for item in expected if item.get("chunk_id")}
    cited_chunk_ids = {str(item.get("chunk_id")) for item in citations if item.get("chunk_id")}
    expected_keys = {
        identity for item in expected
        if (identity := stable_evidence_identity(item)) is not None
    }
    cited_keys = {
        identity for item in citations
        if (identity := stable_evidence_identity(item)) is not None
    }
    expected_docs = {item["document_name"] for item in expected}
    cited_docs = {item.get("document_name") for item in citations}
    expected_pages = expected_keys
    cited_pages = cited_keys
    point_results = []
    for point in case["golden"].get("expected_answer_points", []):
        supporting_keys = {
            stable_evidence_identity(expected_by_id[eid])
            for eid in point["supported_by"] if eid in expected_by_id
        }
        point_results.append({"point_id": point["point_id"], "supported": bool(supporting_keys & cited_keys)})
    return {
        "question_id": case["question_id"],
        "split": case["golden"]["split"],
        "answerable": case["golden"]["answerable"],
        "wrong_document": bool(cited_keys) and not bool(expected_docs & cited_docs),
        "wrong_page": bool(cited_keys) and not bool(expected_pages & cited_pages),
        "wrong_chunk": bool(cited_keys) and not bool(expected_keys & cited_keys),
        "chunk_id_mismatch": bool(cited_chunk_ids) and not bool(expected_chunk_ids & cited_chunk_ids),
        "expected_evidence_count": len(expected),
        "cited_expected_count": len(expected_keys & cited_keys),
        "answer_point_results": point_results,
        "all_answer_points_supported": all(item["supported"] for item in point_results),
        "claim_level_accuracy_available": False,
    }
