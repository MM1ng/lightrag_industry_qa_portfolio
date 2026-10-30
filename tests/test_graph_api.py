from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest
from fastapi.testclient import TestClient
from industrial_rag.api import create_app
from industrial_rag.config import Settings
from industrial_rag.db.models import KBStatus
from industrial_rag.vector_collections import VectorBackend

from graph_test_support import (
    isolate_database,
    reset_database,
    seed_knowledge_base,
    seed_qdrant_knowledge_base,
)

GRAPHML = "graph_chunk_entity_relation.graphml"


@pytest.fixture(scope="module", autouse=True)
def _isolated_test_db(tmp_path_factory):
    isolate_database(tmp_path_factory.mktemp("graph_api_db") / "test.db")
    yield


@pytest.fixture(autouse=True)
def _fresh_schema():
    reset_database()
    yield


class _Runtime:
    def query(self, question, *, mode, timeout):
        raise AssertionError("graph routes must not call the runtime")

    def close(self):
        pass


def _storage(tmp_path: Path) -> Path:
    storage = tmp_path / "graph-storage"
    storage.mkdir(parents=True, exist_ok=True)
    graph = nx.Graph()
    graph.add_node("Pump", entity_id="离心泵", entity_type="artifact")
    graph.add_node("Bearing", entity_id="轴承", entity_type="component")
    graph.add_node("Seal", entity_id="机械密封", entity_type="component")
    graph.add_edge("Pump", "Bearing", keywords="contains")
    graph.add_edge("Bearing", "Seal", keywords="connected_to")
    nx.write_graphml(graph, storage / GRAPHML)
    return storage


def _app(tmp_path: Path, *, service_api_key: str | None = None) -> tuple[object, str]:
    storage = _storage(tmp_path)
    kb_id = seed_knowledge_base(workspace=storage)
    settings = Settings(api_key="offline-test-key", working_dir=storage, service_api_key=service_api_key)
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())
    return app, kb_id


def test_graph_overview_is_bounded_and_user_safe(tmp_path: Path):
    app, kb_id = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/overview?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 2
    assert body["stats"]["mode"] == "overview"
    assert {"id", "label", "type", "x", "y", "degree"} == set(body["nodes"][0])
    assert "entity_id" not in body["nodes"][0]


def test_graph_neighborhood_matches_entity_and_hops(tmp_path: Path):
    app, kb_id = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get(
            f"/v1/knowledge-bases/{kb_id}/graph/neighborhood",
            params={"query": "轴承", "hops": 2},
        )
    assert response.status_code == 200
    assert {node["label"] for node in response.json()["nodes"]} == {"轴承", "离心泵", "机械密封"}


def test_graph_missing_file_returns_public_404(tmp_path: Path):
    kb_id = seed_knowledge_base(workspace=tmp_path / "missing")
    settings = Settings(api_key="offline-test-key", working_dir=tmp_path / "missing")
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())
    with TestClient(app) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/overview")
    assert response.status_code == 404
    assert response.json()["code"] == "graph_not_found"


def test_unknown_knowledge_base_is_not_found(tmp_path: Path):
    app, _kb_id = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/v1/knowledge-bases/kb-does-not-exist/graph/overview")
    assert response.status_code == 404
    assert response.json()["code"] == "knowledge_base_not_found"


def test_deleted_knowledge_base_is_hidden(tmp_path: Path):
    storage = _storage(tmp_path)
    kb_id = seed_knowledge_base(workspace=storage, status=KBStatus.deleted)
    settings = Settings(api_key="offline-test-key", working_dir=storage)
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())
    with TestClient(app) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/overview")
    assert response.status_code == 404
    assert response.json()["code"] == "knowledge_base_not_found"


def test_two_knowledge_bases_render_their_own_graph(tmp_path: Path):
    """The regression this scoping exists for: one KB's graph must not leak into another."""

    storage_a = _storage(tmp_path / "a")
    storage_b = tmp_path / "b"
    storage_b.mkdir(parents=True, exist_ok=True)
    graph = nx.Graph()
    graph.add_node("Valve", entity_id="阀门", entity_type="component")
    nx.write_graphml(graph, storage_b / GRAPHML)

    kb_a = seed_knowledge_base(workspace=storage_a, name="KB A")
    kb_b = seed_knowledge_base(workspace=storage_b, name="KB B")
    settings = Settings(api_key="offline-test-key", working_dir=storage_a)
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())

    with TestClient(app) as client:
        response_a = client.get(f"/v1/knowledge-bases/{kb_a}/graph/overview")
        response_b = client.get(f"/v1/knowledge-bases/{kb_b}/graph/overview")

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert {node["label"] for node in response_a.json()["nodes"]} == {"离心泵", "轴承", "机械密封"}
    assert {node["label"] for node in response_b.json()["nodes"]} == {"阀门"}


def test_graph_overview_finds_qdrant_generation_workspace(tmp_path: Path):
    storage = tmp_path / "generation-root"
    workspace = storage / "qdrant-g12345678"
    workspace.mkdir(parents=True)
    graph = nx.Graph()
    graph.add_node("Pump", entity_id="离心泵", entity_type="artifact")
    nx.write_graphml(graph, workspace / GRAPHML)
    kb_id = seed_qdrant_knowledge_base(workspace=workspace, generation="g12345678")
    settings = Settings(
        api_key="offline-test-key",
        working_dir=storage,
        vector_backend=VectorBackend.qdrant,
        qdrant_url="http://127.0.0.1:17333",
    )
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: _Runtime())) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/overview")
    assert response.status_code == 200
    assert response.json()["nodes"][0]["label"] == "离心泵"


def test_graph_native_returns_lightrag_pyvis_document(tmp_path: Path):
    app, kb_id = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/native?limit=2")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "vis-network" in response.text
    assert "Pump" in response.text


def test_graph_is_public_for_browser_but_admin_document_reads_are_protected(tmp_path: Path):
    app, kb_id = _app(tmp_path, service_api_key="service-secret")
    with TestClient(app) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/overview")
        protected = client.get("/v1/knowledge-bases/kb-1/documents")
    assert response.status_code == 200
    assert protected.status_code == 401


def test_graph_unreadable_file_returns_public_503(tmp_path: Path):
    storage = tmp_path / "broken-storage"
    storage.mkdir()
    (storage / GRAPHML).write_text("not graphml", encoding="utf-8")
    kb_id = seed_knowledge_base(workspace=storage)
    app = create_app(
        settings=Settings(api_key="offline-test-key", working_dir=storage),
        runtime_factory=lambda _: _Runtime(),
    )
    with TestClient(app) as client:
        response = client.get(f"/v1/knowledge-bases/{kb_id}/graph/overview")
    assert response.status_code == 503
    assert response.json()["code"] == "graph_unreadable"
