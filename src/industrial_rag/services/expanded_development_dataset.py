"""Offline contract and quality audit for the expanded Development dataset."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "question_id",
    "question",
    "split",
    "source_document_id",
    "question_type",
    "difficulty",
    "evidence_pattern",
    "expected_child_chunk_ids",
    "expected_parent_chunk_ids",
    "evidence",
}
DIFFICULTIES = {"EASY", "MEDIUM", "HARD"}


class DatasetContractError(ValueError):
    """Raised when a dataset cannot be evaluated against a frozen generation."""


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    parent_chunk_id: str | None
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    section_title: str | None


@dataclass(frozen=True)
class GenerationSnapshot:
    generation_id: str
    child_manifest_hash: str
    lexical_index_fingerprint: str
    children: dict[str, ChunkRecord]
    parents: dict[str, ChunkRecord]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record(row: dict[str, Any], *, child: bool) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=str(row["chunk_id"] if child else row["parent_chunk_id"]),
        document_id=str(row["document_id"]),
        document_name=str(row["document_name"]),
        content=str(row.get("content", "")),
        parent_chunk_id=str(row["parent_chunk_id"]) if child else None,
        page_start=int(row["page_start"]) if row.get("page_start") is not None else None,
        page_end=int(row["page_end"]) if row.get("page_end") is not None else None,
        section_path=tuple(str(value) for value in row.get("section_path", [])),
        section_title=str(row["section_title"]) if row.get("section_title") is not None else None,
    )


def load_generation_snapshot(path: Path) -> GenerationSnapshot:
    metadata = json.loads((path / "generation_metadata.json").read_text(encoding="utf-8"))
    retrieval = path / "retrieval"
    children = {_record(row, child=True).chunk_id: _record(row, child=True) for row in _read_jsonl(retrieval / "child_chunks.jsonl")}
    parents = {_record(row, child=False).chunk_id: _record(row, child=False) for row in _read_jsonl(retrieval / "parent_chunks.jsonl")}
    return GenerationSnapshot(
        generation_id=str(metadata["generation_id"]),
        child_manifest_hash=str(metadata["child_manifest_hash"]),
        lexical_index_fingerprint=str(metadata["lexical_index_fingerprint"]),
        children=children,
        parents=parents,
    )


def validate_dataset(cases: list[dict[str, Any]], snapshot: GenerationSnapshot) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"case[{index}]"
        missing = REQUIRED_FIELDS - set(case)
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
            continue
        question_id = str(case["question_id"])
        if question_id in ids:
            errors.append(f"duplicate question_id: {question_id}")
        ids.add(question_id)
        if case["split"] != "development":
            errors.append(f"{question_id} split must be development")
        if case["difficulty"] not in DIFFICULTIES:
            errors.append(f"{question_id} invalid difficulty")
        child_ids = [str(value) for value in case["expected_child_chunk_ids"]]
        parent_ids = [str(value) for value in case["expected_parent_chunk_ids"]]
        evidence = case["evidence"]
        if not child_ids or not parent_ids or not evidence:
            errors.append(f"{question_id} evidence must be non-empty")
            continue
        children = [snapshot.children.get(child_id) for child_id in child_ids]
        if any(child is None for child in children):
            errors.append(f"{question_id} child chunk missing from generation")
            continue
        assert all(child is not None for child in children)
        if any(child.document_id != str(case["source_document_id"]) for child in children):
            errors.append(f"{question_id} source document identity mismatch")
        derived_parents = {child.parent_chunk_id for child in children}
        if not set(parent_ids) <= set(snapshot.parents):
            errors.append(f"{question_id} parent chunk missing from generation")
        if None in derived_parents or not derived_parents <= set(parent_ids):
            errors.append(f"{question_id} child to parent mapping invalid")
        evidence_child_ids = {str(item.get("child_chunk_id")) for item in evidence}
        if evidence_child_ids != set(child_ids):
            errors.append(f"{question_id} evidence annotation incomplete")
        if set(parent_ids) != derived_parents:
            errors.append(f"{question_id} expected parent set is incomplete or contains extras")
        for item in evidence:
            child = snapshot.children.get(str(item.get("child_chunk_id")))
            if child is None:
                errors.append(f"{question_id} evidence child missing from generation")
                continue
            if str(item.get("parent_chunk_id")) != child.parent_chunk_id:
                errors.append(f"{question_id} evidence parent mapping invalid")
            if str(item.get("text", "")) != child.content:
                errors.append(f"{question_id} evidence text mismatch")
            if item.get("page_start") != child.page_start or item.get("page_end") != child.page_end:
                errors.append(f"{question_id} evidence page metadata mismatch")
    if not cases:
        errors.append("dataset must contain questions")
    if errors:
        raise DatasetContractError("; ".join(errors))
    return []


def canonical_dataset_fingerprint(cases: list[dict[str, Any]]) -> str:
    payload = "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for case in cases)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.casefold()):
        tokens.add(token)
    compact = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    tokens.update(compact[index : index + 2] for index in range(len(compact) - 1))
    return tokens


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def audit_dataset(cases: list[dict[str, Any]], snapshot: GenerationSnapshot, legacy_ids: set[str]) -> dict[str, Any]:
    questions = [(str(case["question_id"]), str(case["question"])) for case in cases]
    question_duplicate_pairs = [
        {"left": left_id, "right": right_id, "jaccard": round(_similarity(left, right), 4)}
        for index, (left_id, left) in enumerate(questions)
        for right_id, right in questions[index + 1 :]
        if _similarity(left, right) >= 0.55
    ]
    evidence_counts = Counter(
        str(item["child_chunk_id"]) for case in cases for item in case.get("evidence", [])
    )
    coverage = {
        "source_document": dict(Counter(str(case["source_document_id"]) for case in cases)),
        "question_type": dict(Counter(str(case["question_type"]) for case in cases)),
        "difficulty": dict(Counter(str(case["difficulty"]) for case in cases)),
        "evidence_pattern": dict(Counter(str(case["evidence_pattern"]) for case in cases)),
    }
    counts = {
        "total_questions": len(cases),
        "new_questions": sum(str(case["question_id"]) not in legacy_ids for case in cases),
        "legacy_questions_retained": sum(str(case["question_id"]) in legacy_ids for case in cases),
        "single_evidence": sum(len(case["expected_child_chunk_ids"]) == 1 for case in cases),
        "multi_evidence": sum(len(case["expected_child_chunk_ids"]) > 1 for case in cases),
        "table_or_structured": sum("table" in str(case["evidence_pattern"]) or "structured" in str(case["evidence_pattern"]) for case in cases),
        "adjacent_chunk": sum("adjacent" in str(case["evidence_pattern"]) for case in cases),
    }
    duplicate_audit = {
        "question_duplicate_pairs": question_duplicate_pairs,
        "evidence_reuse_counts": dict(sorted(evidence_counts.items())),
        "max_evidence_reuse": max(evidence_counts.values(), default=0),
        "template_like_question_count": len(question_duplicate_pairs),
        "passed": not question_duplicate_pairs,
    }
    return {
        "counts": counts,
        "coverage": coverage,
        "duplicate_audit": duplicate_audit,
        "validation_errors": validate_dataset(cases, snapshot),
        "fingerprint": canonical_dataset_fingerprint(cases),
    }


def build_manifest(
    cases: list[dict[str, Any]],
    snapshot: GenerationSnapshot,
    audit: dict[str, Any],
    *,
    source_dataset: str,
    guards: dict[str, bool],
) -> dict[str, Any]:
    errors = list(audit["validation_errors"])
    coverage = audit["coverage"]
    sources = coverage["source_document"]
    gate_failures = list(errors)
    if len(cases) < 20:
        gate_failures.append("total questions below 20")
    if len(sources) < 2 or any(count < 8 for count in sources.values()):
        gate_failures.append("both source documents need at least 8 questions")
    if audit["duplicate_audit"]["question_duplicate_pairs"]:
        gate_failures.append("question duplicate audit failed")
    if audit["duplicate_audit"]["max_evidence_reuse"] > 5:
        gate_failures.append("evidence reuse is over-concentrated")
    gate_failures.extend(name for name, passed in guards.items() if not passed)
    return {
        "schema_version": "retrieval-foundation-development-dataset-v2",
        "split": "development",
        "source_dataset": source_dataset,
        "generation_id": snapshot.generation_id,
        "child_manifest_hash": snapshot.child_manifest_hash,
        "lexical_index_fingerprint": snapshot.lexical_index_fingerprint,
        "dataset_fingerprint": audit["fingerprint"],
        "question_ids": [str(case["question_id"]) for case in cases],
        "counts": audit["counts"],
        "coverage": coverage,
        "evidence_mapping_complete": not errors,
        "duplicate_audit_passed": not audit["duplicate_audit"]["question_duplicate_pairs"],
        "guards": guards,
        "gate_failures": gate_failures,
        "final_status": "READY_FOR_EFFECTIVENESS_EVAL" if not gate_failures else "BLOCKED_DATASET_QUALITY",
    }
