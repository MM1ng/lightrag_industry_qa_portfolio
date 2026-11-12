"""C1 data-contract checks only; no workers, services, or external calls."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    CandidateAttemptReference,
    ClaimedExecutionContext,
    Document,
    KnowledgeBase,
    LifecycleTask,
    TaskType,
    UpdateJob,
    UpdateJobExecutionStatus,
    UpdateJobStatus,
    UpdateOperation,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import reset_for_testing
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session


def test_legacy_lifecycle_values_are_unchanged() -> None:
    assert {value.value for value in UpdateJobStatus} == {
        "pending",
        "claimed",
        "running",
        "building",
        "validating",
        "ready",
        "succeeded",
        "failed",
        "cancelled",
        "recovery_required",
        "promoted",
        "rolled_back",
    }


@pytest.mark.parametrize("status", list(UpdateJobStatus))
def test_unclassified_legacy_jobs_remain_nullable(status: UpdateJobStatus) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(KnowledgeBase(id="kb", name="contract"))
            session.add(
                UpdateJob(
                    id="job", knowledge_base_id="kb", operation=UpdateOperation.add, status=status
                )
            )
            session.commit()
            row = session.get(UpdateJob, "job")
            assert row.status is status
            assert row.execution_status is None
            assert row.next_run_at is None
            assert row.cancel_requested_at is None
            assert row.execution_finished_at is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("lifecycle", "execution"),
    [
        ("pending", "PENDING"),
        ("building", "RUNNING"),
        ("building", "SUCCEEDED"),
        ("recovery_required", "RECOVERY_REQUIRED"),
        ("failed", "FAILED"),
        ("failed", "SUCCEEDED"),
        ("cancelled", "CANCELLED"),
        ("cancelled", "SUCCEEDED"),
        ("ready", "SUCCEEDED"),
        ("promoted", "SUCCEEDED"),
    ],
)
def test_execution_values_round_trip_without_replacing_lifecycle(
    lifecycle: str,
    execution: str,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(KnowledgeBase(id="kb", name="contract"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id="kb",
                    operation=UpdateOperation.add,
                    status=UpdateJobStatus(lifecycle),
                    execution_status=UpdateJobExecutionStatus(execution),
                )
            )
            session.commit()
            session.expire_all()
            row = session.get(UpdateJob, "job")
            assert row.status.value == lifecycle
            assert row.execution_status.value == execution
            assert (
                session.execute(text("SELECT execution_status FROM update_jobs")).scalar_one()
                == execution
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("lifecycle", "execution"),
    [("promoted", "PENDING"), ("ready", "RUNNING"), ("pending", "BOGUS")],
)
def test_database_rejects_invalid_execution_pairs(lifecycle: str, execution: str) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(KnowledgeBase(id="kb", name="contract"))
            session.add(UpdateJob(id="job", knowledge_base_id="kb", operation=UpdateOperation.add))
            session.commit()
            with pytest.raises(IntegrityError):
                session.execute(
                    text("UPDATE update_jobs SET status=:status, execution_status=:execution"),
                    {"status": lifecycle, "execution": execution},
                )
                session.commit()
            session.rollback()
    finally:
        engine.dispose()


def _context() -> ClaimedExecutionContext:
    return ClaimedExecutionContext(
        job_id="job",
        knowledge_base_id="kb",
        attempt=1,
        worker_id="worker",
        lease_token="secret-token",
        fencing_token=7,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )


def test_ownership_context_is_immutable_and_hides_lease_secret() -> None:
    context = _context()
    assert context.candidate_reference is None
    assert "secret-token" not in repr(context)
    with pytest.raises(FrozenInstanceError):
        context.attempt = 2


def test_candidate_binding_cannot_cross_attempt_or_kb() -> None:
    reference = CandidateAttemptReference(
        job_id="job",
        knowledge_base_id="kb",
        attempt=1,
        candidate_generation_id="candidate",
    )
    context = replace(_context(), candidate_reference=reference)
    assert context.candidate_reference == reference
    for mismatched in (
        replace(reference, attempt=2),
        replace(reference, job_id="other"),
        replace(reference, knowledge_base_id="other"),
    ):
        with pytest.raises(ValueError, match="candidate"):
            replace(context, candidate_reference=mismatched)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": 0},
        {"fencing_token": 0},
        {"lease_token": ""},
        {"worker_id": " "},
        {"lease_expires_at": datetime(2026, 9, 7)},
    ],
)
def test_ownership_context_rejects_incomplete_claim(changes: dict) -> None:
    with pytest.raises(ValueError):
        replace(_context(), **changes)


def test_attempt_staging_paths_are_isolated() -> None:
    from industrial_rag.storage_layout import kb_parsed_attempt_staging_dir

    first = kb_parsed_attempt_staging_dir("a" * 32, "b" * 32, 1, "c" * 32)
    second = kb_parsed_attempt_staging_dir("a" * 32, "b" * 32, 2, "c" * 32)
    assert first != second
    assert first.parts[-5:] == ("attempts", "b" * 32, "1", "c" * 32, "staging")
    assert second.parts[-5:] == ("attempts", "b" * 32, "2", "c" * 32, "staging")


def test_input_snapshot_digest_is_canonical_and_rejects_missing_operation_input() -> None:
    from industrial_rag.services.update_job_snapshot import build_input_snapshot

    common = {
        "operation": "delete",
        "knowledge_base_id": "kb",
        "base_generation": {"generation_id": "generation", "manifest_hash": "c" * 64},
        "configuration": {"parser": "PyMuPDF", "parser_version": "1"},
        "target": {"document_id": "doc", "source_hash": "a" * 64, "version": 4},
    }
    first = build_input_snapshot(**common)
    second = build_input_snapshot(
        **{
            **common,
            "target": {"version": 4, "source_hash": "a" * 64, "document_id": "doc"},
        }
    )
    assert first == second
    assert first["schema_version"] == 1
    assert len(first["input_digest"]) == 64
    with pytest.raises(ValueError, match="source"):
        build_input_snapshot(
            operation="add",
            knowledge_base_id="kb",
            base_generation=None,
            configuration=common["configuration"],
        )


@pytest.mark.asyncio
async def test_claimed_context_is_consumed_without_second_claim_or_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Worker claim is the only claim and only ownership lease for an attempt."""
    from industrial_rag.db.models import UpdateJob
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository
    from industrial_rag.services.incremental_update_service import IncrementalUpdateService
    from industrial_rag.services.kb_lease_service import KBLeaseService
    from industrial_rag.services.update_job_worker import UpdateJobWorker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id="kb", name="single-claim"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id="kb",
                    operation=UpdateOperation.delete,
                    status=UpdateJobStatus.pending,
                )
            )
            await session.commit()

        claimed = await UpdateJobWorker(factory, worker_id="worker").claim_one(now=now)
        assert claimed is not None

        async def unexpected_claim(*_args, **_kwargs):
            raise AssertionError("claimed execution must not issue claim_specific")

        async def unexpected_lease_acquire(*_args, **_kwargs):
            raise AssertionError("claimed execution must not acquire another ownership lease")

        monkeypatch.setattr(UpdateJobRepository, "claim_specific", unexpected_claim)
        monkeypatch.setattr(KBLeaseService, "acquire", unexpected_lease_acquire)
        async with factory() as session:
            service = IncrementalUpdateService(
                session,
                settings=Settings(api_key="phase15c-test-key"),
            )
            received_context = None

            async def build_candidate(kb_id, job_id, *, old_doc=None, context=None):
                nonlocal received_context
                received_context = context
                assert (kb_id, job_id, old_doc) == ("kb", "job", None)
                return {"status": "candidate_built", "job_id": job_id}

            monkeypatch.setattr(service, "_run_job", build_candidate)
            result = await service.execute_claimed_job(claimed.execution_context)

        assert result == {"status": "candidate_built", "job_id": "job"}
        assert received_context == claimed.execution_context
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_update_preserves_persisted_input_snapshot_across_sessions() -> None:
    """A mutable runtime checkpoint must not erase the immutable accepted input."""
    from industrial_rag.db.models import KBOperationLease, UpdateJob
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    snapshot = {
        "schema_version": 1,
        "operation": "delete",
        "knowledge_base_id": "kb",
        "target": {"document_id": "doc", "version": 4, "source_hash": "a" * 64},
        "input_digest": "b" * 64,
    }
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id="kb", name="snapshot"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id="kb",
                    operation=UpdateOperation.delete,
                    status=UpdateJobStatus.claimed,
                    worker_id="worker",
                    lease_token="lease",
                    fencing_token=1,
                    attempt=1,
                    lease_expires_at=now + timedelta(minutes=1),
                    checkpoint={"input_snapshot": snapshot},
                )
            )
            session.add(
                KBOperationLease(
                    knowledge_base_id="kb",
                    lock_owner="worker",
                    lease_token="lease",
                    fencing_token=1,
                    expires_at=now + timedelta(minutes=1),
                )
            )
            await session.commit()
            assert await UpdateJobRepository(session).save_checkpoint(
                "job",
                knowledge_base_id="kb",
                worker_id="worker",
                lease_token="lease",
                fencing_token=1,
                attempt=1,
                checkpoint={"stage": "building_candidate"},
                status=UpdateJobStatus.building,
                now=now,
            )

        async with factory() as session:
            persisted = await UpdateJobRepository(session).get("job")
            assert persisted is not None
            assert persisted.checkpoint == {
                "input_snapshot": snapshot,
                "stage": "building_candidate",
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_claim_cannot_mark_candidate_building() -> None:
    """A superseded UpdateJob claimant cannot bind a candidate generation."""
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id="kb", name="fenced-building"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id="kb",
                    operation=UpdateOperation.delete,
                    status=UpdateJobStatus.claimed,
                    worker_id="current-worker",
                    lease_token="current-lease",
                    fencing_token=2,
                    attempt=2,
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
            await session.commit()
            updated = await UpdateJobRepository(session).mark_building_fenced(
                "job",
                worker_id="stale-worker",
                lease_token="stale-lease",
                fencing_token=1,
                attempt=1,
                candidate_generation_id="candidate",
                now=now,
            )
            assert updated is False

        async with factory() as session:
            job = await session.get(UpdateJob, "job")
            assert job is not None
            assert job.status is UpdateJobStatus.claimed
            assert job.candidate_generation_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claimed_runtime_binds_candidate_through_fenced_repository_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claimed candidate path must never fall back to an unfenced binding."""
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository
    from industrial_rag.services.incremental_update_service import IncrementalUpdateService
    from industrial_rag.services.update_job_worker import UpdateJobWorker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id = "a" * 32
    fenced_calls: list[dict[str, object]] = []
    original_fenced = UpdateJobRepository.mark_building_fenced
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="claimed-fenced-runtime"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id=kb_id,
                    operation=UpdateOperation.delete,
                    status=UpdateJobStatus.pending,
                )
            )
            await session.commit()

        claimed = await UpdateJobWorker(factory, worker_id="worker").claim_one(now=now)
        assert claimed is not None

        async def unfenced_binding(*_args, **_kwargs):
            raise AssertionError("claimed runtime used unfenced candidate binding")

        async def fenced_binding(self, job_id, **kwargs):
            fenced_calls.append({"job_id": job_id, **kwargs})
            return await original_fenced(self, job_id, **kwargs)

        class NoopQdrantClient:
            async def close(self) -> None:
                pass

        async def stop_after_fenced_binding(*_args, **_kwargs):
            raise RuntimeError("stop after fenced candidate binding")

        async with factory() as session:
            service = IncrementalUpdateService(
                session,
                settings=Settings(api_key="phase15c-test-key"),
            )
            monkeypatch.setattr(UpdateJobRepository, "mark_building", unfenced_binding)
            monkeypatch.setattr(UpdateJobRepository, "mark_building_fenced", fenced_binding)
            monkeypatch.setattr(service, "_new_qdrant_client", NoopQdrantClient)
            monkeypatch.setattr(service, "_ensure_collections", stop_after_fenced_binding)
            with pytest.raises(RuntimeError, match="stop after fenced candidate binding"):
                await service.execute_claimed_job(claimed.execution_context)

        assert len(fenced_calls) == 1
        assert {
            key: fenced_calls[0][key]
            for key in ("job_id", "worker_id", "lease_token", "fencing_token", "attempt")
        } == {
            "job_id": "job",
            "worker_id": claimed.execution_context.worker_id,
            "lease_token": claimed.execution_context.lease_token,
            "fencing_token": claimed.execution_context.fencing_token,
            "attempt": claimed.execution_context.attempt,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_claimed_runtime_failure_cannot_overwrite_current_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A superseded executor must not mark the new owner's job as failed."""
    from industrial_rag.db.models import KBOperationLease
    from industrial_rag.services.incremental_update_service import IncrementalUpdateService
    from industrial_rag.services.update_job_worker import UpdateJobWorker
    from sqlalchemy import update

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id = "b" * 32
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="stale-failure-runtime"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id=kb_id,
                    operation=UpdateOperation.delete,
                    status=UpdateJobStatus.pending,
                )
            )
            await session.commit()

        claimed = await UpdateJobWorker(factory, worker_id="worker-a").claim_one(now=now)
        assert claimed is not None

        class NoopQdrantClient:
            async def close(self) -> None:
                pass

        async def supersede_then_fail(*_args, **_kwargs):
            async with factory() as takeover_session:
                await takeover_session.execute(
                    update(UpdateJob)
                    .where(UpdateJob.id == "job")
                    .values(
                        worker_id="worker-b",
                        lease_token="lease-b",
                        fencing_token=claimed.fencing_token + 1,
                        attempt=claimed.attempt + 1,
                        lease_expires_at=now + timedelta(minutes=1),
                        status=UpdateJobStatus.building,
                        current_stage="building_candidate",
                    )
                )
                await takeover_session.execute(
                    update(KBOperationLease)
                    .where(KBOperationLease.knowledge_base_id == kb_id)
                    .values(
                        lock_owner="worker-b",
                        lease_token="lease-b",
                        fencing_token=claimed.fencing_token + 1,
                        expires_at=now + timedelta(minutes=1),
                    )
                )
                await takeover_session.commit()
            raise RuntimeError("candidate build failed after ownership changed")

        async with factory() as session:
            service = IncrementalUpdateService(
                session,
                settings=Settings(api_key="phase15c-test-key"),
            )
            monkeypatch.setattr(service, "_new_qdrant_client", NoopQdrantClient)
            monkeypatch.setattr(service, "_ensure_collections", supersede_then_fail)
            with pytest.raises(RuntimeError, match="ownership changed"):
                await service.execute_claimed_job(claimed.execution_context)

        async with factory() as session:
            current = await session.get(UpdateJob, "job")
            assert current is not None
            assert current.status is UpdateJobStatus.building
            assert current.worker_id == "worker-b"
            assert current.error_code is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_worker_checkpoint_cannot_overwrite_current_owner() -> None:
    """Checkpoint persistence rejects a superseded worker and preserves B's state."""
    from industrial_rag.db.models import KBOperationLease
    from industrial_rag.services.update_job_worker import UpdateJobWorker
    from sqlalchemy import update

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id = "c" * 32
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="stale-checkpoint"))
            session.add(UpdateJob(id="job", knowledge_base_id=kb_id, operation=UpdateOperation.delete))
            await session.commit()
        worker = UpdateJobWorker(factory, worker_id="worker-a")
        claimed = await worker.claim_one(now=now)
        assert claimed is not None
        async with factory() as session:
            await session.execute(
                update(UpdateJob).where(UpdateJob.id == "job").values(
                    worker_id="worker-b", lease_token="lease-b", fencing_token=2,
                    attempt=2, lease_expires_at=now + timedelta(minutes=1),
                    checkpoint={"stage": "b-current"}, status=UpdateJobStatus.building,
                )
            )
            await session.execute(
                update(KBOperationLease).where(KBOperationLease.knowledge_base_id == kb_id).values(
                    lock_owner="worker-b", lease_token="lease-b", fencing_token=2,
                    expires_at=now + timedelta(minutes=1),
                )
            )
            await session.commit()

        assert not await worker.save_checkpoint(
            claimed, checkpoint={"stage": "a-stale"}, status=UpdateJobStatus.building, now=now
        )
        async with factory() as session:
            current = await session.get(UpdateJob, "job")
            assert current is not None
            assert current.checkpoint == {"stage": "b-current"}
            assert current.worker_id == "worker-b"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_worker_cannot_complete_after_kb_lease_moves_to_another_owner() -> None:
    """Success must be fenced by the current KB operation lease as well as the job claim."""
    from industrial_rag.db.models import KBOperationLease
    from industrial_rag.services.update_job_worker import UpdateJobWorker
    from sqlalchemy import update

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id = "d" * 32
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="stale-completion"))
            session.add(UpdateJob(id="job", knowledge_base_id=kb_id, operation=UpdateOperation.delete))
            await session.commit()

        worker = UpdateJobWorker(factory, worker_id="worker-a")
        claimed = await worker.claim_one(now=now)
        assert claimed is not None

        async with factory() as session:
            await session.execute(
                update(KBOperationLease)
                .where(KBOperationLease.knowledge_base_id == kb_id)
                .values(
                    lock_owner="worker-b",
                    lease_token="lease-b",
                    fencing_token=claimed.fencing_token + 1,
                    expires_at=now + timedelta(minutes=1),
                )
            )
            await session.commit()

        assert not await worker.complete(claimed, result={"owner": "a"}, now=now)
        async with factory() as session:
            current = await session.get(UpdateJob, "job")
            assert current is not None
            assert current.status is UpdateJobStatus.claimed
            assert current.result is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("add", "replace", "delete"))
async def test_document_operation_persists_accepted_input_snapshot_across_sessions(
    tmp_path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Accepted document work retains its complete intent after its creator exits."""
    from industrial_rag.services.incremental_update_service import IncrementalUpdateService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), f"Phase15-C1B {operation}")
    content = pdf.tobytes()
    pdf.close()
    kb_id = "a" * 32
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            kb = KnowledgeBase(id=kb_id, name="snapshot-creation")
            existing = Document(
                id="existing",
                knowledge_base_id=kb.id,
                original_file_name="existing.pdf",
                stored_file_name="existing.pdf",
                file_path=str(tmp_path / "existing.pdf"),
                file_hash="a" * 64,
                file_size=1,
            )
            session.add_all((kb, existing))
            await session.commit()
            service = IncrementalUpdateService(
                session,
                settings=Settings(api_key="phase15c-test-key", working_dir=tmp_path),
            )

            async def defer_execution(_kb_id: str, job_id: str, **_kwargs):
                return {"update_job_id": job_id}

            monkeypatch.setattr(service, "_execute_persisted_job", defer_execution)
            if operation == "add":
                result = await service.add_document(
                    kb.id, original_file_name="added.pdf", content=content
                )
            elif operation == "replace":
                result = await service.replace_document(kb.id, existing.id, content=content)
            else:
                result = await service.delete_document(kb.id, existing.id)

        async with factory() as session:
            job = await session.get(UpdateJob, result["update_job_id"])
            assert job is not None
            snapshot = job.checkpoint["input_snapshot"]
            assert snapshot["schema_version"] == 1
            assert snapshot["operation"] == operation
            assert snapshot["knowledge_base_id"] == kb_id
            assert snapshot["configuration"]["parser"] is not None
            assert len(snapshot["input_digest"]) == 64
            if operation == "add":
                assert snapshot["source"]["document_id"] == job.document_id
            elif operation == "replace":
                assert snapshot["target"]["document_id"] == "existing"
                assert snapshot["source"] == snapshot["replacement"]
            else:
                assert snapshot["target"]["document_id"] == "existing"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", (UpdateOperation.reparse, UpdateOperation.reindex))
async def test_legacy_operation_persists_accepted_input_snapshot_across_sessions(
    tmp_path, operation: UpdateOperation
) -> None:
    """Legacy reparse/reindex adapters capture intent before lifecycle execution."""
    from industrial_rag.repositories.document_repository import DocumentRepository
    from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
    from industrial_rag.repositories.task_repository import TaskRepository
    from industrial_rag.services.handler_impls import _create_update_job_from_document_task
    from industrial_rag.services.task_context import TaskExecutionContext

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kb_id = "b" * 32
    document_id = "c" * 32
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            kb = KnowledgeBase(id=kb_id, name="legacy-snapshot")
            document = Document(
                id=document_id,
                knowledge_base_id=kb.id,
                original_file_name="manual.pdf",
                stored_file_name="manual.pdf",
                file_path=str(tmp_path / "manual.pdf"),
                file_hash="d" * 64,
                file_size=1,
            )
            generation = VectorIndexGeneration(
                knowledge_base_id=kb.id,
                backend="qdrant",
                generation="active-generation",
                status=VectorIndexGenerationStatus.active,
                workspace_path=str(tmp_path / "active"),
                document_manifest_hash="e" * 64,
                child_chunks_manifest_hash="f" * 64,
                embedding_config_hash="1" * 64,
                chunking_config_hash="2" * 64,
            )
            session.add_all((kb, document, generation))
            await session.flush()
            kb.active_vector_generation_id = generation.id
            task = LifecycleTask(
                knowledge_base_id=kb.id,
                document_id=document.id if operation is UpdateOperation.reparse else None,
                task_type=TaskType.reparse if operation is UpdateOperation.reparse else TaskType.reindex,
            )
            session.add(task)
            await session.commit()
            context = TaskExecutionContext(
                task=task,
                kb_repo=KnowledgeBaseRepository(session),
                doc_repo=DocumentRepository(session),
                task_repo=TaskRepository(session),
                settings=Settings(api_key="phase15c-test-key"),
            )
            created = await _create_update_job_from_document_task(context, operation=operation)
            assert created.success is True
            assert created.result is not None
            job_id = created.result["update_job_id"]
            await session.commit()

        async with factory() as session:
            job = await session.get(UpdateJob, job_id)
            assert job is not None
            snapshot = job.checkpoint["input_snapshot"]
            assert snapshot["operation"] == operation.value
            assert snapshot["knowledge_base_id"] == kb_id
            if operation is UpdateOperation.reparse:
                assert snapshot["source"]["document_id"] == document_id
                assert snapshot["target"]["document_id"] == document_id
            else:
                assert snapshot["base_generation"]["generation_id"] == generation.id
                assert snapshot["document_bindings"] == [
                    {
                        "document_id": document_id,
                        "version": 1,
                        "source_hash": "d" * 64,
                        "stored_file_name": "manual.pdf",
                    }
                ]
    finally:
        await engine.dispose()


def test_active_migration_adds_execution_contract_without_classifying_legacy_rows(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "phase15c-c1b.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    reset_for_testing()
    config = Config("alembic.ini")
    command.upgrade(config, "f15b0a1c2d3e")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO update_jobs (id, knowledge_base_id, operation, status,
                retry_count, created_by, created_at, updated_at)
                VALUES ('job', 'kb', 'add', 'ready', 0, 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""
            )
        )
    command.upgrade(config, "head")
    columns = {column["name"] for column in inspect(engine).get_columns("update_jobs")}
    assert {"execution_status", "next_run_at", "cancel_requested_at", "execution_finished_at"} <= columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT execution_status FROM update_jobs WHERE id='job'")).scalar_one() is None
    engine.dispose()
    reset_for_testing()
