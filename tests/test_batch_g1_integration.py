"""Real admission/persistence/Promote chain, with local SQLite and mock HTTP."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from industrial_rag.db.models import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    UpdateJob,
    UpdateJobStatus,
    UpdateOperation,
    ValidationRun,
    ValidationRunStatus,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.errors import AppError
from industrial_rag.services.generation_artifacts import freeze_generation_child_chunks
from industrial_rag.services.generation_content_fingerprint import stable_hash
from industrial_rag.services.generation_validation_service import GenerationValidationService
from industrial_rag.services.incremental_update_service import IncrementalUpdateService
from industrial_rag.services.validation_gate_service import ValidationGateService
from industrial_rag.services.validation_policy import CONTRACT_VERSION, resolve_validation_policy
from sqlalchemy import select

from test_batch_g1_contract import suite_fixture
from test_phase9b_validation_gate import validation_state as _validation_fixture

validation_state = _validation_fixture


@pytest_asyncio.fixture
async def bootstrap_state(validation_state, monkeypatch):
    factory, settings, qdrant, kb_id, generation_id, tmp_path = validation_state
    settings = replace(
        settings, validation_base_url="http://candidate.invalid", admin_api_key="test-admin"
    )
    validation_state = factory, settings, qdrant, kb_id, generation_id, tmp_path
    rows, docs, records = suite_fixture()
    root = tmp_path / "suites"
    (root / "newdomain").mkdir(parents=True)
    questions_path = root / "newdomain" / "questions.jsonl"
    questions_path.write_text("".join(json.dumps(q) + "\n" for q in rows), encoding="utf-8")
    manifest = {
        "contract": CONTRACT_VERSION,
        "suite_id": "newdomain",
        "version": "newdomain-v1",
        "documents": docs,
    }
    (root / "newdomain" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sha = stable_hash(
        {
            "manifest": manifest,
            "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        }
    )
    (root / "approved_suites.json").write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "suite_id": "newdomain",
                        "sha256": sha,
                        "approved_by": "independent-release-review",
                        "approved_at": "2026-10-12",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("industrial_rag.services.validation_policy.SUITES_ROOT", root)
    async with factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        kb.active_vector_generation_id = None
        old = await session.get(VectorIndexGeneration, "c" * 32)
        old.status = VectorIndexGenerationStatus.shadow
        generation = await session.get(VectorIndexGeneration, generation_id)
        generation.status = VectorIndexGenerationStatus.building
        doc = Document(
            id="d" * 32,
            knowledge_base_id=kb_id,
            original_file_name="new.pdf",
            stored_file_name="new.pdf",
            file_hash="a" * 64,
            version=1,
            status=DocumentStatus.indexed,
            is_active=False,
        )
        session.add(doc)
        snapshot = freeze_generation_child_chunks(
            tmp_path / "workspace",
            generation_id="g1",
            document_children=[(doc, records[0])],
            replace_existing=True,
        )
        generation.child_chunks_manifest_hash = snapshot.child_manifest_hash
        session.add(
            UpdateJob(
                id="e" * 32,
                knowledge_base_id=kb_id,
                candidate_generation_id=generation_id,
                operation=UpdateOperation.add,
                document_id=doc.id,
                status=UpdateJobStatus.building,
                current_stage="awaiting_validation",
                created_by="test",
            )
        )
        await session.commit()
    calls = []

    class MockHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, endpoint, *, json, headers):
            q = next(q for q in rows if q["question"] == json["query"])
            calls.append(q["id"])
            positive = q["expects_evidence"]
            return httpx.Response(
                200,
                json={
                    "answer": "supported" if positive else "",
                    "status": "success" if positive else "insufficient_evidence",
                    "generation_id": generation_id,
                    "citations": [
                        {
                            "document_id": "d" * 32,
                            "chunk_id": "c1",
                            "generation_id": generation_id,
                            "document_name": "new.pdf",
                            "page": 1,
                        }
                    ]
                    if positive
                    else [],
                },
            )

    monkeypatch.setattr("industrial_rag.services.canonical_validation_runner.AsyncClient", MockHTTP)

    # Structural gates are tested separately; isolate this integration to acceptance data.
    async def structural_pass(*args, **kwargs):
        return {"passed": True, "status": "PASS"}

    for name in ("_doc_registration", "_count_gate", "_payload_gate"):
        monkeypatch.setattr(GenerationValidationService, name, structural_pass)
    return validation_state, root, sha, calls


async def validate(state, **kwargs):
    factory, settings, qdrant, kb_id, generation_id, _ = state
    async with factory() as session:
        return await IncrementalUpdateService(
            session, settings=settings, qdrant_client_factory=lambda: qdrant
        ).validate_generation(kb_id, generation_id, **kwargs)


@pytest.mark.asyncio
async def test_new_domain_without_suite_blocks_without_http_and_preserves_candidate(
    bootstrap_state,
):
    state, _, _, calls = bootstrap_state
    report = await validate(state)
    assert report["outcome"] == "BLOCKED"
    assert report["applicability"] == "NOT_APPLICABLE"
    assert calls == []
    assert report["report"]["run"]["positive_citation_match_rate"] is None
    factory, _, _, kb_id, generation_id, _ = state
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        job = await session.get(UpdateJob, "e" * 32)
        doc = await session.get(Document, "d" * 32)
        run = (await session.execute(select(ValidationRun))).scalars().one()
        assert run.status is ValidationRunStatus.blocked
        assert run.qdrant_content_fingerprint is None
        assert generation.status is VectorIndexGenerationStatus.building
        assert generation.validated_fingerprint is None
        assert job.status is UpdateJobStatus.building and job.current_stage == "awaiting_validation"
        assert doc.status is DocumentStatus.indexed and not doc.is_active
        assert (await session.get(KnowledgeBase, kb_id)).active_vector_generation_id is None


@pytest.mark.asyncio
async def test_approved_new_domain_executes_all_twenty_and_can_pass_gate(bootstrap_state):
    state, _, sha, calls = bootstrap_state
    report = await validate(state, suite_id="newdomain", suite_sha256=sha)
    assert report["outcome"] == "PASSED", report
    assert len(calls) == 20
    factory, settings, qdrant, kb_id, generation_id, _ = state
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        run = await ValidationGateService(
            session, settings=settings, qdrant_client_factory=lambda: qdrant
        ).require_eligible(kb_id, generation)
        assert run.status is ValidationRunStatus.passed
        assert generation.status is VectorIndexGenerationStatus.ready
        assert (await session.get(KnowledgeBase, kb_id)).active_vector_generation_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper", ["approval", "question", "base_active", "duplicate_execution", "skipped_gate"]
)
async def test_post_validation_changes_block_promote(bootstrap_state, tamper):
    state, root, sha, _ = bootstrap_state
    report = await validate(state, suite_id="newdomain", suite_sha256=sha)
    assert report["passed"]
    factory, settings, qdrant, kb_id, generation_id, _ = state
    async with factory() as session:
        if tamper == "approval":
            (root / "approved_suites.json").write_text('{"suites":[]}', encoding="utf-8")
        elif tamper == "question":
            path = root / "newdomain" / "questions.jsonl"
            path.write_text(
                path.read_text(encoding="utf-8").replace("What is fact 0?", "Changed question?"),
                encoding="utf-8",
            )
        elif tamper == "base_active":
            active = await session.get(VectorIndexGeneration, "c" * 32)
            active.status = VectorIndexGenerationStatus.active
            (await session.get(KnowledgeBase, kb_id)).active_vector_generation_id = active.id
        else:
            run = (await session.execute(select(ValidationRun))).scalars().one()
            from pathlib import Path

            path = Path(run.result_artifact_path)
            payload = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if tamper == "duplicate_execution":
                payload[-1] = payload[-2]
            else:
                payload[0]["summary"]["gates"]["db_integrity"] = {
                    "status": "SKIPPED",
                    "passed": False,
                }
            path.write_text("".join(json.dumps(r) + "\n" for r in payload), encoding="utf-8")
            run.result_artifact_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        await session.commit()
        generation = await session.get(VectorIndexGeneration, generation_id)
        with pytest.raises(AppError):
            await ValidationGateService(
                session, settings=settings, qdrant_client_factory=lambda: qdrant
            ).require_eligible(kb_id, generation)


@pytest.mark.asyncio
async def test_regression_inherits_new_domain_scope_even_if_candidate_loses_sources(
    bootstrap_state,
):
    state, _, sha, _ = bootstrap_state
    await validate(state, suite_id="newdomain", suite_sha256=sha)
    factory, _, _, kb_id, generation_id, tmp_path = state
    async with factory() as session:
        active = await session.get(VectorIndexGeneration, generation_id)
        active.status = VectorIndexGenerationStatus.active
        active.activated_at = datetime.now(UTC)
        (await session.get(KnowledgeBase, kb_id)).active_vector_generation_id = active.id
        candidate = await session.get(VectorIndexGeneration, "c" * 32)
        candidate.generation = "g2"
        candidate.workspace_path = str(tmp_path / "regression")
        snapshot = freeze_generation_child_chunks(
            tmp_path / "regression", generation_id="g2", document_children=[]
        )
        candidate.child_chunks_manifest_hash = snapshot.child_manifest_hash
        await session.commit()
        resolved = await resolve_validation_policy(session, kb_id, candidate)
        assert resolved.admitted
        assert len(resolved.policy.questions) == 20
        assert resolved.snapshot["profile"] == "regression"
        with pytest.raises(AppError):
            await resolve_validation_policy(
                session, kb_id, candidate, suite_id="newdomain", suite_sha256=sha
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected",
    [
        ("15_matches", "PASSED"),
        ("14_matches", "FAILED"),
        ("bad_negative", "FAILED"),
        ("http_error", "FAILED"),
        ("duplicate", "FAILED"),
        ("wrong_generation", "FAILED"),
        ("summary_only", "FAILED"),
    ],
)
async def test_quality_failure_is_distinct_and_threshold_is_not_lowered(
    bootstrap_state, case, expected
):
    state, _, sha, calls = bootstrap_state
    from g1_test_support import passing_records

    factory, _, _, kb_id, generation_id, _ = state
    async with factory() as session:
        generation = await session.get(VectorIndexGeneration, generation_id)
        resolved = await resolve_validation_policy(
            session, kb_id, generation, suite_id="newdomain", suite_sha256=sha
        )
    records = passing_records(generation_id, resolved.policy)
    if case in {"15_matches", "14_matches"}:
        for r in records[: 3 if case == "15_matches" else 4]:
            r["citation_match"] = False
    if case == "bad_negative":
        records[-1]["negative_case_passed"] = False
    if case == "http_error":
        records[0]["status_code"] = 500
    if case == "duplicate":
        records[-1] = records[-2]
    if case == "wrong_generation":
        records[0]["actual_generation_id"] = "another"

    async def runner(*args):
        return {
            "results": records if case != "summary_only" else [],
            "golden_subset_regression": True,
        }

    report = await validate(state, suite_id="newdomain", suite_sha256=sha, golden_runner=runner)
    assert report["outcome"] == expected
    assert report["admission"] == "ADMITTED"
    assert calls == []
    async with factory() as session:
        assert (await session.get(KnowledgeBase, kb_id)).active_vector_generation_id is None
        if expected == "FAILED":
            assert (
                await session.get(VectorIndexGeneration, generation_id)
            ).status is VectorIndexGenerationStatus.failed
            assert (await session.get(Document, "d" * 32)).status is DocumentStatus.failed


@pytest.mark.asyncio
async def test_active_identity_inconsistency_and_retries_never_publish(bootstrap_state):
    state, _, sha, calls = bootstrap_state
    factory, _, _, kb_id, _, _ = state
    async with factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        kb.active_vector_generation_id = "c" * 32  # points at a shadow row
        await session.commit()
    first = await validate(state, suite_id="newdomain", suite_sha256=sha)
    second = await validate(state, suite_id="newdomain", suite_sha256=sha)
    assert first["outcome"] == second["outcome"] == "BLOCKED"
    assert "ACTIVE_IDENTITY_INCONSISTENT" in first["report"]["policy_snapshot"]["reasons"]
    assert first["report"]["validation_run_id"] != second["report"]["validation_run_id"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["wrong_sha", "unapproved", "malformed_package"])
async def test_unapproved_or_invalid_package_never_calls_http(bootstrap_state, problem):
    state, root, sha, calls = bootstrap_state
    if problem == "wrong_sha":
        sha = "0" * 64
    elif problem == "unapproved":
        (root / "approved_suites.json").write_text('{"suites":[]}', encoding="utf-8")
    else:
        (root / "newdomain" / "questions.jsonl").write_text("bad json\n", encoding="utf-8")
    report = await validate(state, suite_id="newdomain", suite_sha256=sha)
    assert report["outcome"] == "BLOCKED" and not report["passed"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem,expected",
    [("unsupported_db", "BLOCKED"), ("failed_db", "FAILED"), ("unconfigured_runner", "BLOCKED")],
)
async def test_unmeasured_execution_is_never_a_quality_pass(
    bootstrap_state, monkeypatch, problem, expected
):
    state, _, sha, calls = bootstrap_state
    if problem == "unconfigured_runner":
        factory, settings, qdrant, kb_id, generation_id, tmp_path = state
        state = (
            factory,
            replace(settings, validation_base_url=""),
            qdrant,
            kb_id,
            generation_id,
            tmp_path,
        )
    else:

        async def integrity(*args):
            return {"status": "SKIPPED" if problem == "unsupported_db" else "FAIL", "passed": False}

        monkeypatch.setattr(GenerationValidationService, "_db_integrity", integrity)
    report = await validate(state, suite_id="newdomain", suite_sha256=sha)
    assert report["outcome"] == expected
    assert report["admission"] == "ADMITTED"
    assert report["report"]["run"]["measurement_status"] == "NOT_MEASURED"
    assert report["report"]["run"]["positive_citation_match_rate"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_expired_run_cannot_promote(bootstrap_state):
    state, _, sha, _ = bootstrap_state
    await validate(state, suite_id="newdomain", suite_sha256=sha)
    factory, settings, qdrant, kb_id, generation_id, _ = state
    async with factory() as session:
        from datetime import timedelta

        run = (await session.execute(select(ValidationRun))).scalars().one()
        run.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        generation = await session.get(VectorIndexGeneration, generation_id)
        with pytest.raises(AppError):
            await ValidationGateService(
                session, settings=settings, qdrant_client_factory=lambda: qdrant
            ).require_eligible(kb_id, generation)
