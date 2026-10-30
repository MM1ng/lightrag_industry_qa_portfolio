"""Read-only, user-safe GraphML projection for the Vue workbench."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

import networkx as nx
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from industrial_rag import graph_visualizer as graph_tools
from industrial_rag.config import Settings
from industrial_rag.db.models import KBStatus, KnowledgeBase
from industrial_rag.db.session import get_session_factory
from industrial_rag.errors import AppError
from industrial_rag.graph_display_mapping import map_type_zh
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base

# Graph reads are scoped to one knowledge base. The previous application-level
# routes resolved the workspace from process settings, so every visitor saw the
# same graph regardless of which knowledge base they were reading.
router = APIRouter(prefix="/v1/knowledge-bases/{kb_id}/graph", tags=["graph"])

# Layout is deterministic because ``spring_layout`` is seeded, so the result can
# be memoised per (graph fingerprint, subgraph shape, seed).  Bounded to keep
# long-running processes from accumulating positions for stale generations.
_LAYOUT_SEED = 17
_LAYOUT_SCALE = 280
_LAYOUT_CACHE_MAX_ENTRIES = 32
_layout_cache: OrderedDict[tuple[Any, ...], dict[Any, tuple[float, float]]] = OrderedDict()


class GraphNodeResponse(BaseModel):
    id: str
    label: str
    type: str
    x: float
    y: float
    degree: int = Field(ge=0)


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    label: str


class GraphStatsResponse(BaseModel):
    node_count: int
    edge_count: int
    mode: str
    query: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    stats: GraphStatsResponse


async def _kb_settings(request: Request, kb_id: str) -> Settings:
    """Resolve the settings of one knowledge base from its trusted metadata.

    ``active_vector_generation`` is eager loaded because the session is closed
    before the settings are derived; a lazy relationship would raise
    ``MissingGreenlet`` at that point.
    """
    base = getattr(request.app.state, "resolved_settings", None)
    if base is None:
        raise AppError("graph_not_ready", "图谱服务尚未就绪。", status_code=503)
    async with get_session_factory()() as session:
        statement = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.active_vector_generation))
            .where(KnowledgeBase.id == kb_id)
        )
        knowledge_base = (await session.execute(statement)).scalar_one_or_none()
        if knowledge_base is None or knowledge_base.status is KBStatus.deleted:
            raise AppError("knowledge_base_not_found", "知识库不存在。", status_code=404)
        try:
            return settings_for_knowledge_base(base, knowledge_base)
        except RuntimeError as error:
            raise AppError(
                "graph_not_ready", "当前知识库尚未完成索引。", status_code=503
            ) from error


def _graph_path(settings: Settings):
    workspace = settings.vector_workspace
    if workspace is None and settings.qdrant_generation:
        workspace = f"{settings.vector_backend.value}-{settings.qdrant_generation}"
    graph_path = graph_tools.locate_graph_file(settings.working_dir, workspace=workspace)
    if graph_path is None and workspace is not None:
        graph_path = graph_tools.locate_graph_file(settings.working_dir)
    if graph_path is None:
        raise AppError("graph_not_found", "当前知识库没有可用的知识图谱。", status_code=404)
    return graph_path


async def _load_graph(request: Request, kb_id: str) -> tuple[nx.Graph, str]:
    """Load one knowledge base's graph off the event loop with its file identity.

    ``nx.read_graphml`` is a synchronous, CPU/IO bound call that costs hundreds
    of milliseconds on a production graph, so it runs in a worker thread and is
    memoised by ``(path, mtime, size)`` in :mod:`industrial_rag.graph_visualizer`.
    """
    graph_path = _graph_path(await _kb_settings(request, kb_id))
    try:
        fingerprint = await asyncio.to_thread(graph_tools.graph_file_fingerprint, graph_path)
        graph = await asyncio.to_thread(graph_tools.load_graph_cached, graph_path)
    except FileNotFoundError:
        raise AppError("graph_not_found", "当前知识库没有可用的知识图谱。", status_code=404) from None
    except Exception as error:
        raise AppError("graph_unreadable", "知识图谱暂时无法读取。", status_code=503) from error
    return graph, fingerprint


def _positions_for(
    graph: nx.Graph,
    *,
    fingerprint: str,
    kind: str,
    limit: int,
    hops: int,
    center: str | None,
) -> dict[Any, tuple[float, float]]:
    """Return cached ``spring_layout`` positions for one subgraph shape."""
    if graph.number_of_nodes() == 0:
        return {}
    key = (fingerprint, kind, limit, hops, center, _LAYOUT_SEED)
    cached = _layout_cache.get(key)
    if cached is not None:
        _layout_cache.move_to_end(key)
        return cached

    positions = nx.spring_layout(graph, seed=_LAYOUT_SEED, dim=2, scale=_LAYOUT_SCALE)
    _layout_cache[key] = positions
    while len(_layout_cache) > _LAYOUT_CACHE_MAX_ENTRIES:
        _layout_cache.popitem(last=False)
    return positions


def _serialize_graph(
    graph: nx.Graph,
    *,
    mode: str,
    query: str | None,
    positions: dict[Any, tuple[float, float]],
) -> GraphResponse:
    nodes = []
    for node_id, attrs in graph.nodes(data=True):
        identifier = str(node_id)
        label = graph_tools.get_node_display_name(identifier, attrs)
        nodes.append(GraphNodeResponse(
            id=identifier,
            label=label,
            type=map_type_zh(graph_tools.get_node_type(attrs)),
            x=round(float(positions[node_id][0]), 2),
            y=round(float(positions[node_id][1]), 2),
            degree=int(graph.degree(node_id)),
        ))
    nodes.sort(key=lambda node: node.id)

    edges: list[GraphEdgeResponse] = []
    if graph.is_multigraph():
        edge_rows = graph.edges(data=True, keys=True)
        for index, (source, target, _key, attrs) in enumerate(edge_rows):
            edges.append(_edge_response(source, target, attrs, index))
    else:
        for index, (source, target, attrs) in enumerate(graph.edges(data=True)):
            edges.append(_edge_response(source, target, attrs, index))

    return GraphResponse(
        nodes=nodes,
        edges=edges,
        stats=GraphStatsResponse(node_count=len(nodes), edge_count=len(edges), mode=mode, query=query),
    )


def _render_graph_payload(
    graph: nx.Graph,
    *,
    fingerprint: str,
    kind: str,
    limit: int = 0,
    hops: int = 0,
    center: str | None = None,
    mode: str,
    query: str | None,
) -> GraphResponse:
    """Subgraph + layout + serialisation, executed in one worker thread."""
    positions = _positions_for(
        graph, fingerprint=fingerprint, kind=kind, limit=limit, hops=hops, center=center
    )
    return _serialize_graph(graph, mode=mode, query=query, positions=positions)


def _render_native_html(
    graph: nx.Graph,
    *,
    show_edge_labels: bool,
    show_node_labels: bool,
) -> str:
    """PyVis rendering, executed in a worker thread."""
    return graph_tools.render_pyvis_html(
        graph,
        show_edge_labels=show_edge_labels,
        show_all_labels=False,
        label_top_n=15 if show_node_labels else 0,
        height="700px",
        width="100%",
    )


def _edge_response(source: Any, target: Any, attrs: dict[str, Any], index: int) -> GraphEdgeResponse:
    source_id, target_id = str(source), str(target)
    return GraphEdgeResponse(
        id=f"edge-{index}-{source_id}-{target_id}",
        source=source_id,
        target=target_id,
        label=graph_tools.get_edge_relation(attrs),
    )


@router.get("/overview", response_model=GraphResponse)
async def graph_overview(
    request: Request, kb_id: str, limit: int = Query(50, ge=1, le=150)
) -> GraphResponse:
    graph, fingerprint = await _load_graph(request, kb_id)
    subgraph = await asyncio.to_thread(graph_tools.build_overview_subgraph, graph, limit=limit)
    return await asyncio.to_thread(
        _render_graph_payload,
        subgraph,
        fingerprint=fingerprint,
        kind="overview",
        limit=limit,
        mode="overview",
        query=None,
    )


@router.get("/neighborhood", response_model=GraphResponse)
async def graph_neighborhood(
    request: Request,
    kb_id: str,
    query: str = Query(min_length=1, max_length=200),
    hops: int = Query(1, ge=1, le=2),
) -> GraphResponse:
    graph, fingerprint = await _load_graph(request, kb_id)
    subgraph, center = await asyncio.to_thread(
        _build_neighborhood, graph, query=query, hops=hops
    )
    return await asyncio.to_thread(
        _render_graph_payload,
        subgraph,
        fingerprint=fingerprint,
        kind="neighborhood",
        hops=hops,
        center=center,
        mode="neighborhood",
        query=query,
    )


def _build_neighborhood(
    graph: nx.Graph, *, query: str, hops: int
) -> tuple[nx.Graph, str | None]:
    matches = graph_tools.find_matching_nodes(graph, query)
    if not matches:
        return graph.__class__(), None
    return (
        graph_tools.build_neighborhood_subgraph(graph, matches[0], hops=hops),
        str(matches[0]),
    )


@router.get("/native", response_class=HTMLResponse)
async def graph_native(
    request: Request,
    kb_id: str,
    query: str | None = Query(default=None, min_length=1, max_length=200),
    hops: int = Query(1, ge=1, le=2),
    limit: int = Query(50, ge=1, le=150),
    show_node_labels: bool = Query(True),
    show_edge_labels: bool = Query(False),
) -> HTMLResponse:
    graph, _fingerprint = await _load_graph(request, kb_id)
    if query:
        subgraph, _center = await asyncio.to_thread(
            _build_neighborhood, graph, query=query, hops=hops
        )
    else:
        subgraph = await asyncio.to_thread(
            graph_tools.build_overview_subgraph, graph, limit=limit
        )
    try:
        html = await asyncio.to_thread(
            _render_native_html,
            subgraph,
            show_edge_labels=show_edge_labels,
            show_node_labels=show_node_labels,
        )
    except Exception as error:
        raise AppError("graph_unreadable", "知识图谱暂时无法读取。", status_code=503) from error
    return HTMLResponse(content=html)

