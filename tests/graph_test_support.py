"""Shared helpers for the knowledge-base scoped graph routes.

Graph reads resolve their workspace from knowledge-base metadata, so the tests
need a real row behind the request. Each module points ``DATABASE_URL`` at its
own temporary SQLite file and recreates the schema per test.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from industrial_rag.db.models import (
    KBStatus,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import get_session_factory, init_db, reset_for_testing
from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository


def run(coro):
    """Run ``coro`` on a fresh loop bound to the calling thread."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def isolate_database(db_path: Path) -> None:
    """Point the process at a private database file (call once per module)."""
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    reset_for_testing()


def reset_database() -> None:
    """Drop and recreate every table so each test starts from a known state."""
    run(init_db(drop_all=True))


def seed_knowledge_base(
    *,
    workspace: Path,
    name: str = "Graph KB",
    status: KBStatus = KBStatus.ready,
) -> str:
    """Insert one knowledge base and return its id."""

    async def _seed() -> str:
        async with get_session_factory()() as session:
            knowledge_base = await KnowledgeBaseRepository(session).create(
                name=name,
                description="graph route fixture",
                workspace_path=str(workspace),
                upload_path=str(workspace / "uploads"),
                parsed_path=str(workspace / "parsed"),
                vector_backend="nano",
            )
            # ``create`` always starts in ``creating``; the status is what the
            # route filters on, so set it explicitly before committing.
            knowledge_base.status = status
            await session.commit()
            return knowledge_base.id

    return run(_seed())


def seed_qdrant_knowledge_base(
    *,
    workspace: Path,
    generation: str = "gtest0001",
    name: str = "Qdrant Graph KB",
) -> str:
    """Insert a Qdrant knowledge base with one active generation record.

    Qdrant knowledge bases derive ``working_dir`` from the generation record, so
    a row without one is rejected by ``settings_for_knowledge_base``.
    """

    async def _seed() -> str:
        async with get_session_factory()() as session:
            knowledge_base = await KnowledgeBaseRepository(session).create(
                name=name,
                description="qdrant graph route fixture",
                workspace_path=str(workspace),
                upload_path=str(workspace / "uploads"),
                parsed_path=str(workspace / "parsed"),
                vector_backend="qdrant",
            )
            knowledge_base.status = KBStatus.ready
            record = VectorIndexGeneration(
                knowledge_base_id=knowledge_base.id,
                backend="qdrant",
                generation=generation,
                status=VectorIndexGenerationStatus.active,
                workspace_path=str(workspace),
                document_manifest_hash="d" * 64,
                child_chunks_manifest_hash="c" * 64,
                embedding_config_hash="e" * 64,
                chunking_config_hash="h" * 64,
            )
            session.add(record)
            await session.flush()
            knowledge_base.active_vector_generation_id = record.id
            await session.commit()
            return knowledge_base.id

    return run(_seed())
