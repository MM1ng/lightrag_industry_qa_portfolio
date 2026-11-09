"""Integration-level tests for the service lifecycle modules (offline).

Covers KnowledgeBaseService, DocumentService, IndexService, RebuildService and
KnowledgeBaseCleanupService, reusing the FakeQdrant / FakeLightRAGService /
Phase9Ctx infrastructure from test_phase9 so no real LLM, Qdrant or production
storage is touched.

Focus: generation/KB lifecycle state machine — build failure isolation,
promote/delete idempotency, rollback on atomic-swap failure, and
delete-by-version style cleanup steps.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

_TEST_DIR = Path(__file__).resolve().parent
if str(_TEST_DIR) not in sys.path:
    sys.path.insert(0, str(_TEST_DIR))

from industrial_rag.db.models import KBStatus  # noqa: E402
from industrial_rag.db.session import init_db, reset_for_testing  # noqa: E402
from industrial_rag.errors import AppError  # noqa: E402
from industrial_rag.repositories.document_repository import DocumentRepository  # noqa: E402
from industrial_rag.repositories.knowledge_base_repository import (  # noqa: E402
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository  # noqa: E402
from industrial_rag.services.cleanup_service import KnowledgeBaseCleanupService  # noqa: E402
from industrial_rag.services.document_service import (  # noqa: E402
    MAX_UPLOAD_SIZE,
    DocumentService,
)
from industrial_rag.services.index_service import IndexService  # noqa: E402
from industrial_rag.services.knowledge_base_service import KnowledgeBaseService  # noqa: E402
from industrial_rag.services.rebuild_service import RebuildService  # noqa: E402

from test_phase9 import Phase9Ctx, _write_pdf  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def offline(async_fn):
    """Run an async test body on a private loop (repo convention, see test_phase9._run)."""

    @functools.wraps(async_fn)
    def wrapper(*args, **kwargs):
        return _run(async_fn(*args, **kwargs))

    return wrapper


@pytest.fixture(scope="module", autouse=True)
def _isolated_env(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("svc_lifecycle_db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    os.environ["QDRANT_URL"] = "http://fake:6333"
    os.environ["QDRANT_COLLECTION_PREFIX"] = "ira_test"
    os.environ["EMBEDDING_MODEL"] = "text-embedding-v4"
    os.environ["EMBEDDING_DIM"] = "1024"
    os.environ["LLM_MODEL"] = "qwen-plus-2025-07-28"
    os.environ["DASHSCOPE_API_KEY"] = "test-key"
    reset_for_testing()
    yield
    for key in ("DATABASE_URL", "QDRANT_URL", "QDRANT_COLLECTION_PREFIX"):
        os.environ.pop(key, None)
    reset_for_testing()


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from g1_test_support import stub_lifecycle_policy

    stub_lifecycle_policy(monkeypatch)
    reset_for_testing()

    async def _init():
        await init_db(drop_all=True)

    _run(_init())
    yield
    _run(_init())


@pytest.fixture()
def ctx(tmp_path: Path) -> Phase9Ctx:
    return Phase9Ctx(tmp_path)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.closed: list[str] = []

    async def close_runtime(self, kb_id: str) -> None:
        self.closed.append(kb_id)


async def _make_ready_kb(ctx: Phase9Ctx, name: str = "SvcLifecycle") -> str:
    return await ctx.create_kb(name=name)


async def _upload_doc(ctx: Phase9Ctx, kb_id: str, text: str = "离心泵轴承温度上限 80 摄氏度。") -> str:
    """Create an active Document row via the light upload path (no generation build)."""
    content = _pdf_bytes(Path(os.environ["KB_DATA_ROOT"]), text)
    async with ctx.factory() as session:
        doc = await DocumentService(session).upload(
            kb_id, original_file_name=f"doc-{uuid.uuid4().hex[:6]}.pdf", content=content
        )
        await session.commit()
        return doc.id


async def _new_task(ctx: Phase9Ctx, kb_id: str, task_type: str) -> str:
    async with ctx.factory() as session:
        task = await TaskRepository(session).create(
            knowledge_base_id=kb_id, task_type=task_type, payload={}
        )
        await session.commit()
        return task.id


# ---------------------------------------------------------------------------
# KnowledgeBaseService
# ---------------------------------------------------------------------------


@offline
async def test_kb_create_get_update_list_roundtrip(ctx: Phase9Ctx):
    factory = ctx.factory
    async with factory() as session:
        svc = KnowledgeBaseService(session)
        kb = await svc.create(name="循环测试库", description="initial")
        await session.commit()

        assert kb.workspace_path and Path(kb.workspace_path).is_dir()
        fetched = await svc.get(kb.id)
        assert fetched.id == kb.id

        updated = await svc.update(kb.id, name="  改名后的库  ", description=" 更新 ")
        assert updated.name == "改名后的库"
        assert updated.description == "更新"

        kbs, total = await svc.list_all()
        assert total >= 1 and any(x.id == kb.id for x in kbs)

        with pytest.raises(AppError) as empty_err:
            await svc.create(name="   ")
        assert empty_err.value.code == "invalid_state_transition"

        with pytest.raises(AppError):
            await svc.create(name="x" * 201)

        with pytest.raises(AppError) as missing:
            await svc.get("kb-not-exists")
        assert missing.value.code == "knowledge_base_not_found"


@offline
async def test_kb_update_rejected_once_deleting(ctx: Phase9Ctx):
    kb_id = await _make_ready_kb(ctx, "删除中的库")
    async with ctx.factory() as session:
        svc = KnowledgeBaseService(session)
        await svc.request_delete(kb_id)
        await session.commit()

        with pytest.raises(AppError) as err:
            await svc.update(kb_id, name="new-name")
        assert err.value.code == "invalid_state_transition"
        assert err.value.status_code == 409


@offline
async def test_kb_backend_change_idempotent_and_guards(ctx: Phase9Ctx):
    kb_id = await _make_ready_kb(ctx, "后端迁移库")
    async with ctx.factory() as session:
        svc = KnowledgeBaseService(session)

        with pytest.raises(AppError):
            await svc.request_vector_backend_change(kb_id, target_backend="redis")

        first = await svc.request_vector_backend_change(kb_id, target_backend="qdrant")
        assert first["idempotent"] is False
        assert first["target_backend"] == "qdrant"
        await session.commit()

        second = await svc.request_vector_backend_change(kb_id, target_backend="qdrant")
        assert second["idempotent"] is True
        assert second["task_id"] == first["task_id"]

        with pytest.raises(AppError) as busy:
            await svc.request_delete(kb_id)
        assert busy.value.code == "knowledge_base_busy"

        with pytest.raises(AppError) as same:
            await svc.request_vector_backend_change(kb_id, target_backend="nano")
        assert same.value.status_code == 409


@offline
async def test_kb_delete_guards_protected_and_double_delete(ctx: Phase9Ctx):
    kb_id = await _make_ready_kb(ctx, "受保护库")
    async with ctx.factory() as session:
        repo = KnowledgeBaseRepository(session)
        svc = KnowledgeBaseService(session)

        await repo.update(kb_id, protect_from_delete=True)
        await session.commit()
        with pytest.raises(AppError) as protected:
            await svc.request_delete(kb_id)
        assert protected.value.status_code == 403

        await repo.update(kb_id, protect_from_delete=False)
        await session.commit()
        result = await svc.request_delete(kb_id)
        assert result["status"] == "pending"

        with pytest.raises(AppError) as deleting:
            await svc.request_delete(kb_id)
        assert deleting.value.status_code == 423


# ---------------------------------------------------------------------------
# DocumentService
# ---------------------------------------------------------------------------


def _pdf_bytes(tmp_path: Path, text: str = "泵手册内容") -> bytes:
    pdf_path = tmp_path / f"src-{uuid.uuid4().hex[:6]}.pdf"
    _write_pdf(pdf_path, text)
    return pdf_path.read_bytes()


@offline
async def test_document_upload_rejects_invalid_inputs(ctx: Phase9Ctx, tmp_path: Path):
    kb_id = await _make_ready_kb(ctx, "上传校验库")
    good = _pdf_bytes(tmp_path)

    async with ctx.factory() as session:
        svc = DocumentService(session)

        with pytest.raises(AppError) as missing_kb:
            await svc.upload("kb-not-exists", original_file_name="a.pdf", content=good)
        assert missing_kb.value.code == "knowledge_base_not_found"

        bad_ext = _pdf_bytes(tmp_path)
        with pytest.raises(AppError) as ext_err:
            await svc.upload(kb_id, original_file_name="a.txt", content=bad_ext)
        assert ext_err.value.status_code == 415

        with pytest.raises(AppError) as empty_err:
            await svc.upload(kb_id, original_file_name="a.pdf", content=b"")
        assert empty_err.value.status_code == 422

        with pytest.raises(AppError) as size_err:
            await svc.upload(
                kb_id, original_file_name="a.pdf", content=b"x" * (MAX_UPLOAD_SIZE + 1)
            )
        assert size_err.value.status_code == 413

        with pytest.raises(AppError) as magic_err:
            await svc.upload(kb_id, original_file_name="a.pdf", content=b"not-a-pdf")
        assert magic_err.value.code == "invalid_pdf"


@offline
async def test_document_upload_happy_path_and_duplicate_hash(ctx: Phase9Ctx, tmp_path: Path):
    kb_id = await _make_ready_kb(ctx, "上传主链路库")
    content = _pdf_bytes(tmp_path, "轴承润滑周期说明")

    async with ctx.factory() as session:
        svc = DocumentService(session)
        doc = await svc.upload(kb_id, original_file_name="manual.pdf", content=content)
        await session.commit()
        assert doc.file_size == len(content)
        assert Path(doc.file_path).is_file()

        with pytest.raises(AppError) as dup:
            await svc.upload(kb_id, original_file_name="manual-again.pdf", content=content)
        assert dup.value.code == "duplicate_document"
        assert dup.value.status_code == 409

        fetched = await svc.get(kb_id, doc.id)
        assert fetched.id == doc.id

        with pytest.raises(AppError) as no_doc:
            await svc.get(kb_id, "doc-not-exists")
        assert no_doc.value.code == "document_not_found"


@offline
async def test_document_task_requests_and_active_task_conflict(ctx: Phase9Ctx, tmp_path: Path):
    kb_id = await _make_ready_kb(ctx, "文档任务库")
    content = _pdf_bytes(tmp_path)

    async with ctx.factory() as session:
        svc = DocumentService(session)
        doc = await svc.upload(kb_id, original_file_name="task.pdf", content=content)
        await session.commit()

        # The upload itself creates a pending parse task, so any new task
        # request for the same document must be rejected outright.
        with pytest.raises(AppError) as conflict_parse:
            await svc.request_reparse(kb_id, doc.id)
        assert conflict_parse.value.code == "task_already_running"

        tasks = await TaskRepository(session).list_by_doc(doc.id, limit=5)
        parse_task = next(t for t in tasks if t.task_type.value == "parse")
        await TaskRepository(session).mark_succeeded(parse_task.id)
        await session.commit()

        reparse = await svc.request_reparse(kb_id, doc.id)
        assert reparse["status"] == "pending"

        with pytest.raises(AppError) as conflict:
            await svc.request_reindex(kb_id, doc.id)
        assert conflict.value.code == "task_already_running"
        assert conflict.value.status_code == 409


@offline
async def test_document_delete_requests_rebuild_and_listing(ctx: Phase9Ctx, tmp_path: Path):
    kb_id = await _make_ready_kb(ctx, "文档删除库")
    content = _pdf_bytes(tmp_path)

    async with ctx.factory() as session:
        svc = DocumentService(session)
        doc = await svc.upload(kb_id, original_file_name="del.pdf", content=content)
        await session.commit()

        tasks = await TaskRepository(session).list_by_doc(doc.id, limit=5)
        parse_task = next(t for t in tasks if t.task_type.value == "parse")
        await TaskRepository(session).mark_succeeded(parse_task.id)
        await session.commit()

        result = await svc.request_delete(kb_id, doc.id)
        assert result["status"] == "pending"
        await session.commit()

        tasks = await TaskRepository(session).list_by_kb(kb_id, limit=10)
        task_types = {t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type) for t in tasks}
        assert "delete_document" in task_types
        assert "rebuild" in task_types

        docs, total = await svc.list_by_kb(kb_id, include_deleted=True)
        assert total >= 1
        statuses = {d.id: d.status.value for d in docs}
        assert statuses[doc.id] == "deleting"

        active_docs, _ = await svc.list_by_kb(kb_id, include_deleted=False)
        active_statuses = {d.id: d.status.value for d in active_docs}
        assert active_statuses.get(doc.id) != "deleted", "soft-deleted doc stays visible until cleanup"
        assert all(d.status.value != "deleted" for d in active_docs)


# ---------------------------------------------------------------------------
# IndexService (error paths before any LLM/network work)
# ---------------------------------------------------------------------------


@offline
async def test_index_service_requires_explicit_backend(ctx: Phase9Ctx):
    kb_id = await _make_ready_kb(ctx, "索引缺参库")
    async with ctx.factory() as session:
        svc = IndexService(session)
        with pytest.raises(RuntimeError, match="explicit backend migration target"):
            await svc.index_knowledge_base(kb_id, "task-x")


@offline
async def test_index_service_rejects_missing_kb_and_empty_kb(ctx: Phase9Ctx):
    async with ctx.factory() as session:
        svc = IndexService(session)
        with pytest.raises(RuntimeError, match="not found"):
            await svc.index_knowledge_base(
                "kb-not-exists", "task-y", target_backend="qdrant"
            )

    kb_id = await _make_ready_kb(ctx, "空知识库")
    async with ctx.factory() as session:
        svc = IndexService(session)
        with pytest.raises(RuntimeError, match="no active documents"):
            await svc.index_knowledge_base(kb_id, "task-z", target_backend="nano")


# ---------------------------------------------------------------------------
# RebuildService — atomic workspace swap state machine
# ---------------------------------------------------------------------------


@offline
async def test_rebuild_rejects_missing_kb_and_empty_kb(ctx: Phase9Ctx):
    async with ctx.factory() as session:
        svc = RebuildService(session, runtime_manager=FakeRuntimeManager())
        with pytest.raises(RuntimeError, match="not found"):
            await svc.rebuild("kb-not-exists", "task-r0")

    kb_id = await _make_ready_kb(ctx, "重建空库")
    async with ctx.factory() as session:
        svc = RebuildService(session, runtime_manager=FakeRuntimeManager())
        with pytest.raises(RuntimeError, match="No active documents"):
            await svc.rebuild(kb_id, "task-r1")


@offline
async def test_rebuild_health_check_requires_index_marker(ctx: Phase9Ctx, tmp_path: Path):
    kb_id = await _make_ready_kb(ctx, "重建健康检查库")
    await _upload_doc(ctx, kb_id)
    task_id = await _new_task(ctx, kb_id, "rebuild")

    async with ctx.factory() as session:
        svc = RebuildService(session, runtime_manager=FakeRuntimeManager())
        with pytest.raises(RuntimeError, match="index marker missing"):
            await svc.rebuild(kb_id, task_id)

    async with ctx.factory() as session:
        kb = await KnowledgeBaseRepository(session).get(kb_id)
        workspace = Path(kb.workspace_path)
        assert workspace.is_dir(), "active workspace must survive failed rebuild"
        assert not (workspace.parent / f"{workspace.name}.backup-{task_id}").exists()


@offline
async def test_rebuild_success_swaps_workspace_and_cleans_backup(ctx: Phase9Ctx):
    kb_id = await _make_ready_kb(ctx, "重建主链路库")
    doc_id = await _upload_doc(ctx, kb_id)
    task_id = await _new_task(ctx, kb_id, "rebuild")

    async with ctx.factory() as session:
        kb_repo = KnowledgeBaseRepository(session)
        kb = await kb_repo.get(kb_id)
        workspace = Path(kb.workspace_path)
        tmp_workspace = workspace.parent / f"{workspace.name}.rebuild-{task_id}"
        backup_workspace = workspace.parent / f"{workspace.name}.backup-{task_id}"

        # Simulate the handler-provided index build result in the tmp workspace.
        tmp_workspace.mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "industrial_rag_index.json").write_text("{}", encoding="utf-8")
        (tmp_workspace / "kv_store_doc_status.json").write_text(
            json.dumps({doc_id: {"status": "processed", "chunks_count": 3}}), encoding="utf-8"
        )
        # Something in the old workspace that must disappear after the swap.
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "old-artifact.txt").write_text("stale", encoding="utf-8")
        await session.commit()

        runtime = FakeRuntimeManager()
        svc = RebuildService(session, runtime_manager=runtime)
        await svc.rebuild(kb_id, task_id)

        assert kb_id in runtime.closed, "old runtime must be closed before swap"
        assert (workspace / "industrial_rag_index.json").is_file()
        assert not (workspace / "old-artifact.txt").exists()
        assert not backup_workspace.exists(), "backup must be cleaned after swap"

        refreshed = await kb_repo.get(kb_id)
        assert refreshed.active_document_count == 1


@offline
async def test_rebuild_atomic_swap_failure_rolls_back(ctx: Phase9Ctx, monkeypatch):
    kb_id = await _make_ready_kb(ctx, "重建回滚库")
    await _upload_doc(ctx, kb_id)
    task_id = await _new_task(ctx, kb_id, "rebuild")

    async with ctx.factory() as session:
        kb_repo = KnowledgeBaseRepository(session)
        kb = await kb_repo.get(kb_id)
        workspace = Path(kb.workspace_path)
        tmp_workspace = workspace.parent / f"{workspace.name}.rebuild-{task_id}"
        backup_workspace = workspace.parent / f"{workspace.name}.backup-{task_id}"

        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "current.txt").write_text("active-content", encoding="utf-8")
        tmp_workspace.mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "industrial_rag_index.json").write_text("{}", encoding="utf-8")
        await session.commit()

    original_rename = Path.rename
    real_tmp = tmp_workspace

    def failing_rename(self, target):
        if str(self) == str(real_tmp):
            raise OSError("simulated swap failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)

    async with ctx.factory() as session:
        svc = RebuildService(session, runtime_manager=FakeRuntimeManager())
        with pytest.raises(RuntimeError, match="Atomic swap failed"):
            await svc.rebuild(kb_id, task_id)

    monkeypatch.undo()
    assert workspace.is_dir(), "old workspace must be restored from backup"
    assert (workspace / "current.txt").read_text(encoding="utf-8") == "active-content"
    assert not backup_workspace.exists(), "backup must be moved back during rollback"
    assert tmp_workspace.is_dir(), "failed tmp workspace is kept for inspection"


# ---------------------------------------------------------------------------
# KnowledgeBaseCleanupService — step-wise deletion state machine
# ---------------------------------------------------------------------------


@offline
async def test_cleanup_execute_steps_idempotent_rerun(ctx: Phase9Ctx, tmp_path: Path):
    kb_id = await _make_ready_kb(ctx, "清理主链路库")
    doc_id = await _upload_doc(ctx, kb_id)
    task_id = await _new_task(ctx, kb_id, "delete_knowledge_base")

    async with ctx.factory() as session:
        repo = KnowledgeBaseRepository(session)
        await repo.soft_delete(kb_id)
        await session.commit()
    # storage layout helpers give the authoritative base dir
    from industrial_rag.storage_layout import kb_base_dir

    base_dir = kb_base_dir(kb_id)
    (base_dir / "parsed").mkdir(parents=True, exist_ok=True)
    (base_dir / "parsed" / "keep.txt").write_text("x", encoding="utf-8")
    (base_dir / "tmp").mkdir(parents=True, exist_ok=True)
    uploads_marker = base_dir / "uploads" / "keep.txt"
    uploads_marker.parent.mkdir(parents=True, exist_ok=True)
    uploads_marker.write_text("y", encoding="utf-8")

    runtime = FakeRuntimeManager()
    async with ctx.factory() as session:
        svc = KnowledgeBaseCleanupService(session, runtime_manager=runtime)
        await svc.execute(kb_id, task_id)
        await session.commit()  # cleanup service flushes only; persist task/kb/doc writes

    assert kb_id in runtime.closed
    assert not (base_dir / "parsed").exists()
    assert not (base_dir / "tmp").exists()
    assert uploads_marker.exists(), "source files are kept unless delete_source_files=True"

    async with ctx.factory() as session:
        doc = await DocumentRepository(session).get(doc_id)
        assert doc.status.value == "deleted"
        kb = await KnowledgeBaseRepository(session).get(kb_id)
        assert kb.status == KBStatus.deleted
        task = await TaskRepository(session).get(task_id)
        assert task.status.value == "succeeded"
        step_names = [s["step_name"] for s in task.result["steps"]]
        assert step_names[0] == "close_runtime" and step_names[-1] == "mark_kb_deleted"
        assert all(s["status"] == "succeeded" for s in task.result["steps"])

    # Idempotent rerun: everything already deleted, no error, task succeeds again.
    task_id_2 = await _new_task(ctx, kb_id, "delete_knowledge_base")
    async with ctx.factory() as session:
        svc = KnowledgeBaseCleanupService(session, runtime_manager=runtime)
        await svc.execute(kb_id, task_id_2)
        await session.commit()
        task2 = await TaskRepository(session).get(task_id_2)
        assert task2.status.value == "succeeded"


@offline
async def test_cleanup_step_failure_records_error_and_raises(ctx: Phase9Ctx):
    from industrial_rag.storage_layout import kb_base_dir

    kb_id = await _make_ready_kb(ctx, "清理失败库")
    await _upload_doc(ctx, kb_id)
    task_id = await _new_task(ctx, kb_id, "delete_knowledge_base")

    base_dir = kb_base_dir(kb_id)
    (base_dir / "parsed").mkdir(parents=True, exist_ok=True)
    (base_dir / "parsed" / "boom.txt").write_text("x", encoding="utf-8")

    async with ctx.factory() as session:
        repo = KnowledgeBaseRepository(session)
        await repo.soft_delete(kb_id)
        await session.commit()

    async with ctx.factory() as session:
        svc = KnowledgeBaseCleanupService(session, runtime_manager=FakeRuntimeManager())

        def exploding_delete(path, kb):
            if Path(path).name == "parsed":
                raise OSError("simulated delete failure")
            return KnowledgeBaseCleanupService._safe_delete_dir(path, kb)

        monkeypatch_local = svc
        monkeypatch_local._safe_delete_dir = exploding_delete
        with pytest.raises(OSError, match="simulated delete failure"):
            await svc.execute(kb_id, task_id)
        await session.commit()  # persist the step-failure bookkeeping before the session closes

    async with ctx.factory() as session:
        task = await TaskRepository(session).get(task_id)
        assert task.error_code == "cleanup_step_failed"
        assert "parsed" in (task.error_message or "")
        kb = await KnowledgeBaseRepository(session).get(kb_id)
        assert kb.status == KBStatus.deleting, "failed cleanup keeps KB retryable"
        assert "parsed" in (kb.last_error or "")
