from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from industrial_rag.services.canonical_evaluation_artifact_v2 import (
    CanonicalArtifactV2Error,
    build_canonical_artifact_v2,
    inspect_legacy_canonical_artifact,
    replay_canonical_artifact_v2,
    validate_canonical_artifact_v2,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_V1 = ROOT / "evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json"


def _fingerprint(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _identity() -> dict[str, object]:
    return {
        "dataset_fingerprint": "dataset-sha",
        "generation_id": "dev-v2-20260902",
        "document_fingerprint": "document-sha",
        "chunk_registry_fingerprint": "chunks-sha",
        "embedding_index_fingerprint": "embedding-sha",
        "bm25_index_fingerprint": "bm25-sha",
        "qdrant_collection_identity": "collection-v1",
        "gold_mapping_fingerprint": "gold-sha",
        "question_ids": ["Q1", "Q2"],
    }


def _question(question_id: str, expected: list[str], final: list[str]) -> dict[str, object]:
    input_ids = ["c1", "c2", "c3"]
    return {
        "question_id": question_id,
        "query": f"query for {question_id}",
        "query_hash": hashlib.sha256(question_id.encode("utf-8")).hexdigest(),
        "expected_evidence": expected,
        "raw_retrieval_candidates": [
            {"candidate_id": candidate_id, "retriever_source": "lightrag", "local_rank": rank, "raw_score": 1.0 / rank}
            for rank, candidate_id in enumerate(input_ids, start=1)
        ],
        "fusion_candidates": [
            {"candidate_id": candidate_id, "fusion_rank": rank, "fusion_score": 1.0 / rank, "contributing_retrievers": ["lightrag"]}
            for rank, candidate_id in enumerate(input_ids, start=1)
        ],
        "rerank_input": {"candidate_ids": input_ids, "candidate_fingerprint": _fingerprint(input_ids)},
        "rerank_output": [
            {"candidate_id": candidate_id, "rerank_input_rank": input_ids.index(candidate_id) + 1, "rerank_rank": rank, "rerank_score": 1.0 / rank}
            for rank, candidate_id in enumerate(final + [candidate for candidate in input_ids if candidate not in final], start=1)
        ],
        "final": {"top5_evidence_ids": final[:5], "top10_evidence_ids": final},
        "runtime_metadata": {"model_name": "qwen3-rerank", "provider": "aliyun_model_studio", "request_status": "success", "fallback_used": False, "latency_ms": 12.0},
    }


def _artifact() -> dict[str, object]:
    return build_canonical_artifact_v2(
        identity=_identity(),
        runtime_metadata={"retrieval_config": {"candidate_top_k": 20}, "reranker": {"model": "qwen3-rerank", "top_n": 10}},
        question_traces=[_question("Q1", ["c1"], ["c1", "c2", "c3"]), _question("Q2", ["c2", "c3"], ["c2", "c1", "c3"])],
        historical_authority={"path": "evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json", "sha256": "legacy-sha"},
    )


def test_v2_schema_validates_trace_complete_artifact_and_replays_metrics() -> None:
    artifact = _artifact()
    assert validate_canonical_artifact_v2(artifact, expected_identity=_identity()) == []
    replayed = replay_canonical_artifact_v2(artifact)
    assert replayed["metrics"] == artifact["metrics"]
    assert replayed["metrics"]["recall@5"] == 1.0
    assert replayed["metrics"]["complete@5"] == 1.0


def test_v2_schema_rejects_missing_rerank_input_fingerprint() -> None:
    artifact = _artifact()
    del artifact["questions"][0]["rerank_input"]["candidate_fingerprint"]
    with pytest.raises(CanonicalArtifactV2Error, match="candidate_fingerprint"):
        validate_canonical_artifact_v2(artifact, raise_on_error=True)


def test_v2_schema_rejects_identity_drift() -> None:
    artifact = _artifact()
    drifted_identity = deepcopy(_identity())
    drifted_identity["bm25_index_fingerprint"] = "other-index"
    with pytest.raises(CanonicalArtifactV2Error, match="identity mismatch"):
        validate_canonical_artifact_v2(artifact, expected_identity=drifted_identity, raise_on_error=True)


def test_v2_schema_rejects_final_order_that_disagrees_with_rerank_output() -> None:
    artifact = _artifact()
    artifact["questions"][0]["final"]["top5_evidence_ids"] = ["c2", "c1", "c3"]
    with pytest.raises(CanonicalArtifactV2Error, match="final Top10"):
        validate_canonical_artifact_v2(artifact, raise_on_error=True)


def test_v2_schema_rejects_metrics_that_do_not_match_artifact_replay() -> None:
    artifact = _artifact()
    artifact["metrics"]["recall@5"] = 0.0
    with pytest.raises(CanonicalArtifactV2Error, match="offline replay"):
        validate_canonical_artifact_v2(artifact, raise_on_error=True)


def test_legacy_v1_is_readable_without_mutating_historical_artifact() -> None:
    before = hashlib.sha256(CANONICAL_V1.read_bytes()).hexdigest()
    inspection = inspect_legacy_canonical_artifact(json.loads(CANONICAL_V1.read_text(encoding="utf-8")))
    after = hashlib.sha256(CANONICAL_V1.read_bytes()).hexdigest()
    assert before == after
    assert inspection["compatible"] is True
    assert inspection["trace_complete"] is False
    assert "raw_retrieval_candidates" in inspection["missing_v2_fields"]
