"""Phase15-B Step1 contract tests for UpdateJob operation persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from industrial_rag.db.models import (
    Base,
    Document,
    KnowledgeBase,
    LifecycleTask,
    TaskType,
    UpdateJobStatus,
    UpdateOperation,
)
from industrial_rag.db.session import reset_for_testing
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from industrial_rag.services.document_service import DocumentService
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
    )


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
    task_type: TaskType,
    operation_value: str,
) -> None:
    from industrial_rag.services.handler_impls import handle_reindex, handle_reparse

    session, kb, document = update_job_session
    context = await _task_context(session, kb, document, task_type)
    handler = handle_reparse if task_type is TaskType.reparse else handle_reindex

    result = await handler(context)

    assert result.success is True
    assert result.result is not None
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
async def test_document_handlers_do_not_activate_generation(
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
async def test_legacy_manual_reindex_rebuild_task_converges_to_update_job(
    update_job_session,
    monkeypatch,
) -> None:
    from industrial_rag.services.handler_impls import handle_rebuild
    from industrial_rag.services.index_service import IndexService

    async def legacy_publish_path_called(*_args, **_kwargs):
        raise AssertionError("legacy manual reindex entered IndexService")

    monkeypatch.setattr(IndexService, "index_knowledge_base", legacy_publish_path_called)
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
