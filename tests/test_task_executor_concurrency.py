"""Resource-boundary tests for :class:`LifecycleTaskExecutor`.

These tests pin down three invariants that used to be broken:

1. A runner waiting for the concurrency semaphore must not hold a pooled DB
   connection, otherwise a short burst of pending tasks drains the pool.
2. Every spawned runner handle is tracked, cancelled and drained on ``stop()``.
3. A handler failure always lands on ``retrying``/``failed`` - never ``running``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from industrial_rag.config import Settings
from industrial_rag.db.models import Base, KnowledgeBase, LifecycleTask, TaskStatus, TaskType
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.services.lifecycle_task_executor import LifecycleTaskExecutor
from industrial_rag.services.task_context import TaskExecutionResult
from industrial_rag.services.task_handlers import get_builtin_registry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

# Production pool shape: pool_size=5 + max_overflow=10 => 15 connections.
POOL_SIZE = 5
MAX_OVERFLOW = 10
MAX_CONCURRENCY = 2


def _settings() -> Settings:
    return Settings(api_key="offline-test-key")


def _kb_id(index: int) -> str:
    """Deterministic 32 character knowledge-base id."""
    return f"kb{index:030d}"


async def _make_engine(
    tmp_path: Path,
) -> tuple[object, async_sessionmaker[AsyncSession]]:
    """Build a file-backed engine with a real queue pool.

    A file database (plus an explicit ``AsyncAdaptedQueuePool``) is required:
    the in-memory SQLite pool cannot report more than one checked-out
    connection, which would hide the regression under test.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'executor.db'}",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        connect_args={"timeout": 30.0},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _seed_tasks(
    factory: async_sessionmaker[AsyncSession], count: int
) -> list[str]:
    """Create ``count`` pending tasks, each on its own knowledge base.

    One KB per task keeps the per-KB locks out of the way, so the only
    admission control left is the semaphore - exactly what is under test.
    """
    async with factory() as session:
        task_ids: list[str] = []
        for index in range(count):
            kb_id = _kb_id(index)
            session.add(
                KnowledgeBase(
                    id=kb_id,
                    name=f"concurrency-{index}",
                    workspace_path=f"C:/tmp/nano/{index}",
                    upload_path=f"C:/tmp/uploads/{index}",
                    parsed_path=f"C:/tmp/parsed/{index}",
                )
            )
            await session.flush()
            task = LifecycleTask(
                knowledge_base_id=kb_id,
                task_type=TaskType.rebuild,
                status=TaskStatus.pending,
            )
            session.add(task)
            await session.flush()
            task_ids.append(task.id)
        await session.commit()
        return task_ids


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    """Yield to the event loop until ``predicate()`` is true."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for the executor to reach the expected state")


def _install_handler(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Temporarily replace the ``rebuild`` handler in the builtin registry."""
    monkeypatch.setitem(get_builtin_registry()._handlers, TaskType.rebuild, handler)


async def _statuses(factory: async_sessionmaker[AsyncSession], task_ids: list[str]) -> dict:
    async with factory() as session:
        repo = TaskRepository(session)
        result = {}
        for task_id in task_ids:
            task = await repo.get(task_id)
            assert task is not None
            result[task_id] = task.status
        return result


def _semaphore_waiters(executor: LifecycleTaskExecutor) -> int:
    """Count runners parked on the semaphore.

    Test-only probe of a stdlib internal: ``_waiters`` is created lazily by
    ``asyncio.Semaphore``, so a missing/``None`` value means "nobody waiting".
    Knowing that a runner is parked lets us assert on a settled steady state
    instead of racing against the short phase-1 identity read.
    """
    return len(getattr(executor._semaphore, "_waiters", None) or ())


@pytest.mark.asyncio
async def test_semaphore_wait_does_not_hold_db_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting runners must not sit on pooled connections.

    Reproduces the reported drain: ``max_concurrency=2`` but six queued
    runners, each holding a connection while blocked on the semaphore, so
    ``pool.checkedout()`` climbed to six and a thirteen second burst could
    exhaust the fifteen connection pool.
    """
    engine, factory = await _make_engine(tmp_path)
    release = asyncio.Event()
    in_handler = 0

    async def blocking_handler(context) -> TaskExecutionResult:
        nonlocal in_handler
        in_handler += 1
        await release.wait()
        return TaskExecutionResult(success=True)

    _install_handler(monkeypatch, blocking_handler)
    task_ids = await _seed_tasks(factory, count=6)
    executor = LifecycleTaskExecutor(
        factory,
        settings=_settings(),
        poll_interval=60.0,
        max_concurrency=MAX_CONCURRENCY,
    )

    try:
        for _ in range(6):
            await executor._poll_once()
            await asyncio.sleep(0)
        # Steady state: all six runners are alive, exactly max_concurrency of
        # them are inside the handler, and the remaining four are parked on the
        # semaphore with their identity session already closed.
        await _wait_until(
            lambda: len(executor._active_tasks) == 6
            and in_handler == MAX_CONCURRENCY
            and _semaphore_waiters(executor) == 6 - MAX_CONCURRENCY
        )

        checked_out = engine.pool.checkedout()
        assert checked_out <= MAX_CONCURRENCY, (
            f"pool.checkedout()={checked_out} exceeds max_concurrency={MAX_CONCURRENCY}: "
            "runners hold DB connections while waiting for the semaphore"
        )

        release.set()
        await _wait_until(lambda: not executor._active_tasks)
        assert set((await _statuses(factory, task_ids)).values()) == {TaskStatus.succeeded}
    finally:
        release.set()
        await executor.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_task_handles_are_tracked_and_drained_on_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stop()`` cancels and drains every spawned runner handle."""
    engine, factory = await _make_engine(tmp_path)
    release = asyncio.Event()

    async def blocking_handler(context) -> TaskExecutionResult:
        await release.wait()
        return TaskExecutionResult(success=True)

    _install_handler(monkeypatch, blocking_handler)
    await _seed_tasks(factory, count=2)
    executor = LifecycleTaskExecutor(
        factory,
        settings=_settings(),
        poll_interval=60.0,
        max_concurrency=MAX_CONCURRENCY,
        shutdown_timeout=2.0,
    )

    try:
        for _ in range(2):
            await executor._poll_once()
            await asyncio.sleep(0)
        await _wait_until(lambda: len(executor._active_tasks) == 2)
        assert len(executor._task_handles) == 2

        await executor.stop()

        assert executor._task_handles == set()
        assert executor._active_tasks == set()
    finally:
        release.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_handler_exception_commits_retrying_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising handler must leave the task in ``retrying``, never ``running``."""
    engine, factory = await _make_engine(tmp_path)

    async def failing_handler(context) -> TaskExecutionResult:
        raise RuntimeError("handler exploded")

    _install_handler(monkeypatch, failing_handler)
    task_ids = await _seed_tasks(factory, count=1)
    executor = LifecycleTaskExecutor(factory, settings=_settings(), poll_interval=60.0)

    try:
        await executor._poll_once()
        await _wait_until(lambda: not executor._active_tasks)

        # Read through a *new* session so the assertion sees committed state.
        statuses = await _statuses(factory, task_ids)
        assert statuses[task_ids[0]] == "retrying"
    finally:
        await executor.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_broken_retry_write_does_not_leave_task_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the retry write itself fails the task must not stay ``running``.

    This is the exact failure the audit found: ``mark_retrying`` raised, the
    execution session rolled back, and the task was stuck in ``running`` until
    the next process restart ran stale-task recovery.
    """
    engine, factory = await _make_engine(tmp_path)

    async def failing_handler(context) -> TaskExecutionResult:
        raise RuntimeError("handler exploded")

    async def broken_mark_retrying(self, task_id: str):
        raise RuntimeError("cannot write retrying")

    _install_handler(monkeypatch, failing_handler)
    monkeypatch.setattr(TaskRepository, "mark_retrying", broken_mark_retrying)
    task_ids = await _seed_tasks(factory, count=1)
    executor = LifecycleTaskExecutor(factory, settings=_settings(), poll_interval=60.0)

    try:
        await executor._poll_once()
        await _wait_until(lambda: not executor._active_tasks)

        statuses = await _statuses(factory, task_ids)
        assert statuses[task_ids[0]] != TaskStatus.running
        assert statuses[task_ids[0]] == TaskStatus.failed
    finally:
        await executor.stop()
        await engine.dispose()
