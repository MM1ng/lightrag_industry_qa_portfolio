from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase14b0_evidence_to_chunk_trace_audit.py"
SPEC = importlib.util.spec_from_file_location("phase14b0_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def record(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "question_id": "S003",
        "gold_evidence_id": "gold-1",
        "document_id": "doc-1",
        "page": 1,
        "gold_text_hash": "hash",
        "parsed_block_ids": ["block-1"],
        "parsed_match": "FULL",
        "parent_chunk_ids": ["parent-1"],
        "child_chunk_ids": ["gold-1"],
        "chunk_match": "FULL",
        "embedding_exists": True,
        "bm25_exists": True,
        "retrieval_hit": "unavailable",
        "retrieval_rank": None,
        "fusion_hit": "unavailable",
        "fusion_rank": None,
        "rerank_hit": "unavailable",
        "rerank_rank": None,
        "final_top10": False,
        "final_top5": False,
    }
    base.update(overrides)
    return base


def test_lineage_schema_requires_all_contract_fields() -> None:
    assert audit.validate_lineage_record(record()) == []
    incomplete = record()
    incomplete.pop("bm25_exists")
    assert audit.validate_lineage_record(incomplete) == ["missing field: bm25_exists"]


def test_historical_missing_evidence_is_not_filtered_by_retrieval_state() -> None:
    missing = {"S003": ["gold-1"], "S006": ["gold-2"]}
    rows = audit.build_missing_rows(missing, {("S003", "gold-1"): record(retrieval_hit=False)})
    assert [(row["question_id"], row["gold_evidence_id"]) for row in rows] == [
        ("S003", "gold-1"),
        ("S006", "gold-2"),
    ]
    assert rows[1]["retrieval_hit"] == "unavailable"


def test_chunk_identity_preserves_canonical_child_and_parent_mapping() -> None:
    child = {"gold-1": {"parent_chunk_id": "parent-1", "content": "evidence"}}
    match = audit.resolve_chunk_lineage("gold-1", "evidence", child)
    assert match == {"parent_chunk_ids": ["parent-1"], "child_chunk_ids": ["gold-1"], "chunk_match": "FULL"}


def test_identity_contract_rejects_a_fingerprint_mismatch() -> None:
    expected = {"dataset_fingerprint": "a", "generation_id": "g", "chunk_fingerprint": "c"}
    current = {"dataset_fingerprint": "a", "generation_id": "g", "chunk_fingerprint": "different"}
    assert audit.identity_drift(expected, current) == {"chunk_fingerprint": {"expected": "c", "actual": "different"}}


def test_root_cause_rules_only_classify_when_the_required_trace_exists() -> None:
    assert audit.classify_root_cause(record(parsed_match="MISSING")) == "PARSING_LOSS"
    assert audit.classify_root_cause(record(chunk_match="MISSING")) == "CHUNK_GENERATION_LOSS"
    assert audit.classify_root_cause(record(embedding_exists=False)) == "INDEX_MISSING"
    assert audit.classify_root_cause(record(retrieval_hit=False)) == "CANDIDATE_RECALL_FAILURE"
    assert audit.classify_root_cause(record(retrieval_hit=True, fusion_hit=False)) == "FUSION_LOSS"
    assert audit.classify_root_cause(record(retrieval_hit=True, fusion_hit=True, rerank_hit=False)) == "RERANKER_LOSS"
    assert audit.classify_root_cause(record(retrieval_hit=True, fusion_hit=True, rerank_hit=True, final_top10=False)) == "TOPK_SELECTION_LOSS"
    assert audit.classify_root_cause(record()) == "UNRESOLVED"


def test_frozen_audit_preserves_all_21_missing_evidence_without_stage_inference() -> None:
    report = audit.audit("test-commit")
    assert report["historical_missing_evidence_total"] == 21
    assert report["evidence_funnel"]["parsed"] == {"known_hit": 21, "unavailable": 0}
    assert report["evidence_funnel"]["chunk"] == {"known_hit": 21, "unavailable": 0}
    assert report["evidence_funnel"]["embedding"] == {"known_hit": 21, "unavailable": 0}
    assert report["evidence_funnel"]["bm25"] == {"known_hit": 21, "unavailable": 0}
    assert report["evidence_funnel"]["retrieval"] == {"known_hit": 0, "unavailable": 21}
    assert {row["primary_root_cause"] for row in report["evidence_lineage"]} == {"UNRESOLVED"}
