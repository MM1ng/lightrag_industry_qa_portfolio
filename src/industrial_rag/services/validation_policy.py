"""Server-owned G1 policy resolution shared by Validate and Promote.

Suite approvals are reviewed release data, never authority supplied in a request.
The legacy twenty questions remain the regression baseline for existing Active KBs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from industrial_rag.config import PROJECT_ROOT
from industrial_rag.db.models import KnowledgeBase, VectorIndexGeneration
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.services.generation_artifacts import (
    GenerationArtifactError,
    generation_artifact_evidence,
)
from industrial_rag.services.generation_content_fingerprint import stable_hash
from industrial_rag.services.golden_set_policy import GoldenSetPolicy, load_canonical_policy

CONTRACT_VERSION = "g1-acceptance-v1"
SUITES_ROOT = PROJECT_ROOT / "evaluation" / "bootstrap_suites"
THRESHOLDS = {"positive_count": 18, "negative_count": 2, "positive_matches": 15}
REQUIRED_GATES = frozenset(
    {
        "frozen_snapshot_consistency",
        "acceptance_data",
        "db_integrity",
        "document_registration_consistency",
        "counts",
        "payload_completeness",
        "generation_mixing",
        "canonical_question_identity",
        "generation_identity",
        "citation_traceability",
        "golden_subset_regression",
        "http_success_1_0",
        "trace_complete_1_0",
        "negative_unsupported_0",
        "no_5xx",
        "no_fabricated_citation",
        "no_secret_leak",
        "no_old_document_reference",
    }
)


def assess_policy(policy: GoldenSetPolicy, sources: set[str], *, bootstrap: bool) -> dict:
    positives = [q for q in policy.questions if q.get("expects_evidence") is True]
    negatives = [q for q in policy.questions if q.get("expects_evidence") is False]
    applicable = (
        positives
        if not bootstrap
        else [
            q
            for q in positives
            if q.get("expected_citations")
            and all(c.get("source_file") in sources for c in q["expected_citations"])
        ]
    )
    admitted = (
        len(applicable) == len(positives) == 18
        and len(negatives) == 2
        and len(policy.questions) == 20
    )
    return {
        "applicability": "APPLICABLE" if applicable else "NOT_APPLICABLE",
        "admission": "ADMITTED" if admitted else "INSUFFICIENT_ACCEPTANCE_DATA",
        "positive_count": len(applicable),
        "negative_count": len(negatives),
        "excluded_ids": [q["id"] for q in positives if q not in applicable],
    }


def admit_bootstrap_suite(
    questions: list[dict], documents: list[dict], records: list[dict], *, bind_sources: bool = True
) -> list[str]:
    """Validate review evidence, independent facts, coverage and frozen provenance."""
    problems: list[str] = []
    positives = [q for q in questions if q.get("expects_evidence") is True]
    negatives = [q for q in questions if q.get("expects_evidence") is False]
    if len(questions) != 20 or len(positives) != 18 or len(negatives) != 2:
        problems.append("EXACT_18_POSITIVE_2_NEGATIVE_REQUIRED")
    ids = [q.get("id") for q in questions]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        problems.append("UNIQUE_QUESTION_IDS_REQUIRED")
    facts: set[str] = set()
    spans: set[tuple] = set()
    topics: Counter = Counter()
    covered: set[str] = set()
    by_name = {d.get("document_name"): d for d in documents}
    if not documents or len(by_name) != len(documents):
        problems.append("UNAMBIGUOUS_DOCUMENT_SCOPE_REQUIRED")
    for q in questions:
        if not str(q.get("question") or "").strip():
            problems.append("QUESTION_REQUIRED")
        author, reviewer = q.get("authored_by"), q.get("reviewed_by")
        try:
            datetime.fromisoformat(q.get("reviewed_at", "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            problems.append("REVIEW_TIME_REQUIRED")
        if not author or not reviewer or author == reviewer or q.get("approved") is not True:
            problems.append("INDEPENDENT_REVIEW_REQUIRED")
        citations = q.get("expected_citations")
        if q.get("expects_evidence") is True:
            fact, topic = q.get("fact_id"), q.get("topic")
            if not fact or fact in facts or not topic or not q.get("reference_fact"):
                problems.append("INDEPENDENT_FACT_AND_TOPIC_REQUIRED")
            facts.add(fact)
            topics[topic] += 1
            if not isinstance(citations, list) or not citations:
                problems.append("POSITIVE_CITATIONS_REQUIRED")
                continue
            fact_spans = []
            for c in citations:
                source, page = c.get("source_file"), c.get("page_number")
                doc = by_name.get(source, {})
                support = c.get("support_text")
                if (
                    type(page) is not int
                    or page < 1
                    or not support
                    or c.get("support_text_sha256") != stable_hash(support)
                    or not re.fullmatch(r"[0-9a-f]{64}", str(c.get("file_sha256", "")))
                    or c.get("file_sha256") != doc.get("file_hash")
                    or c.get("document_version") != doc.get("document_version")
                ):
                    problems.append("SOURCE_REFERENCE_INVALID")
                if bind_sources and not any(
                    r.get("chunk_id") == c.get("chunk_id")
                    and r.get("document_name") == source
                    and int(r.get("page_start") or 0)
                    <= (page or 0)
                    <= int(r.get("page_end") or r.get("page_start") or 0)
                    and isinstance(support, str)
                    and support in str(r.get("content") or "")
                    for r in records
                ):
                    problems.append("FROZEN_SUPPORT_NOT_FOUND")
                covered.add(source)
                fact_spans.append((source, page, c.get("chunk_id"), support))
            span = tuple(sorted(fact_spans, key=str))
            if span in spans:
                problems.append("DUPLICATE_SUPPORT_SPAN")
            spans.add(span)
        elif q.get("expects_evidence") is False:
            if citations != [] or not q.get("rationale") or q.get("checked_documents") != documents:
                problems.append("NEGATIVE_CORPUS_REVIEW_REQUIRED")
        else:
            problems.append("EXPLICIT_EVIDENCE_TYPE_REQUIRED")
    if len(topics) < 3 or any(n < 3 for n in topics.values()) or covered != set(by_name):
        problems.append("TOPIC_AND_DOCUMENT_COVERAGE_REQUIRED")
    if {q.get("negative_kind") for q in negatives} != {"missing_detail", "unsupported_assertion"}:
        problems.append("BOTH_NEGATIVE_TYPES_REQUIRED")
    if len({q.get("question") for q in negatives}) != len(negatives):
        problems.append("DISTINCT_NEGATIVE_QUESTIONS_REQUIRED")
    return sorted(set(problems))


def load_approved_suite(suite_id: str, expected_sha: str, *, root: Path | None = None):
    root = root or SUITES_ROOT
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", suite_id):
        raise ValueError("INVALID_SUITE_ID")
    approvals_raw = (root / "approved_suites.json").read_bytes()
    approvals = json.loads(approvals_raw)
    manifest = json.loads((root / suite_id / "manifest.json").read_bytes())
    raw = (root / suite_id / "questions.jsonl").read_bytes()
    questions = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    package_sha = stable_hash(
        {"manifest": manifest, "questions_sha256": hashlib.sha256(raw).hexdigest()}
    )
    approval = next(
        (
            a
            for a in approvals["suites"]
            if a.get("suite_id") == suite_id and a.get("sha256") == package_sha
        ),
        None,
    )
    if (
        package_sha != expected_sha
        or approval is None
        or not approval.get("approved_by")
        or not approval.get("approved_at")
        or manifest.get("suite_id") != suite_id
        or not manifest.get("version")
        or manifest.get("contract") != CONTRACT_VERSION
    ):
        raise ValueError("SUITE_NOT_APPROVED_OR_CHANGED")
    return (
        GoldenSetPolicy(
            str(manifest["version"]),
            root / suite_id / "questions.jsonl",
            package_sha,
            tuple(questions),
        ),
        manifest,
        {"record": approval, "registry_sha256": hashlib.sha256(approvals_raw).hexdigest()},
    )


@dataclass(frozen=True)
class ResolvedValidationPolicy:
    policy: GoldenSetPolicy
    snapshot: dict[str, Any]

    @property
    def admitted(self) -> bool:
        return self.snapshot["admission"] == "ADMITTED"

    @property
    def fingerprint(self) -> str:
        return stable_hash(self.snapshot)


async def resolve_validation_policy(
    session, kb_id: str, generation, *, suite_id: str | None = None, suite_sha256: str | None = None
) -> ResolvedValidationPolicy:
    kb = await session.get(KnowledgeBase, kb_id)
    rows = (
        (
            await session.execute(
                select(VectorIndexGeneration).where(
                    VectorIndexGeneration.knowledge_base_id == kb_id
                )
            )
        )
        .scalars()
        .all()
    )
    active_rows = [g for g in rows if g.status.value == "active"]
    active = active_rows[0] if len(active_rows) == 1 else None
    pointer = kb.active_vector_generation_id if kb else None
    identity_ok = bool(kb) and (
        (active is not None and pointer == active.id)
        or (
            not active_rows
            and pointer is None
            and not any(g.activated_at or g.retired_at for g in rows)
        )
    )
    if active and (suite_id or suite_sha256):
        raise AppError(
            AppErrorCode.generation_invalid_state,
            "已有 Active 的验收不能选择 Bootstrap 套件。",
            status_code=409,
        )
    bootstrap = active is None
    anchor = (getattr(active, "accepted_policy_snapshot", None) or {}) if active else {}
    selected_id = anchor.get("suite_id") if active else suite_id
    selected_sha = anchor.get("suite_sha256") if active else suite_sha256
    policy = load_canonical_policy()
    approval, problems = None, []
    try:
        artifact = generation_artifact_evidence(
            Path(generation.workspace_path),
            expected_generation_id=generation.generation,
            expected_child_manifest_hash=generation.child_chunks_manifest_hash,
        )
    except GenerationArtifactError:
        problems.append("FROZEN_SNAPSHOT_INVALID")
        artifact = SimpleNamespace(
            manifest=SimpleNamespace(documents=()), records=(), manifest_bytes_sha256=None
        )
    documents = list(artifact.manifest.documents)
    sources = {str(d["document_name"]) for d in documents}
    decision = assess_policy(policy, sources, bootstrap=bootstrap)
    if selected_id or selected_sha:
        try:
            policy, manifest, approval = load_approved_suite(selected_id or "", selected_sha or "")
            declared_documents = manifest.get("documents", [])
            if bootstrap and declared_documents != [
                {k: d[k] for k in ("document_name", "file_hash", "document_version")}
                for d in documents
            ]:
                problems.append("SUITE_CORPUS_CHANGED")
            problems.extend(
                admit_bootstrap_suite(
                    list(policy.questions),
                    declared_documents,
                    list(artifact.records),
                    bind_sources=bootstrap,
                )
            )
            decision = assess_policy(policy, sources, bootstrap=bootstrap)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            problems.append("SUITE_NOT_APPROVED_OR_INVALID")
            decision["admission"] = "INSUFFICIENT_ACCEPTANCE_DATA"
    if not identity_ok:
        problems.append("ACTIVE_IDENTITY_INCONSISTENT")
    if problems:
        decision["admission"] = "INSUFFICIENT_ACCEPTANCE_DATA"
    snapshot = {
        "contract": CONTRACT_VERSION,
        "profile": "bootstrap" if bootstrap else "regression",
        "suite_id": selected_id,
        "suite_sha256": selected_sha,
        "approval": approval,
        "source_sha256": policy.source_sha256,
        "version": policy.version,
        "questions": list(policy.questions),
        "effective_questions_sha256": stable_hash(policy.questions),
        "thresholds": THRESHOLDS,
        "base_active_id": active.id if active else None,
        "candidate_generation_id": generation.id,
        "candidate_manifest_sha256": artifact.manifest_bytes_sha256,
        **decision,
        "raw_positive_count": sum(q.get("expects_evidence") is True for q in policy.questions),
        "raw_negative_count": sum(q.get("expects_evidence") is False for q in policy.questions),
        "admitted_positive_count": 18 if decision["admission"] == "ADMITTED" else 0,
        "admitted_negative_count": 2 if decision["admission"] == "ADMITTED" else 0,
        "reasons": problems
        or ([] if decision["admission"] == "ADMITTED" else [decision["admission"]]),
    }
    return ResolvedValidationPolicy(policy, snapshot)
