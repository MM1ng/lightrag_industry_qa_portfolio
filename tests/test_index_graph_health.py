"""Graph materialisation is verified before a generation becomes active.

Regression: chunk-level checks passed on a generation whose entity extraction
wrote nothing, so the KB reported ``ready`` with an empty knowledge graph and
every query silently degraded to vector-only retrieval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from industrial_rag.config import Settings
from industrial_rag.services.index_service import IndexService
from industrial_rag.services.qdrant_collection_service import QdrantCollectionService
from industrial_rag.vector_collections import VectorBackend

_GRAPH_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n'


def _write_graphml(path: Path, *, nodes: int, edges: int) -> None:
    lines = [_GRAPH_HEADER, '  <graph id="G" edgedefault="directed">\n']
    lines += [f'    <node id="n{i}" />\n' for i in range(nodes)]
    lines += [f'    <edge source="n0" target="n1" />\n' for _ in range(edges)]
    lines.append("  </graph>\n</graphml>\n")
    path.write_text("".join(lines), encoding="utf-8")


def _qdrant_settings() -> Settings:
    return Settings(
        api_key="x" * 40,
        vector_backend=VectorBackend.qdrant,
        qdrant_url="http://127.0.0.1:6333",
        qdrant_kb_id="a" * 32,
        qdrant_generation="g" + "b" * 12,
        qdrant_collection_prefix="ira_test",
    )


class _CountingClient:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts

    async def count(self, name: str, exact: bool = True) -> Any:  # noqa: ARG002
        namespace = name.rsplit("_", 1)[-1]
        return type("Count", (), {"count": self._counts.get(namespace, 0)})()

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_verify_graph_rejects_generation_without_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    service = QdrantCollectionService(_qdrant_settings())
    client = _CountingClient({"chunks": 453, "entities": 0, "relationships": 0})
    monkeypatch.setattr(QdrantCollectionService, "_client", lambda self: client)

    with pytest.raises(RuntimeError, match="knowledge graph is empty"):
        await service.verify_graph()


@pytest.mark.asyncio
async def test_verify_graph_returns_counts_when_graph_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    service = QdrantCollectionService(_qdrant_settings())
    client = _CountingClient({"chunks": 453, "entities": 41, "relationships": 57})
    monkeypatch.setattr(QdrantCollectionService, "_client", lambda self: client)

    assert await service.verify_graph() == (41, 57)


def test_nano_graph_check_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="graph file is missing"):
        IndexService._verify_nano_graph(tmp_path, "kb-token")


def test_nano_graph_check_rejects_empty_graph(tmp_path: Path) -> None:
    storage_root = tmp_path / "kb-token"
    storage_root.mkdir()
    _write_graphml(storage_root / "graph_chunk_entity_relation.graphml", nodes=0, edges=0)

    with pytest.raises(RuntimeError, match="knowledge graph is empty"):
        IndexService._verify_nano_graph(tmp_path, "kb-token")


def test_nano_graph_check_counts_nodes_and_edges(tmp_path: Path) -> None:
    storage_root = tmp_path / "kb-token"
    storage_root.mkdir()
    _write_graphml(storage_root / "graph_chunk_entity_relation.graphml", nodes=6, edges=5)

    assert IndexService._verify_nano_graph(tmp_path, "kb-token") == (6, 5)
