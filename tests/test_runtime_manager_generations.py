from __future__ import annotations

from pathlib import Path

import pytest
from industrial_rag.config import Settings
from industrial_rag.runtime_chunk_hydration import ChunkRegistry
from industrial_rag.services.runtime_manager import KnowledgeBaseRuntimeManager
from industrial_rag.vector_collections import VectorBackend


class _Service:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True
        self.initialized = False


class _RegistryService(_Service):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.chunk_registry: ChunkRegistry | None = None

    def bind_chunk_registry(self, registry: ChunkRegistry) -> None:
        self.chunk_registry = registry


def _settings(*, backend: VectorBackend, generation: str | None, workspace: Path) -> Settings:
    return Settings(
        api_key="test",
        vector_backend=backend,
        qdrant_url="http://qdrant.test:6333" if backend is VectorBackend.qdrant else None,
        qdrant_generation=generation,
        qdrant_kb_id="a" * 32 if backend is VectorBackend.qdrant else None,
        working_dir=workspace,
    )


@pytest.mark.asyncio
async def test_runtime_manager_replaces_cached_runtime_when_generation_changes(tmp_path: Path) -> None:
    manager = KnowledgeBaseRuntimeManager(service_factory=_Service)
    first = await manager.get_runtime(
        "a" * 32,
        _settings(backend=VectorBackend.qdrant, generation="g20260731aaa", workspace=tmp_path / "g1"),
    )
    second = await manager.get_runtime(
        "a" * 32,
        _settings(backend=VectorBackend.qdrant, generation="g20260731bbb", workspace=tmp_path / "g2"),
    )

    assert second is not first
    assert first.closed is True
    assert manager.is_cached("a" * 32)


@pytest.mark.asyncio
async def test_runtime_manager_binds_each_generation_registry_before_initialization(
    tmp_path: Path,
) -> None:
    """Using a stale registry after a generation switch would cross-contaminate evidence."""
    manager = KnowledgeBaseRuntimeManager(service_factory=_RegistryService)
    g1_registry = ChunkRegistry.from_records(
        [{"chunk_id": "child-a", "document_name": "a.pdf", "page_start": 1, "content": "A"}],
        source="g1/retrieval/child_chunks.jsonl",
    )
    g2_registry = ChunkRegistry.from_records(
        [{"chunk_id": "child-b", "document_name": "b.pdf", "page_start": 1, "content": "B"}],
        source="g2/retrieval/child_chunks.jsonl",
    )

    first = await manager.get_runtime(
        "a" * 32,
        _settings(backend=VectorBackend.qdrant, generation="g1", workspace=tmp_path / "g1"),
        chunk_registry=g1_registry,
    )
    second = await manager.get_runtime(
        "a" * 32,
        _settings(backend=VectorBackend.qdrant, generation="g2", workspace=tmp_path / "g2"),
        chunk_registry=g2_registry,
    )

    assert first.chunk_registry is g1_registry
    assert first.closed is True
    assert second.chunk_registry is g2_registry


@pytest.mark.asyncio
async def test_runtime_manager_rejects_registry_when_service_cannot_bind_it(tmp_path: Path) -> None:
    """Returning an unbound runtime would bypass canonical snapshot hydration."""
    manager = KnowledgeBaseRuntimeManager(service_factory=_Service)
    registry = ChunkRegistry.from_records(
        [{"chunk_id": "child-a", "document_name": "a.pdf", "page_start": 1, "content": "A"}],
        source="g1/retrieval/child_chunks.jsonl",
    )

    with pytest.raises(RuntimeError, match="does not support chunk registry binding"):
        await manager.get_runtime(
            "a" * 32,
            _settings(backend=VectorBackend.qdrant, generation="g1", workspace=tmp_path / "g1"),
            chunk_registry=registry,
        )
