"""Tests for rebuilding a graph from Qdrant entity/relationship payloads."""

from __future__ import annotations

import networkx as nx
import pytest
from industrial_rag import qdrant_graph_reader as reader


def _entity(name: str, description: str, **extra: object) -> dict[str, object]:
    payload = {
        "entity_name": name,
        "content": f"{name}\n{description}",
        "source_id": "kb-1-chunk-001",
        "file_path": "manual.pdf",
    }
    payload.update(extra)
    return payload


def _relation(source: str, target: str, keywords: str, description: str) -> dict[str, object]:
    return {
        "src_id": source,
        "tgt_id": target,
        "content": f"{keywords}\t{description}",
        "source_id": "kb-1-chunk-002",
        "file_path": "manual.pdf",
    }


def test_build_graph_projects_node_and_edge_attributes() -> None:
    graph = reader.build_graph_from_payloads(
        [_entity("Pump", "离心泵主体"), _entity("Bearing", "支撑转子的部件")],
        [_relation("Pump", "Bearing", "contains, part-of", "泵体包含轴承")],
    )

    assert graph.number_of_nodes() == 2
    assert graph.number_of_edges() == 1
    assert graph.nodes["Pump"]["entity_name"] == "Pump"
    assert graph.nodes["Pump"]["description"] == "离心泵主体"
    assert graph.nodes["Pump"]["entity_type"] == reader.DEFAULT_ENTITY_TYPE
    assert graph.edges["Pump", "Bearing"]["keywords"] == "contains, part-of"
    assert graph.edges["Pump", "Bearing"]["description"] == "泵体包含轴承"


def test_build_graph_skips_unusable_payloads() -> None:
    graph = reader.build_graph_from_payloads(
        [
            {"entity_name": "", "content": "匿名实体"},
            {"content": "缺少名称"},
            _entity("Pump", "离心泵主体"),
        ],
        [
            {"src_id": "Pump", "tgt_id": "", "content": "孤立关系"},
            {"src_id": "Pump", "tgt_id": "Pump", "content": "自环"},
            _relation("Pump", "Bearing", "contains", "泵体包含轴承"),
        ],
    )

    assert set(graph.nodes) == {"Pump", "Bearing"}
    assert graph.number_of_edges() == 1


def test_build_graph_falls_back_to_newline_keywords() -> None:
    graph = reader.build_graph_from_payloads(
        [_entity("Pump", "离心泵主体"), _entity("Bearing", "支撑转子的部件")],
        [{"src_id": "Pump", "tgt_id": "Bearing", "content": "contains\n泵体包含轴承"}],
    )

    assert graph.edges["Pump", "Bearing"]["keywords"] == "contains"
    assert graph.edges["Pump", "Bearing"]["description"] == "泵体包含轴承"


def test_empty_payloads_produce_empty_graph() -> None:
    graph = reader.build_graph_from_payloads([], [])
    assert isinstance(graph, nx.Graph)
    assert graph.number_of_nodes() == 0


def test_graph_fingerprint_tracks_shape() -> None:
    small = reader.build_graph_from_payloads([_entity("Pump", "泵")], [])
    large = reader.build_graph_from_payloads(
        [_entity("Pump", "泵"), _entity("Bearing", "轴承")],
        [_relation("Pump", "Bearing", "contains", "包含")],
    )

    assert reader.graph_fingerprint("gtest0001", small) == reader.graph_fingerprint(
        "gtest0001", small
    )
    assert reader.graph_fingerprint("gtest0001", small) != reader.graph_fingerprint(
        "gtest0001", large
    )
    assert reader.graph_fingerprint("gtest0002", small) != reader.graph_fingerprint(
        "gtest0001", small
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Pump\n离心泵主体", ("Pump", "离心泵主体")), ("Pump", ("Pump", "")), ("", ("", ""))],
)
def test_split_headline(text: str, expected: tuple[str, str]) -> None:
    assert reader._split_headline(text) == expected


def test_materialize_graphml_writes_lightrag_graph_file(tmp_path) -> None:
    graph = reader.build_graph_from_payloads(
        [
            {"entity_name": "Pump", "content": "Pump\n离心泵主体", "source_id": "c1"},
            {"entity_name": "Bearing", "content": "Bearing\n支撑转子", "source_id": "c2"},
        ],
        [
            {
                "src_id": "Pump",
                "tgt_id": "Bearing",
                "content": "contains\t泵体包含轴承",
                "source_id": "c3",
            }
        ],
    )

    path = reader.materialize_graphml(graph, tmp_path / "qdrant-gtest0001")

    assert path.name == reader.GRAPHML_FILENAME
    assert path.is_file()
    reloaded = nx.read_graphml(path)
    assert set(reloaded.nodes) == {"Pump", "Bearing"}
    assert reloaded.nodes["Pump"]["entity_id"] == "Pump"
    assert reloaded.number_of_edges() == 1
    assert not list(path.parent.glob("*.tmp"))


def test_materialize_graphml_replaces_previous_file(tmp_path) -> None:
    target = tmp_path / "qdrant-gtest0001"
    first = reader.build_graph_from_payloads([_entity("Pump", "泵")], [])
    second = reader.build_graph_from_payloads(
        [_entity("Pump", "泵"), _entity("Valve", "阀门")], []
    )

    reader.materialize_graphml(first, target)
    path = reader.materialize_graphml(second, target)

    assert set(nx.read_graphml(path).nodes) == {"Pump", "Valve"}


def test_stored_entity_type_wins_over_inference() -> None:
    graph = reader.build_graph_from_payloads(
        [_entity("Mechanical Seal", "机械密封", entity_type="artifact")], []
    )

    assert graph.nodes["Mechanical Seal"]["entity_type"] == "artifact"


def test_unknown_stored_type_falls_back_to_inference() -> None:
    graph = reader.build_graph_from_payloads(
        [_entity("Mechanical Seal", "机械密封", entity_type="UNKNOWN")], []
    )

    assert graph.nodes["Mechanical Seal"]["entity_type"] == "inferred:component"


def test_payload_without_type_is_inferred_for_display() -> None:
    """Payloads carry no entity_type today; nodes must not all read 未知."""

    graph = reader.build_graph_from_payloads(
        [
            _entity("Mechanical Seal", "机械密封"),
            _entity("Installation", "安装步骤"),
            _entity("Bearing Temperature", "轴承温度限值"),
            _entity("2196-ANSI-Manual-Chinese.pdf", "手册文件"),
        ],
        [],
    )

    types = {name: graph.nodes[name]["entity_type"] for name in graph.nodes}
    assert types["Mechanical Seal"] == "inferred:component"
    assert types["Installation"] == "inferred:procedure"
    assert types["Bearing Temperature"] == "inferred:measurement"
    assert types["2196-ANSI-Manual-Chinese.pdf"] == "inferred:document"
    assert "UNKNOWN" not in types.values()


def test_unmatched_entity_name_stays_unknown() -> None:
    graph = reader.build_graph_from_payloads([_entity("Zzz", "无类型线索")], [])

    assert graph.nodes["Zzz"]["entity_type"] == reader.DEFAULT_ENTITY_TYPE
