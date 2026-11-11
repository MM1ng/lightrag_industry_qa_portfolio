"""Behaviour contracts for parent-context expansion.

``retrieval_context`` is designed but not yet wired into
``LightRAGService.query()`` (see docs/parent-child-chunking-design.md §14). These
tests pin the behaviour that already works so the module is no longer an
unverified zero-coverage island, and so a future integration cannot silently
change the token-budget or de-duplication rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from industrial_rag.retrieval_context import (
    ExpansionConfig,
    expand_context,
)


@dataclass
class FakeChild:
    child_chunk_id: str
    parent_chunk_id: str
    content: str


@dataclass
class FakeParent:
    parent_chunk_id: str
    content: str
    child_chunk_ids: list[str] = field(default_factory=list)
    section_title: str = ""
    page_no: int = 1
    document_id: str = "doc"


class FakeStore:
    def __init__(self, parents: dict[str, object]) -> None:
        self._parents = parents
        self.requested: list[str] = []

    def get_parent(self, parent_id: str):
        self.requested.append(parent_id)
        return self._parents.get(parent_id)


def child(child_id: str, parent_id: str, text: str) -> FakeChild:
    return FakeChild(child_chunk_id=child_id, parent_chunk_id=parent_id, content=text)


def parent(parent_id: str, text: str) -> FakeParent:
    return FakeParent(parent_chunk_id=parent_id, content=text)


def test_disabled_config_returns_raw_child_text() -> None:
    hits = [child("c1", "p1", "第一段")]
    store = FakeStore({})
    result = expand_context(hits, store, config=ExpansionConfig(enabled=False))
    assert result.metadata["expanded"] is False
    assert result.parent_chunks == ()
    assert "第一段" in result.context_text
    # A disabled expansion must not touch the store at all.
    assert store.requested == []


def test_empty_hits_short_circuit_without_store_lookup() -> None:
    store = FakeStore({})
    result = expand_context([], store)
    assert result.metadata["expanded"] is False
    assert store.requested == []


def test_multiple_children_sharing_a_parent_fetch_it_once() -> None:
    """De-duplication is the property that keeps the budget from being wasted."""
    hits = [child("c1", "p1", "a"), child("c2", "p1", "b"), child("c3", "p2", "c")]
    store = FakeStore({"p1": parent("p1", "父块一"), "p2": parent("p2", "父块二")})
    result = expand_context(hits, store, config=ExpansionConfig(keep_source_context=False))
    assert store.requested.count("p1") == 1
    assert store.requested.count("p2") == 1
    assert len(result.parent_chunks) == 2


def test_max_parent_chunks_caps_the_lookup() -> None:
    hits = [child(f"c{i}", f"p{i}", "t") for i in range(5)]
    store = FakeStore({f"p{i}": parent(f"p{i}", "父块") for i in range(5)})
    result = expand_context(hits, store, config=ExpansionConfig(max_parent_chunks=2))
    assert len(store.requested) <= 2
    assert len(result.parent_chunks) <= 2


def test_token_budget_stops_before_overflowing() -> None:
    """Once a parent would exceed the budget, expansion stops adding them."""
    hits = [child(f"c{i}", f"p{i}", "t") for i in range(4)]
    big = "字" * 400
    store = FakeStore({f"p{i}": parent(f"p{i}", big) for i in range(4)})
    result = expand_context(
        hits,
        store,
        config=ExpansionConfig(
            max_parent_chunks=4, max_parent_tokens=500, keep_source_context=False
        ),
    )
    assert result.total_tokens <= 500
    assert len(result.parent_chunks) < 4


def test_missing_parent_is_skipped_not_fatal() -> None:
    hits = [child("c1", "p1", "a"), child("c2", "gone", "b")]
    store = FakeStore({"p1": parent("p1", "父块一")})
    result = expand_context(hits, store, config=ExpansionConfig(keep_source_context=False))
    assert len(result.parent_chunks) == 1
    assert result.parent_chunks[0].parent_chunk_id == "p1"


def test_keep_source_context_can_be_disabled() -> None:
    hits = [child("c1", "p1", "子块原文")]
    store = FakeStore({"p1": parent("p1", "父块原文")})
    kept = expand_context(hits, store, config=ExpansionConfig(keep_source_context=True))
    dropped = expand_context(hits, store, config=ExpansionConfig(keep_source_context=False))
    assert "子块原文" in kept.context_text
    assert "子块原文" not in dropped.context_text


def test_safety_warning_scan_is_a_documented_gap() -> None:
    """The safety-warning branch is an empty placeholder today.

    Pinning it makes the gap explicit: if someone implements it, this test fails
    and forces them to update the design doc and this expectation together.
    """
    hits = [child("c1", "p1", "注意：高温危险")]
    store = FakeStore({"p1": parent("p1", "父块")})
    result = expand_context(hits, store, config=ExpansionConfig(include_safety_warnings=True))
    # No warning section is appended today; context is source + parent only.
    assert "警告" not in result.context_text
    assert result.metadata["expanded"] is True


@pytest.mark.parametrize("bad_config", [ExpansionConfig(max_parent_chunks=0)])
def test_zero_parent_cap_yields_no_parents(bad_config: ExpansionConfig) -> None:
    hits = [child("c1", "p1", "a")]
    store = FakeStore({"p1": parent("p1", "父块")})
    result = expand_context(hits, store, config=bad_config)
    assert result.parent_chunks == ()
