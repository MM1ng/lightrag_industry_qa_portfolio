"""Graph routes must not run CPU-bound work on the event loop thread.

``nx.read_graphml`` plus ``nx.spring_layout`` cost roughly 230 ms on the
production graph, and the old handlers ran both directly inside ``async def``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import networkx as nx
import pytest
from httpx import ASGITransport, AsyncClient
from industrial_rag import graph_visualizer as gv
from industrial_rag.api import create_app
from industrial_rag.config import Settings

from graph_test_support import (
    isolate_database,
    reset_database,
    run,
    seed_knowledge_base,
)


class _Runtime:
    def query(self, question, *, mode, timeout):
        raise AssertionError("graph routes must not call the runtime")

    def close(self):
        pass


@pytest.fixture(scope="module", autouse=True)
def _isolated_test_db(tmp_path_factory):
    isolate_database(tmp_path_factory.mktemp("graph_threading_db") / "test.db")
    yield


@pytest.fixture(autouse=True)
def _fresh_schema():
    reset_database()
    yield


def _app(tmp_path: Path) -> tuple[object, str]:
    storage = tmp_path / "graph-storage"
    storage.mkdir(parents=True, exist_ok=True)
    graph = nx.Graph()
    graph.add_node("Pump", entity_id="离心泵", entity_type="artifact")
    graph.add_node("Bearing", entity_id="轴承", entity_type="component")
    graph.add_node("Seal", entity_id="机械密封", entity_type="component")
    graph.add_edge("Pump", "Bearing", keywords="contains")
    graph.add_edge("Bearing", "Seal", keywords="connected_to")
    nx.write_graphml(graph, storage / gv.GRAPHML_FILENAME)

    kb_id = seed_knowledge_base(workspace=storage)
    settings = Settings(api_key="offline-test-key", working_dir=storage)
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())
    # The lifespan never runs under ASGITransport, so publish the same state
    # the lifespan would have published.
    app.state.resolved_settings = settings
    app.state.runtime = _Runtime()
    app.state.service_api_key = None
    app.state.admin_api_key = None
    return app, kb_id


def _graph_url(kb_id: str, leaf: str) -> str:
    return f"/v1/knowledge-bases/{kb_id}/graph/{leaf}"


async def _get(app, path: str, **params):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, params=params or None)


def test_spring_layout_runs_off_event_loop_thread(tmp_path: Path, monkeypatch) -> None:
    observed: list[threading.Thread] = []
    original = nx.spring_layout

    def _recording(*args, **kwargs):
        observed.append(threading.current_thread())
        return original(*args, **kwargs)

    monkeypatch.setattr(nx, "spring_layout", _recording)
    gv.clear_graph_cache()

    app, kb_id = _app(tmp_path)
    response = run(_get(app, _graph_url(kb_id, "overview")))

    assert response.status_code == 200
    assert observed, "spring_layout was never called"
    loop_thread = threading.main_thread()
    assert [thread.name for thread in observed if thread is loop_thread] == []


def test_graphml_is_parsed_once_for_repeated_requests(tmp_path: Path, monkeypatch) -> None:
    gv.clear_graph_cache()
    app, kb_id = _app(tmp_path)

    reads: list[Path] = []
    original = nx.read_graphml

    def _counting(path, *args, **kwargs):
        reads.append(Path(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(nx, "read_graphml", _counting)

    first = run(_get(app, _graph_url(kb_id, "overview")))
    second = run(_get(app, _graph_url(kb_id, "overview")))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(reads) == 1, f"GraphML parsed {len(reads)} times for two requests"
