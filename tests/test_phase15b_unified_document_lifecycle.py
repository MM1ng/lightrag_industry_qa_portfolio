"""Phase15-B Step1 contract tests for UpdateJob operation persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    Document,
    KnowledgeBase,
    LifecycleTask,
    TaskType,
    UpdateJobStatus,
    UpdateOperation,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import reset_for_testing
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from industrial_rag.services.document_service import DocumentService
from industrial_rag.services.generation_artifacts import freeze_generation_child_chunks
from industrial_rag.services.generation_fingerprint_service import (
    build_generation_fingerprint,
)
from industrial_rag.services.incremental_update_service import IncrementalUpdateService
from industrial_rag.services.parse_service import load_child_chunks, load_parent_chunks
from industrial_rag.services.task_context import TaskExecutionContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def update_job_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        kb = KnowledgeBase(
            id="a" * 32,
            name="phase15b",
            workspace_path="C:/tmp/phase15b",
            upload_path="C:/tmp/phase15b/uploads",
            parsed_path="C:/tmp/phase15b/parsed",
        )
        document = Document(
            id="b" * 32,
            knowledge_base_id=kb.id,
            original_file_name="manual.pdf",
            stored_file_name="manual.pdf",
            file_path="C:/tmp/phase15b/uploads/manual.pdf",
            file_hash="c" * 64,
            file_size=1,
        )
        session.add_all([kb, document])
        await session.commit()
        yield session, kb, document
    await engine.dispose()


class _CandidateBuildQdrant:
    """Minimal offline client for generation creation and count checks."""

    async def collection_exists(self, _name: str) -> bool:
        return False

    async def create_collection(self, **_kwargs) -> None:
        return None

    async def count(self, _name: str, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(count=0)

    async def close(self) -> None:
        return None


async def _service_with_active_snapshot(
    session,
    kb: KnowledgeBase,
    document: Document,
    tmp_path: Path,
    monkeypatch,
) -> tuple[IncrementalUpdateService, VectorIndexGeneration]:
    """Prepare a frozen active generation without invoking external services."""
    data_root = tmp_path / "kb-data"
    monkeypatch.setenv("KB_DATA_ROOT", str(data_root))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "phase15b-test-key")
    source_path = data_root / kb.id / "uploads" / document.stored_file_name
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_pdf = pymupdf.open()
    source_pdf.new_page().insert_text((72, 72), "Phase15-B candidate source")
    source_pdf.save(source_path)
    source_pdf.close()
    document.file_path = str(source_path)

    client = _CandidateBuildQdrant()
    service = IncrementalUpdateService(
        session,
        qdrant_client_factory=lambda: client,
    )
    await service._parse_document_pymupdf(kb, document)

    parsed_dir = data_root / kb.id / "parsed" / "documents" / document.id
    children = load_child_chunks(parsed_dir)
    parents = load_parent_chunks(parsed_dir)
    active_workspace = data_root / kb.id / "qdrant" / "active" / "workspace"
    active_token = "gphase15bactive"
    snapshot_pairs = [(document, child) for child in children]
    snapshot_parent_pairs = [(document, parent) for parent in parents]
    snapshot = freeze_generation_child_chunks(
        active_workspace,
        generation_id=active_token,
        document_children=snapshot_pairs,
        document_parents=snapshot_parent_pairs,
    )
    fingerprint = build_generation_fingerprint(kb, snapshot_pairs)
    active = VectorIndexGeneration(
        knowledge_base_id=kb.id,
        backend="qdrant",
        generation=active_token,
        status=VectorIndexGenerationStatus.active,
        workspace_path=str(active_workspace),
        collections={},
        document_manifest_hash=fingerprint.document_manifest_hash,
        child_chunks_manifest_hash=snapshot.child_manifest_hash,
        embedding_config_hash=fingerprint.embedding_config_hash,
        chunking_config_hash=fingerprint.chunking_config_hash,
    )
    session.add(active)
    await session.flush()
    kb.active_vector_generation_id = active.id
    await session.commit()

    async def no_external_ingest(*_args, **_kwargs) -> int:
        return 0

    async def no_external_remove(*_args, **_kwargs) -> list[str]:
        return []

    monkeypatch.setattr(service, "_ingest_document", no_external_ingest)
    monkeypatch.setattr(service, "_remove_document_points", no_external_remove)
    return service, active


def test_model_supports_reparse_and_reindex_operations() -> None:
    assert UpdateOperation("reparse") is UpdateOperation.reparse
    assert UpdateOperation("reindex") is UpdateOperation.reindex


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_value", "requires_document"),
    [("reparse", True), ("reindex", False)],
)
async def test_repository_creates_queries_and_recovers_new_operation_types(
    update_job_session,
    operation_value: str,
    requires_document: bool,
) -> None:
    session, kb, document = update_job_session
    operation = UpdateOperation(operation_value)
    repository = UpdateJobRepository(session)
    job = await repository.create(
        knowledge_base_id=kb.id,
        operation=operation,
        document_id=document.id if requires_document else None,
        created_by="phase15b-test",
    )
    await session.commit()

    queried = await repository.get_by_kb_and_id(kb.id, job.id)
    assert queried is not None
    assert queried.operation is operation
    assert queried.document_id == (document.id if requires_document else None)

    now = datetime.now(tz=UTC)
    claimed = await repository.claim_specific(
        job.id,
        worker_id="phase15b-worker",
        lease_token="phase15b-token",
        fencing_token=1,
        now=now,
        lease_expires_at=now - timedelta(seconds=1),
    )
    assert claimed is not None

    recovered_ids = await repository.mark_expired_for_recovery(now=now)
    assert recovered_ids == [job.id]
    recovered = await repository.get(job.id)
    assert recovered is not None
    await session.refresh(recovered)
    assert recovered.operation is operation
    assert recovered.status is UpdateJobStatus.recovery_required


@pytest.mark.asyncio
async def test_reparse_job_requires_document_id(update_job_session) -> None:
    session, kb, _document = update_job_session
    repository = UpdateJobRepository(session)
    with pytest.raises(IntegrityError):
        await repository.create(
            knowledge_base_id=kb.id,
            operation=UpdateOperation("reparse"),
            document_id=None,
            created_by="phase15b-test",
        )
    await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "replace", "delete"])
async def test_existing_update_operations_remain_persistable(
    update_job_session,
    operation: str,
) -> None:
    session, kb, document = update_job_session
    repository = UpdateJobRepository(session)
    job = await repository.create(
        knowledge_base_id=kb.id,
        operation=UpdateOperation(operation),
        document_id=document.id,
        created_by="phase15b-test",
    )
    await session.commit()

    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.operation.value == operation


def test_migration_preserves_legacy_jobs_and_adds_reparse_constraint(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "phase15b-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    reset_for_testing()
    config = Config("alembic.ini")

    command.upgrade(config, "e2f3a4b5c6d7")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO update_jobs (
                    id, knowledge_base_id, operation, status, retry_count,
                    created_by, created_at, updated_at
                ) VALUES (
                    :id, :knowledge_base_id, :operation, :status, :retry_count,
                    :created_by, :created_at, :updated_at
                )
                """
            ),
            {
                "id": "d" * 32,
                "knowledge_base_id": "a" * 32,
                "operation": "add",
                "status": "pending",
                "retry_count": 0,
                "created_by": "phase15b-test",
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            },
        )

    command.upgrade(config, "head")
    inspector = inspect(engine)
    constraints = inspector.get_check_constraints("update_jobs")
    assert any(
        item["name"] == "ck_update_jobs_reparse_requires_document"
        for item in constraints
    )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT operation FROM update_jobs WHERE id = :id"),
            {"id": "d" * 32},
        ).scalar_one() == "add"

    command.downgrade(config, "e2f3a4b5c6d7")
    reset_for_testing()


async def _task_context(
    session,
    kb: KnowledgeBase,
    document: Document,
    task_type: TaskType,
    payload: dict | None = None,
) -> TaskExecutionContext:
    task = LifecycleTask(
        knowledge_base_id=kb.id,
        document_id=document.id,
        task_type=task_type,
        payload=payload or {"requested_by": "phase15b-test"},
    )
    session.add(task)
    await session.commit()
    return TaskExecutionContext(
        task=task,
        kb_repo=KnowledgeBaseRepository(session),
        doc_repo=DocumentRepository(session),
        task_repo=TaskRepository(session),
        settings=Settings.from_mapping({"DASHSCOPE_API_KEY": "phase15b-test-key"}),
    )


async def _candidate_build_result(job_id: str) -> dict[str, str]:
    return {
        "status": "candidate_built",
        "job_id": job_id,
        "candidate_generation_id": "candidate-for-test",
    }


@pytest.mark.asyncio
async def test_document_service_queues_reindex_as_reindex_task(update_job_session) -> None:
    session, kb, document = update_job_session

    response = await DocumentService(session).request_reindex(kb.id, document.id)

    task = await TaskRepository(session).get(response["task_id"])
    assert task is not None
    assert task.task_type is TaskType.reindex


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_type", "operation_value"),
    [(TaskType.reparse, "reparse"), (TaskType.reindex, "reindex")],
)
async def test_handler_creates_pending_update_job_for_document_operation(
    update_job_session,
    monkeypatch,
    task_type: TaskType,
    operation_value: str,
) -> None:
    from industrial_rag.services.handler_impls import handle_reindex, handle_reparse

    async def candidate_built(self, _kb_id: str, job_id: str, *, actor: str | None = None):
        return {
            "status": "candidate_built",
            "job_id": job_id,
            "candidate_generation_id": "candidate-for-test",
        }

    monkeypatch.setattr(IncrementalUpdateService, "execute_job", candidate_built)
    session, kb, document = update_job_session
    context = await _task_context(session, kb, document, task_type)
    handler = handle_reparse if task_type is TaskType.reparse else handle_reindex

    result = await handler(context)

    assert result.success is True
    assert result.result is not None
    assert result.result["action"] == "candidate_built"
    job_id = result.result["update_job_id"]
    job = await UpdateJobRepository(session).get(job_id)
    assert job is not None
    assert job.operation is UpdateOperation(operation_value)
    assert job.status is UpdateJobStatus.pending
    assert job.document_id == document.id
    assert context.task.payload["update_job_id"] == job.id


@pytest.mark.asyncio
async def test_document_handlers_do_not_use_legacy_publish_path(
    update_job_session,
    monkeypatch,
) -> None:
    from industrial_rag.services.handler_impls import handle_reindex, handle_reparse
    from industrial_rag.services.index_service import IndexService
    from industrial_rag.services.parse_service import ParseService

    async def legacy_path_called(*_args, **_kwargs):
        raise AssertionError("document lifecycle handler entered legacy publish path")

    monkeypatch.setattr(ParseService, "parse_document", legacy_path_called)
    monkeypatch.setattr(IndexService, "index_knowledge_base", legacy_path_called)
    monkeypatch.setattr(
        IncrementalUpdateService,
        "execute_job",
        lambda _self, _kb_id, job_id, **_kwargs: _candidate_build_result(job_id),
    )
    session, kb, document = update_job_session

    reparse = await handle_reparse(
        await _task_context(session, kb, document, TaskType.reparse)
    )
    reindex = await handle_reindex(
        await _task_context(session, kb, document, TaskType.reindex)
    )

    assert reparse.success is True
    assert reindex.success is True


@pytest.mark.asyncio
async def test_document_handlers_no_bypass_activate_generation(
    update_job_session,
    monkeypatch,
) -> None:
    from industrial_rag.repositories.vector_index_generation_repository import (
        VectorIndexGenerationRepository,
    )
    from industrial_rag.services.handler_impls import handle_reindex, handle_reparse

    async def generation_activation_called(*_args, **_kwargs):
        raise AssertionError("document lifecycle handler activated a generation")

    monkeypatch.setattr(
        VectorIndexGenerationRepository,
        "activate",
        generation_activation_called,
    )
    monkeypatch.setattr(
        IncrementalUpdateService,
        "execute_job",
        lambda _self, _kb_id, job_id, **_kwargs: _candidate_build_result(job_id),
    )
    session, kb, document = update_job_session

    reparse = await handle_reparse(
        await _task_context(session, kb, document, TaskType.reparse)
    )
    reindex = await handle_reindex(
        await _task_context(session, kb, document, TaskType.reindex)
    )

    assert reparse.success is True
    assert reindex.success is True


@pytest.mark.asyncio
async def test_index_service_rejects_implicit_document_publish_path(
    update_job_session,
) -> None:
    """IndexService is reserved for explicit backend migration, not documents."""
    from industrial_rag.services.index_service import IndexService

    session, kb, _document = update_job_session

    with pytest.raises(RuntimeError, match="explicit backend migration"):
        await IndexService(session).index_knowledge_base(kb.id, "legacy-task")


@pytest.mark.asyncio
async def test_parse_rebuild_lifecycle_task_converges_to_add_update_job(
    update_job_session,
    monkeypatch,
) -> None:
    """A legacy parse follow-up cannot rebuild and publish directly."""
    from industrial_rag.services.handler_impls import handle_rebuild
    from industrial_rag.services.index_service import IndexService

    async def direct_index_called(*_args, **_kwargs):
        raise AssertionError("document rebuild entered IndexService")

    monkeypatch.setattr(IndexService, "index_knowledge_base", direct_index_called)
    monkeypatch.setattr(
        IncrementalUpdateService,
        "execute_job",
        lambda _self, _kb_id, job_id, **_kwargs: _candidate_build_result(job_id),
    )
    session, kb, document = update_job_session
    result = await handle_rebuild(
        await _task_context(
            session,
            kb,
            document,
            TaskType.rebuild,
            payload={"trigger": "parse_completed", "document_id": document.id},
        )
    )

    assert result.success is True
    assert result.result is not None
    job = await UpdateJobRepository(session).get(result.result["update_job_id"])
    assert job is not None
    assert job.operation is UpdateOperation.add


@pytest.mark.asyncio
async def test_document_delete_rebuild_lifecycle_task_creates_delete_update_job(
    update_job_session,
    monkeypatch,
) -> None:
    """Legacy delete rebuild recovery creates a job without publishing."""
    from industrial_rag.services.handler_impls import handle_rebuild
    from industrial_rag.services.index_service import IndexService

    async def direct_index_called(*_args, **_kwargs):
        raise AssertionError("document delete rebuild entered IndexService")

    monkeypatch.setattr(IndexService, "index_knowledge_base", direct_index_called)
    session, kb, document = update_job_session
    task = LifecycleTask(
        knowledge_base_id=kb.id,
        document_id=None,
        task_type=TaskType.rebuild,
        payload={"trigger": "document_delete", "deleted_document_id": document.id},
    )
    session.add(task)
    await session.commit()
    context = TaskExecutionContext(
        task=task,
        kb_repo=KnowledgeBaseRepository(session),
        doc_repo=DocumentRepository(session),
        task_repo=TaskRepository(session),
        settings=Settings.from_mapping({"DASHSCOPE_API_KEY": "phase15b-test-key"}),
    )

    result = await handle_rebuild(context)

    assert result.success is True
    assert result.result is not None
    assert result.result["action"] == "update_job_created"
    job = await UpdateJobRepository(session).get(result.result["update_job_id"])
    assert job is not None
    assert job.operation is UpdateOperation.delete
    assert job.document_id == document.id


@pytest.mark.asyncio
async def test_legacy_manual_reindex_rebuild_task_converges_to_update_job(
    update_job_session,
    monkeypatch,
) -> None:
    from industrial_rag.services.handler_impls import handle_rebuild
    from industrial_rag.services.index_service import IndexService

    async def legacy_publish_path_called(*_args, **_kwargs):
        raise AssertionError("legacy manual reindex entered IndexService")

    monkeypatch.setattr(IndexService, "index_knowledge_base", legacy_publish_path_called)
    monkeypatch.setattr(
        IncrementalUpdateService,
        "execute_job",
        lambda _self, _kb_id, job_id, **_kwargs: _candidate_build_result(job_id),
    )
    session, kb, document = update_job_session
    context = await _task_context(
        session,
        kb,
        document,
        TaskType.rebuild,
        payload={"reason": "manual reindex request"},
    )

    result = await handle_rebuild(context)

    assert result.success is True
    assert result.result is not None
    job = await UpdateJobRepository(session).get(result.result["update_job_id"])
    assert job is not None
    assert job.operation is UpdateOperation.reindex


@pytest.mark.asyncio
async def test_reparse_execution_builds_isolated_candidate_and_preserves_active(
    update_job_session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A reparse rebuilds a candidate while retaining the serving generation."""
    session, kb, document = update_job_session
    service, active = await _service_with_active_snapshot(
        session, kb, document, tmp_path, monkeypatch
    )
    job = await UpdateJobRepository(session).create(
        knowledge_base_id=kb.id,
        base_generation_id=active.id,
        operation=UpdateOperation.reparse,
        document_id=document.id,
        created_by="phase15b-test",
    )
    await session.commit()

    result = await service.execute_job(kb.id, job.id)

    candidate = await service._generation_repo.get(result["candidate_generation_id"])
    current_active = await service._generation_repo.get_active(kb.id)
    assert result["status"] == "candidate_built"
    assert candidate is not None
    assert candidate.id != active.id
    assert candidate.status is VectorIndexGenerationStatus.building
    assert current_active is not None
    assert current_active.id == active.id
    assert kb.active_vector_generation_id == active.id


@pytest.mark.asyncio
async def test_reindex_candidate_preserves_active_until_promote(
    update_job_session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reindex leaves Active unchanged; only Promote may switch it later."""
    session, kb, document = update_job_session
    service, active = await _service_with_active_snapshot(
        session, kb, document, tmp_path, monkeypatch
    )
    job = await UpdateJobRepository(session).create(
        knowledge_base_id=kb.id,
        base_generation_id=active.id,
        operation=UpdateOperation.reindex,
        document_id=None,
        created_by="phase15b-test",
    )
    await session.commit()

    async def parser_called(*_args, **_kwargs):
        raise AssertionError("reindex must not invoke the parser")

    monkeypatch.setattr(service, "_parse_document_pymupdf", parser_called)

    result = await service.execute_job(kb.id, job.id)

    candidate = await service._generation_repo.get(result["candidate_generation_id"])
    current_active = await service._generation_repo.get_active(kb.id)
    assert result["status"] == "candidate_built"
    assert candidate is not None
    assert candidate.id != active.id
    assert candidate.child_chunks_manifest_hash == active.child_chunks_manifest_hash
    assert current_active is not None
    assert current_active.id == active.id
    assert kb.active_vector_generation_id == active.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_type",
    [TaskType.reparse, TaskType.reindex],
)
async def test_lifecycle_handlers_execute_their_update_job(
    update_job_session,
    monkeypatch,
    task_type: TaskType,
) -> None:
    """Lifecycle adapters hand both operations to the UpdateJob pipeline."""
    from industrial_rag.services.handler_impls import handle_reindex, handle_reparse

    calls: list[tuple[str, str]] = []

    async def execute_job(self, kb_id: str, job_id: str, *, actor: str | None = None):
        calls.append((kb_id, job_id))
        return {
            "status": "candidate_built",
            "job_id": job_id,
            "candidate_generation_id": "candidate-for-test",
        }

    monkeypatch.setattr(IncrementalUpdateService, "execute_job", execute_job, raising=False)
    session, kb, document = update_job_session
    context = await _task_context(session, kb, document, task_type)
    handler = handle_reparse if task_type is TaskType.reparse else handle_reindex

    result = await handler(context)

    assert result.success is True
    assert result.result is not None
    assert result.result["action"] == "candidate_built"
    assert calls == [(kb.id, result.result["update_job_id"])]
