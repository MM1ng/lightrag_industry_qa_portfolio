from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from industrial_rag.services.generation_artifacts import (
    GenerationArtifactError,
    GenerationArtifactResolver,
    freeze_generation_child_chunks,
    load_generation_child_chunks,
)


def _document(document_id: str, version: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=document_id,
        version=version,
        file_hash=f"hash-{document_id}-{version}",
        original_file_name=f"{document_id}.pdf",
    )


def _child(document_id: str, chunk_id: str, content: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "parent_chunk_id": f"parent-{chunk_id}",
        "document_id": document_id,
        "document_name": f"{document_id}.pdf",
        "document_version": "1",
        "page_start": 1,
        "page_end": 1,
        "section_path": [],
        "content_type": "normal_text",
        "content": content,
        "embedding_content": content,
        "token_count": 1,
        "source_hash": f"source-{chunk_id}",
        "parent_source_hash": f"parent-source-{chunk_id}",
        "parser": "test",
        "chunking_strategy": "test",
        "chunking_version": "1",
        "metadata": {},
    }


def _write_current(parsed_current: Path, child: dict[str, object]) -> None:
    parsed_current.mkdir(parents=True, exist_ok=True)
    (parsed_current / "child_chunks.jsonl").write_text(
        json.dumps(child, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_rollback_generation_registry_uses_its_frozen_snapshot_after_current_changes(
    tmp_path: Path,
) -> None:
    """Removing the frozen G1 artifact must break rollback hydration; mutating current must not."""
    parsed_current = tmp_path / "parsed" / "documents" / "doc-1" / "current"
    first = _child("doc-1", "child-a", "snapshot A")
    second = _child("doc-1", "child-b", "snapshot B")
    _write_current(parsed_current, first)

    g1 = freeze_generation_child_chunks(
        tmp_path / "generations" / "g1" / "workspace",
        generation_id="g1",
        document_children=[(_document("doc-1", 1), first)],
    )
    _write_current(parsed_current, second)
    g2 = freeze_generation_child_chunks(
        tmp_path / "generations" / "g2" / "workspace",
        generation_id="g2",
        document_children=[(_document("doc-1", 2), second)],
    )
    (parsed_current / "child_chunks.jsonl").unlink()

    resolver = GenerationArtifactResolver()
    restored = resolver.resolve_registry(
        tmp_path / "generations" / "g1" / "workspace",
        expected_generation_id="g1",
        expected_child_manifest_hash=g1.child_manifest_hash,
    )
    newest = resolver.resolve_registry(
        tmp_path / "generations" / "g2" / "workspace",
        expected_generation_id="g2",
        expected_child_manifest_hash=g2.child_manifest_hash,
    )

    assert restored.hydrate(["child-a"])["child-a"].text == "snapshot A"
    assert restored.hydrate(["child-b"])["child-b"].hydration_status == "missing"
    assert newest.hydrate(["child-b"])["child-b"].text == "snapshot B"


def test_manifest_binds_snapshot_hash_count_and_document_versions(tmp_path: Path) -> None:
    """Changing a snapshot or its expected generation identity must make it unusable."""
    workspace = tmp_path / "generation" / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 7), _child("doc-1", "child-a", "A"))],
    )
    assert manifest.count == 1
    assert manifest.documents == (
        {
            "document_id": "doc-1",
            "document_version": 7,
            "file_hash": "hash-doc-1-7",
            "document_name": "doc-1.pdf",
        },
    )

    resolver = GenerationArtifactResolver()
    with pytest.raises(GenerationArtifactError, match="generation id"):
        resolver.resolve_registry(
            workspace,
            expected_generation_id="g2",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )

    snapshot_path = workspace / "retrieval" / "child_chunks.jsonl"
    snapshot_path.write_text(
        json.dumps(_child("doc-1", "child-a", "tampered")) + "\n", encoding="utf-8"
    )
    with pytest.raises(GenerationArtifactError, match="hash"):
        resolver.resolve_registry(
            workspace,
            expected_generation_id="g1",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )


def test_resolver_reloads_when_switching_generation_workspaces(tmp_path: Path) -> None:
    """A runtime cache entry from G1 cannot satisfy a later G2 activation."""
    g1_workspace = tmp_path / "generations" / "g1" / "workspace"
    g2_workspace = tmp_path / "generations" / "g2" / "workspace"
    g1 = freeze_generation_child_chunks(
        g1_workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "A"))],
    )
    g2 = freeze_generation_child_chunks(
        g2_workspace,
        generation_id="g2",
        document_children=[(_document("doc-1", 2), _child("doc-1", "child-b", "B"))],
    )

    resolver = GenerationArtifactResolver()
    first = resolver.resolve_registry(
        g1_workspace,
        expected_generation_id="g1",
        expected_child_manifest_hash=g1.child_manifest_hash,
    )
    second = resolver.resolve_registry(
        g2_workspace,
        expected_generation_id="g2",
        expected_child_manifest_hash=g2.child_manifest_hash,
    )

    assert second is not first
    assert second.hydrate(["child-b"])["child-b"].text == "B"


def test_freezing_accepts_a_one_pass_document_child_iterable(tmp_path: Path) -> None:
    """A streaming builder must retain its document bindings while freezing."""
    pairs = iter([(_document("doc-1", 1), _child("doc-1", "child-a", "A"))])

    manifest = freeze_generation_child_chunks(
        tmp_path / "workspace", generation_id="g1", document_children=pairs
    )

    assert manifest.documents[0]["document_id"] == "doc-1"


def test_builder_loader_reads_only_the_validated_generation_snapshot(tmp_path: Path) -> None:
    """Builder ingestion gets canonical child ids from the frozen generation artifact."""
    workspace = tmp_path / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "A"))],
    )

    children = load_generation_child_chunks(
        workspace,
        expected_generation_id="g1",
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )

    assert [child.chunk_id for child in children] == ["child-a"]


def test_freeze_binds_parser_local_document_ids_to_the_database_document(tmp_path: Path) -> None:
    """Parser chunk identities may be filename-derived, while generation bindings use DB ids."""
    child = _child("parser-derived-id", "child-a", "A")
    manifest = freeze_generation_child_chunks(
        tmp_path / "workspace",
        generation_id="g1",
        document_children=[(_document("db-document-id", 1), child)],
    )

    children = load_generation_child_chunks(
        tmp_path / "workspace",
        expected_generation_id="g1",
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )

    assert children[0].document_id == "db-document-id"


def test_empty_generation_snapshot_is_valid_for_deleting_the_final_document(
    tmp_path: Path,
) -> None:
    """Deletion may legitimately produce an empty but still verifiable generation."""
    manifest = freeze_generation_child_chunks(
        tmp_path / "workspace", generation_id="g1", document_children=[]
    )

    registry = GenerationArtifactResolver().resolve_registry(
        tmp_path / "workspace",
        expected_generation_id="g1",
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )

    assert manifest.count == 0
    assert registry.hydrate(["former-child"])["former-child"].hydration_status == "missing"
