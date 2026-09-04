"""Immutable, trace-complete contract for future canonical A2 artifacts.

This module deliberately does not call retrieval or reranking.  It validates
the artifact emitted by a controlled formal evaluation and replays the
existing metric semantics from its saved final rankings.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from industrial_rag.services.evaluation_trace_contract import recompute_trace_metrics

SCHEMA_VERSION = "canonical-a2-evaluation-v2"
_IDENTITY_FIELDS = (
    "dataset_fingerprint",
    "generation_id",
    "document_fingerprint",
    "chunk_registry_fingerprint",
    "embedding_index_fingerprint",
    "bm25_index_fingerprint",
    "qdrant_collection_identity",
    "gold_mapping_fingerprint",
    "question_ids",
)


class CanonicalArtifactV2Error(ValueError):
    """Raised when a trace-complete canonical artifact violates its contract."""


def candidate_fingerprint(candidate_ids: Sequence[str]) -> str:
    """Return the stable fingerprint of an ordered rerank input bundle."""
    return hashlib.sha256("\n".join(candidate_ids).encode("utf-8")).hexdigest()


def _errors(artifact: Mapping[str, Any], expected_identity: Mapping[str, Any] | None) -> list[str]:
    errors: list[str] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    identity = artifact.get("identity")
    if not isinstance(identity, Mapping):
        return [*errors, "identity must be an object"]
    for field in _IDENTITY_FIELDS:
        if field not in identity:
            errors.append(f"identity missing {field}")
    question_ids = identity.get("question_ids")
    questions = artifact.get("questions")
    if not isinstance(questions, list):
        return [*errors, "questions must be a list"]
    if isinstance(question_ids, list) and [row.get("question_id") for row in questions if isinstance(row, Mapping)] != question_ids:
        errors.append("identity question_ids must exactly match question trace order")
    if expected_identity is not None:
        for field in _IDENTITY_FIELDS:
            if identity.get(field) != expected_identity.get(field):
                errors.append(f"identity mismatch for {field}")

    seen_question_ids: set[str] = set()
    for position, row in enumerate(questions, start=1):
        label = f"questions[{position}]"
        if not isinstance(row, Mapping):
            errors.append(f"{label} must be an object")
            continue
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            errors.append(f"{label} missing question_id")
        elif question_id in seen_question_ids:
            errors.append(f"{label} has duplicate question_id {question_id}")
        else:
            seen_question_ids.add(question_id)
        for field in (
            "query",
            "query_hash",
            "expected_evidence",
            "raw_retrieval_candidates",
            "fusion_candidates",
            "rerank_input",
            "rerank_output",
            "final",
            "runtime_metadata",
        ):
            if field not in row:
                errors.append(f"{label} missing {field}")
        rerank_input = row.get("rerank_input")
        rerank_output = row.get("rerank_output")
        final = row.get("final")
        if not isinstance(rerank_input, Mapping):
            errors.append(f"{label} rerank_input must be an object")
            continue
        input_ids = rerank_input.get("candidate_ids")
        fingerprint = rerank_input.get("candidate_fingerprint")
        if not isinstance(input_ids, list) or not all(isinstance(item, str) for item in input_ids):
            errors.append(f"{label} rerank_input candidate_ids must be string list")
        elif len(input_ids) != len(set(input_ids)):
            errors.append(f"{label} rerank_input candidate_ids contains duplicates")
        elif fingerprint != candidate_fingerprint(input_ids):
            errors.append(f"{label} rerank_input candidate_fingerprint does not match candidate_ids")
        if not isinstance(rerank_output, list):
            errors.append(f"{label} rerank_output must be a list")
            continue
        output_ids = [item.get("candidate_id") for item in rerank_output if isinstance(item, Mapping)]
        if len(output_ids) != len(rerank_output) or not all(isinstance(item, str) for item in output_ids):
            errors.append(f"{label} rerank_output candidates must have candidate_id")
        elif isinstance(input_ids, list) and set(output_ids) != set(input_ids):
            errors.append(f"{label} rerank_output candidate ids must equal rerank input")
        elif output_ids != [item["candidate_id"] for item in sorted(rerank_output, key=lambda item: item.get("rerank_rank", 10**9))]:
            errors.append(f"{label} rerank_output must be ordered by rerank_rank")
        if not isinstance(final, Mapping):
            errors.append(f"{label} final must be an object")
            continue
        top5, top10 = final.get("top5_evidence_ids"), final.get("top10_evidence_ids")
        if not isinstance(top5, list) or not isinstance(top10, list):
            errors.append(f"{label} final must include top5_evidence_ids and top10_evidence_ids")
        elif top5 != top10[:5] or top10 != output_ids[: len(top10)]:
            errors.append(f"{label} final Top10 must be the rerank output prefix and Top5 its prefix")
    metrics = artifact.get("metrics")
    if not isinstance(metrics, Mapping):
        errors.append("metrics must be an object")
    elif not errors and metrics != _replay_metrics(questions):
        errors.append("metrics must equal offline replay of saved final rankings")
    return errors


def validate_canonical_artifact_v2(
    artifact: Mapping[str, Any], *, expected_identity: Mapping[str, Any] | None = None, raise_on_error: bool = False
) -> list[str]:
    """Validate identity, complete trace stages, and final ranking lineage."""
    errors = _errors(artifact, expected_identity)
    if errors and raise_on_error:
        raise CanonicalArtifactV2Error("; ".join(errors))
    return errors


def replay_canonical_artifact_v2(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Offline-recompute existing Recall/MRR/Hit/Complete metric semantics."""
    validate_canonical_artifact_v2(artifact, raise_on_error=True)
    return {"metrics": _replay_metrics(artifact["questions"]), "question_count": len(artifact["questions"])}


def _replay_metrics(questions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "expected_evidence": row["expected_evidence"],
            "final_top5": row["final"]["top5_evidence_ids"],
            "final_top10": row["final"]["top10_evidence_ids"],
        }
        for row in questions
    ]
    return recompute_trace_metrics(rows)


def build_canonical_artifact_v2(
    *,
    identity: Mapping[str, Any],
    runtime_metadata: Mapping[str, Any],
    question_traces: Sequence[Mapping[str, Any]],
    historical_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and self-validate an immutable v2 evaluation artifact payload."""
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "canonical_a2_evaluation",
        "identity": dict(identity),
        "runtime_metadata": dict(runtime_metadata),
        "historical_authority": dict(historical_authority),
        "questions": [dict(trace) for trace in question_traces],
        "metrics": _replay_metrics(question_traces),
    }
    validate_canonical_artifact_v2(artifact, expected_identity=identity, raise_on_error=True)
    return artifact


def inspect_legacy_canonical_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Describe v1 compatibility without mutating or falsely upgrading it."""
    per_question = artifact.get("per_question")
    compatible = isinstance(per_question, list) and isinstance(artifact.get("metrics"), Mapping)
    missing_v2_fields = [
        "raw_retrieval_candidates",
        "fusion_candidates",
        "rerank_input.candidate_fingerprint",
        "rerank_output",
        "rerank_scores",
        "runtime_metadata",
    ]
    return {
        "compatible": compatible,
        "trace_complete": False,
        "schema_version": "legacy-v1",
        "question_count": len(per_question) if isinstance(per_question, list) else 0,
        "missing_v2_fields": missing_v2_fields,
        "read_only": True,
        "upgrade_policy": "A legacy artifact remains authoritative historical evidence and cannot be promoted to v2 without a controlled trace-complete evaluation.",
    }
