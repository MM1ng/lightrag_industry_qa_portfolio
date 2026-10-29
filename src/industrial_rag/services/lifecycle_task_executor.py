"""Persistent task executor: polls DB for pending tasks and dispatches them.

Single-process, async, with KB-level mutual exclusion.
Runs inside the FastAPI lifespan.  Shuts down gracefully on SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from industrial_rag.config import Settings
from industrial_rag.db.models import TaskStatus
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.services.task_context import TaskExecutionContext
from industrial_rag.services.task_handlers import get_builtin_registry

logger = logging.getLogger(__name__)


class LifecycleTaskExecutor:
    """Polls for pending tasks and executes them one-at-a-time per KB.

    Not distributed — single-process only.  Uses asyncio.Lock per KB
    to serialise destructive operations.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        settings: Settings | None = None,
        runtime_manager: Any = None,
        poll_interval: float = 1.0,
        max_concurrency: int = 2,
        stale_running_seconds: float = 600.0,
        shutdown_timeout: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._runtime_manager = runtime_manager
        self._poll_interval = poll_interval
        self._max_concurrency = max_concurrency
        self._stale_running_seconds = stale_running_seconds
        self._shutdown_timeout = shutdown_timeout

        self._kb_locks: dict[str, asyncio.Lock] = {}
        self._active_tasks: set[str] = set()
        # Strong references to the running runner coroutines so ``stop()`` can
        # cancel them and so their exceptions are retrieved instead of being
        # reported as "Task exception was never retrieved".
        self._task_handles: set[asyncio.Task[None]] = set()
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ------------------------------------------------------------------
    # Lifespan hooks
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin polling.  Called during FastAPI startup."""
        self._running = True
        await self._recover_stale_tasks()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="task-executor-poll")
        logger.info(
            "Task executor started (poll=%.1fs, max_concurrency=%d)",
            self._poll_interval,
            self._max_concurrency,
        )

    async def stop(self) -> None:
        """Gracefully stop the executor.  Called during FastAPI shutdown.

        Order matters: the poll loop stops claiming new work first, then every
        in-flight runner is cancelled and drained, and only afterwards do we
        fall back to waiting on ``_active_tasks`` for runners that registered
        themselves after the snapshot was taken.  The whole sequence is bounded
        by ``shutdown_timeout``.
        """
        self._running = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._shutdown_timeout

        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await asyncio.wait_for(self._poll_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._poll_task = None

        # Cancel and drain the runners so every ``finally`` block runs and no
        # task is left half-written.
        handles = tuple(self._task_handles)
        for handle in handles:
            handle.cancel()
        if handles:
            remaining = max(deadline - loop.time(), 0.05)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*handles, return_exceptions=True), timeout=remaining
                )
            except TimeoutError:
                logger.warning(
                    "Shutdown: %d task(s) did not stop within %.0fs",
                    len(handles),
                    self._shutdown_timeout,
                )

        # Fallback for runners that were not in the snapshot above.
        while self._active_tasks and loop.time() < deadline:
            await asyncio.sleep(0.05)

        if self._active_tasks:
            logger.warning(
                "Shutdown: %d tasks still running after %.0fs, abandoning",
                len(self._active_tasks),
                self._shutdown_timeout,
            )
        logger.info("Task executor stopped")

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Task executor poll iteration failed")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        async with self._session_factory() as session:
            task_repo = TaskRepository(session)

            # Persist the claim before the background task opens its own session.
            pending = await task_repo.find_pending(limit=1)
            for task in pending:
                claimed = await task_repo.mark_running(task.id)
                if claimed is None:
                    continue
                task_id = claimed.id
                await session.commit()
                handle = asyncio.create_task(
                    self._execute_task(task_id), name=f"task-runner:{task_id}"
                )
                self._task_handles.add(handle)
                handle.add_done_callback(self._on_task_done)

    def _on_task_done(self, handle: asyncio.Task[None]) -> None:
        """Drop the finished handle and retrieve any escaped exception."""
        self._task_handles.discard(handle)
        if handle.cancelled():
            return
        error = handle.exception()
        if error is not None:
            logger.error("Task runner crashed", exc_info=error)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_task(self, task_id: str) -> None:
        """Run one task outside the poll loop.

        Two separate, short-lived sessions are used on purpose.  The first one
        only resolves the knowledge-base identity and closes immediately, so a
        runner queued behind ``self._semaphore`` holds **no** pooled connection
        while it waits.  The second session is opened only after the semaphore
        and the per-KB lock are acquired, and is the one the handler writes in.
        """
        self._active_tasks.add(task_id)
        try:
            kb_id = await self._load_knowledge_base_id(task_id)
            if kb_id is None:
                return

            lock = self._acquire_kb_lock(kb_id)
            async with self._semaphore, lock:
                await self._run_task_in_session(task_id)
        except Exception:
            # The identity read and the semaphore/lock acquisition sit *outside*
            # ``_run_task_in_session``'s guard, so a failure there would escape
            # every recovery path and leave the row in ``running`` until the
            # next process start.  ``_ensure_terminal_status`` is idempotent and
            # only rewrites rows that are still ``running``.
            logger.exception("Task %s failed before execution could start", task_id)
            await self._ensure_terminal_status(task_id)
        finally:
            self._active_tasks.discard(task_id)

    async def _load_knowledge_base_id(self, task_id: str) -> str | None:
        """Read the owning KB id in a throwaway session, then close it."""
        async with self._session_factory() as session:
            task = await TaskRepository(session).get(task_id)
            if task is None:
                return None
            return str(task.knowledge_base_id)

    async def _run_task_in_session(self, task_id: str) -> None:
        """Execute the handler and commit its outcome in a dedicated session."""
        try:
            async with self._session_factory() as session:
                task_repo = TaskRepository(session)
                kb_repo = KnowledgeBaseRepository(session)
                doc_repo = DocumentRepository(session)

                task = await task_repo.get(task_id)
                if task is None:
                    return

                await self._run_handler(task, kb_repo, doc_repo, task_repo)
                await session.commit()
        except Exception:
            # The session above has rolled back, so any status the handler
            # wrote is gone.  Re-record it in a fresh session: the task must
            # never stay ``running`` until the next restart recovery.
            logger.exception("Task %s execution failed", task_id)
            await self._ensure_terminal_status(task_id)

    async def _run_handler(
        self,
        task: Any,
        kb_repo: KnowledgeBaseRepository,
        doc_repo: DocumentRepository,
        task_repo: TaskRepository,
    ) -> None:
        registry = get_builtin_registry()
        handler = registry.get(task.task_type)
        ctx = TaskExecutionContext(
            task=task,
            kb_repo=kb_repo,
            doc_repo=doc_repo,
            task_repo=task_repo,
            runtime_manager=self._runtime_manager,
            settings=self._settings,
        )

        if handler is None:
            await task_repo.mark_failed(
                task.id,
                error_code="no_handler",
                error_message=f"No handler registered for task type {task.task_type.value}",
            )
            return

        try:
            result = await handler(ctx)
            if result.success:
                await task_repo.mark_succeeded(task.id, result=result.result)
            else:
                if task.attempt < task.max_attempts:
                    await task_repo.mark_retrying(task.id)
                else:
                    await task_repo.mark_failed(
                        task.id,
                        error_code=result.error_code,
                        error_message=result.error_message,
                    )
        except Exception:
            if task.attempt < task.max_attempts:
                await task_repo.mark_retrying(task.id)
            else:
                await task_repo.mark_failed(
                    task.id,
                    error_code="handler_exception",
                    error_message="Handler raised an unhandled exception",
                )

    async def _ensure_terminal_status(self, task_id: str) -> None:
        """Guarantee the task leaves ``running`` even if the main write failed."""
        if await self._write_terminal_status(task_id, retry=True):
            return
        await self._write_terminal_status(task_id, retry=False)

    async def _write_terminal_status(self, task_id: str, *, retry: bool) -> bool:
        """Best-effort terminal status write in a brand-new session.

        Args:
            task_id: Task to resolve.
            retry: When ``True`` and the attempt budget allows it, the task is
                moved back to ``retrying``; otherwise it becomes ``failed``.

        Returns:
            ``True`` when the task is already resolved (or gone) or the write
            committed; ``False`` when the write itself raised.
        """
        try:
            async with self._session_factory() as session:
                task_repo = TaskRepository(session)
                task = await task_repo.get(task_id)
                if task is None or task.status is not TaskStatus.running:
                    return True
                if retry and task.attempt < task.max_attempts:
                    await task_repo.mark_retrying(task_id)
                else:
                    await task_repo.mark_failed(
                        task_id,
                        error_code="executor_failure",
                        error_message=(
                            "Task execution failed before a terminal status was committed"
                        ),
                    )
                await session.commit()
                return True
        except Exception:
            logger.exception("Failed to record task %s status (retry=%s)", task_id, retry)
            return False

    # ------------------------------------------------------------------
    # KB locks
    # ------------------------------------------------------------------

    def _acquire_kb_lock(self, kb_id: str) -> asyncio.Lock:
        if kb_id not in self._kb_locks:
            self._kb_locks[kb_id] = asyncio.Lock()
        return self._kb_locks[kb_id]

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _recover_stale_tasks(self) -> None:
        """Recover all interrupted local-process tasks immediately on startup."""
        async with self._session_factory() as session:
            task_repo = TaskRepository(session)
            from sqlalchemy import select

            from industrial_rag.db.models import LifecycleTask

            result = await session.execute(
                select(LifecycleTask).where(LifecycleTask.status == TaskStatus.running)
            )
            interrupted_tasks = result.scalars().all()
            for task in interrupted_tasks:
                await task_repo.mark_retrying(task.id)
            if interrupted_tasks:
                await session.commit()
                logger.info("Recovered %d interrupted running tasks", len(interrupted_tasks))
