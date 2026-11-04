"""Read one Qdrant generation's knowledge graph as a NetworkX graph.

The Qdrant backend keeps entities and relationships in two Qdrant collections
and never writes LightRAG's ``graph_chunk_entity_relation.graphml``.  The graph
router used to look only for that GraphML file, so every Qdrant-backed
generation answered ``404 graph_not_found`` even when the collections held
thousands of entities.  This module rebuilds the same node/edge attributes the
GraphML loader produced so the visualisation works for both backends.

Payload contract (written by :mod:`industrial_rag.physical_qdrant_storage`):

* entities: ``entity_name``, ``content`` (``name\\ndescription``), ``source_id``,
  ``file_path``
* relationships: ``src_id``, ``tgt_id``, ``content`` (``keywords\\tdescription``),
  ``source_id``, ``file_path``
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import networkx as nx
from qdrant_client import AsyncQdrantClient

from industrial_rag.config import Settings
from industrial_rag.graph_display_mapping import infer_entity_type
from industrial_rag.services.qdrant_collection_service import QdrantCollectionService

_SCROLL_LIMIT = 1000
GRAPHML_FILENAME = "graph_chunk_entity_relation.graphml"
DEFAULT_ENTITY_TYPE = "UNKNOWN"


def _entity_type_for(payload: Mapping[str, Any], name: str) -> str:
    """Prefer a stored ``entity_type``; otherwise infer one for display only.

    LightRAG's ``entities_vdb`` meta_fields are exactly
    ``{entity_name, source_id, content, file_path}``, so today's payloads never
    contain a real type and every node would render as "未知".  A stored type
    (present as soon as the payload whitelist includes it) always wins; the
    inferred fallback is marked with ``inferred:`` so the UI can label it as a
    presentation guess rather than an extraction result.
    """

    stored = str(payload.get("entity_type") or "").strip()
    if stored and stored.upper() != DEFAULT_ENTITY_TYPE:
        return stored
    return infer_entity_type(name) or DEFAULT_ENTITY_TYPE


def _display_type(stored: Any, name: str) -> str:
    return _entity_type_for({"entity_type": stored}, name)


def apply_display_entity_types(graph: nx.Graph) -> int:
    """Fill missing node types with display-only inferences, in place.

    GraphML materialised before this change carries ``entity_type=UNKNOWN`` for
    every node.  Repairing the rendered legend must not require rewriting the
    file (or rebuilding the generation), so the projection is completed here at
    read time.  Real types are never overwritten.

    Returns the number of nodes whose type was inferred.
    """

    inferred = 0
    for node_id, attrs in graph.nodes(data=True):
        current = str(attrs.get("entity_type") or "").strip()
        if current and current.upper() != DEFAULT_ENTITY_TYPE:
            continue
        name = str(attrs.get("entity_name") or node_id)
        resolved = _display_type(current, name)
        if resolved == current:
            continue
        attrs["entity_type"] = resolved
        graph.nodes[node_id]["entity_type"] = resolved
        if resolved != DEFAULT_ENTITY_TYPE:
            inferred += 1
    return inferred


def _split_headline(text: str, *, separator: str = "\n") -> tuple[str, str]:
    """Split ``head\\nbody`` into its head and the remaining description."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "", ""
    head, _, tail = cleaned.partition(separator)
    return head.strip(), tail.strip()


def build_graph_from_payloads(
    entity_payloads: Iterable[Mapping[str, Any]],
    relation_payloads: Iterable[Mapping[str, Any]],
) -> nx.Graph:
    """Project Qdrant payloads onto the attributes the graph router reads."""
    graph = nx.Graph()
    for payload in entity_payloads:
        name = str(payload.get("entity_name") or "").strip()
        if not name:
            continue
        _, description = _split_headline(str(payload.get("content") or ""))
        graph.add_node(
            name,
            entity_id=name,
            entity_name=name,
            entity_type=_entity_type_for(payload, name),
            description=description,
            source_id=payload.get("source_id") or "",
            file_path=payload.get("file_path") or "",
        )
    for payload in relation_payloads:
        source = str(payload.get("src_id") or "").strip()
        target = str(payload.get("tgt_id") or "").strip()
        if not source or not target or source == target:
            continue
        raw_content = str(payload.get("content") or "")
        if "\t" in raw_content:
            keywords, description = _split_headline(raw_content, separator="\t")
        else:
            keywords, description = _split_headline(raw_content)
        graph.add_edge(
            source,
            target,
            keywords=keywords,
            description=description,
            weight=1.0,
            source_id=payload.get("source_id") or "",
            file_path=payload.get("file_path") or "",
        )
    return graph


def graph_fingerprint(generation: str, graph: nx.Graph) -> str:
    """Stable identity for layout memoisation (no file to hash on this path)."""
    digest = hashlib.sha256(
        f"qdrant:{generation}:{graph.number_of_nodes()}:{graph.number_of_edges()}".encode()
    ).hexdigest()
    return digest[:16]


async def _scroll_payloads(client: AsyncQdrantClient, collection: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    offset = None
    while True:
        records, offset = await client.scroll(
            collection_name=collection,
            limit=_SCROLL_LIMIT,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        payloads.extend(dict(record.payload or {}) for record in records)
        if offset is None:
            return payloads


def materialize_graphml(graph: nx.Graph, storage_root: Path | str) -> Path:
    """Write the projection as LightRAG's ``graphml`` so queries can traverse it.

    LightRAG reads graph structure through its NetworkX graph storage, which is
    a GraphML file; without it a Qdrant generation answers every ``mix`` query
    from vectors alone.  Writing the file next to the generation's workspace
    keeps both the query engine and the visualiser on the same data.
    """
    target = Path(storage_root) / GRAPHML_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f"{GRAPHML_FILENAME}.tmp")
    nx.write_graphml(graph, staging)
    staging.replace(target)
    return target


async def load_graph_from_qdrant(settings: Settings) -> tuple[nx.Graph, str]:
    """Build the graph of the generation configured on ``settings``.

    Raises ``FileNotFoundError`` when the generation has no entity or
    relationship collection, so callers can keep their existing 404 semantics.
    """
    service = QdrantCollectionService(settings)
    names = service.names()
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=30,
    )
    try:
        entity_collection = names["entities"]
        relation_collection = names["relationships"]
        if not await client.collection_exists(entity_collection):
            raise FileNotFoundError(f"Qdrant collection is missing: {entity_collection}")
        entity_payloads = await _scroll_payloads(client, entity_collection)
        relation_payloads: list[dict[str, Any]] = []
        if await client.collection_exists(relation_collection):
            relation_payloads = await _scroll_payloads(client, relation_collection)
    finally:
        await client.close()

    graph = build_graph_from_payloads(entity_payloads, relation_payloads)
    return graph, graph_fingerprint(settings.qdrant_generation or "", graph)
