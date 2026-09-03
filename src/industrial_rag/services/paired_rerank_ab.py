"""Pure contracts for paired reranker evaluations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def candidate_fingerprint(query: str, candidates: Sequence[Mapping[str, Any]]) -> str:
    """Identify query plus ordered candidate IDs and text hashes."""
    payload = {
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "candidate_ids": [str(row.get("child_chunk_id") or row.get("chunk_id")) for row in candidates],
        "candidate_text_hashes": [
            str(row.get("child_text_hash") or row.get("text_hash") or "") for row in candidates
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_paired_inputs(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> None:
    """Fail closed if the two reranker arms did not receive the same bundle."""
    if first.get("candidate_fingerprint") != second.get("candidate_fingerprint"):
        raise ValueError("candidate fingerprint mismatch between reranker arms")
    if list(first.get("candidate_ids", ())) != list(second.get("candidate_ids", ())):
        raise ValueError("candidate order mismatch between reranker arms")


def multi_evidence_cases(cases: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return the fixed multi-evidence denominator from gold, never retrieval."""
    return [
        case
        for case in cases
        if len({str(item).strip() for item in case.get("expected_child_chunk_ids", ()) if str(item).strip()}) > 1
    ]


__all__ = ["candidate_fingerprint", "multi_evidence_cases", "validate_paired_inputs"]
