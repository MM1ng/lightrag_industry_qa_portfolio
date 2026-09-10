"""Frozen Phase 9B canonical validation policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from industrial_rag.config import PROJECT_ROOT

GOLDEN_SET_VERSION = "phase9b-canonical-20-v1"
RUNNER_VERSION = "phase9b-fastapi-candidate-v1"
CANONICAL_QUESTION_IDS = (
    "S001", "S002", "S003", "S004", "S005", "S007", "S009", "S011",
    "S012", "S014", "S015", "S016", "S017", "D003", "D005", "C001",
    "C002", "C003", "N001", "N002",
)
DEFAULT_GOLDEN_SET_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
)


@dataclass(frozen=True, slots=True)
class GoldenSetPolicy:
    version: str
    source_path: Path
    source_sha256: str
    questions: tuple[dict[str, Any], ...]


def load_canonical_policy(
    path: Path = DEFAULT_GOLDEN_SET_PATH, *, question_ids: tuple[str, ...] | None = CANONICAL_QUESTION_IDS
) -> GoldenSetPolicy:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    by_id = {str(row.get("id")): row for row in rows}
    selected_ids = tuple(by_id) if question_ids is None else question_ids
    missing = [question_id for question_id in selected_ids if question_id not in by_id]
    if missing:
        raise ValueError(f"canonical golden set missing IDs: {','.join(missing)}")
    questions = tuple(by_id[question_id] for question_id in selected_ids)
    if not questions or len(questions) != len({item["id"] for item in questions}):
        raise ValueError("golden policy must contain unique questions")
    return GoldenSetPolicy(
        version=GOLDEN_SET_VERSION,
        source_path=path.resolve(),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        questions=questions,
    )


def scope_policy_for_documents(
    policy: GoldenSetPolicy, candidate_documents: set[str], *, bootstrap: bool
) -> GoldenSetPolicy:
    """Restrict only an empty-KB bootstrap run to evidence it can contain."""
    if not bootstrap:
        return policy
    questions = tuple(
        question
        for question in policy.questions
        if not question.get(
            "expects_evidence", bool(question.get("expected_citations"))
        )
        or all(
            citation.get("source_file") in candidate_documents
            for citation in question.get("expected_citations", [])
        )
    )
    return replace(policy, questions=questions)
