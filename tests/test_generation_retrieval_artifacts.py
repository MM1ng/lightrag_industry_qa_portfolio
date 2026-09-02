from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from industrial_rag.services.generation_artifacts import (
    GenerationArtifactError,
    GenerationArtifactResolver,
    freeze_generation_child_chunks,
    generation_artifact_evidence,
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


def test_resolver_hydrates_the_same_snapshot_bytes_it_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second file read could race a valid manifest check and must not control hydration."""
    workspace = tmp_path / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "A"))],
    )
    snapshot = workspace / "retrieval" / "child_chunks.jsonl"
    original_read_bytes = Path.read_bytes
    reads = 0

    def race_read_bytes(path: Path, *args, **kwargs):
        nonlocal reads
        if path == snapshot:
            reads += 1
            if reads == 2:
                return (json.dumps(_child("doc-1", "child-a", "B")) + "\n").encode()
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", race_read_bytes)
    registry = GenerationArtifactResolver().resolve_registry(
        workspace,
        expected_generation_id="g1",
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )

    assert registry.hydrate(["child-a"])["child-a"].text == "A"
    assert reads == 1


def test_manifest_rejects_missing_document_version_hash_or_name_binding(tmp_path: Path) -> None:
    """Document ID alone cannot prove a generation contains the intended revision."""
    workspace = tmp_path / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 7), _child("doc-1", "child-a", "A"))],
    )
    manifest_path = workspace / "retrieval" / "chunk_manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    del raw["documents"][0]["file_hash"]
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(GenerationArtifactError, match="chunk manifest"):
        load_generation_child_chunks(
            workspace,
            expected_generation_id="g1",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )


def test_verified_evidence_binds_the_exact_manifest_bytes(tmp_path: Path) -> None:
    """Formatting changes still invalidate validation evidence even when JSON semantics match."""
    workspace = tmp_path / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "A"))],
    )
    first = generation_artifact_evidence(
        workspace,
        expected_generation_id="g1",
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )
    manifest_path = workspace / "retrieval" / "chunk_manifest.json"
    manifest_path.write_text("\n" + manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    second = generation_artifact_evidence(
        workspace,
        expected_generation_id="g1",
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )

    assert first.manifest_bytes_sha256 != second.manifest_bytes_sha256
    assert first.snapshot_bytes_sha256 == second.snapshot_bytes_sha256


def test_freeze_publishes_a_deterministic_lexical_artifact_bound_to_the_child_manifest(
    tmp_path: Path,
) -> None:
    """Changing lexical content or its child binding must make a generation unusable."""
    records = [
        (_document("doc-2", 1), _child("doc-2", "child-b", "2196-R 机械密封")),
        (_document("doc-1", 1), _child("doc-1", "child-a", "ISO VG 68")),
    ]
    workspace = tmp_path / "workspace"
    duplicate_workspace = tmp_path / "duplicate-workspace"

    manifest = freeze_generation_child_chunks(
        workspace, generation_id="g1", document_children=list(reversed(records))
    )
    freeze_generation_child_chunks(
        duplicate_workspace, generation_id="g1", document_children=records
    )

    lexical_path = workspace / "retrieval" / "lexical_index.json"
    raw = json.loads(lexical_path.read_text(encoding="utf-8"))
    assert raw["generation_id"] == "g1"
    assert raw["child_manifest_hash"] == manifest.child_manifest_hash
    assert raw["artifact_hash"] == manifest.lexical_index_hash
    assert raw["child_count"] == 2
    assert "content" not in json.dumps(raw, ensure_ascii=False)
    assert (
        lexical_path.read_bytes()
        == (duplicate_workspace / "retrieval" / "lexical_index.json").read_bytes()
    )


def test_generation_validation_rejects_missing_or_forged_lexical_artifacts(tmp_path: Path) -> None:
    """A posting to a non-snapshot child or a changed posting count is not trustworthy."""
    workspace = tmp_path / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "2196-R"))],
    )
    lexical_path = workspace / "retrieval" / "lexical_index.json"
    lexical_path.unlink()
    with pytest.raises(GenerationArtifactError, match="lexical"):
        load_generation_child_chunks(
            workspace,
            expected_generation_id="g1",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )

    freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "2196-R"))],
        replace_existing=True,
    )
    raw = json.loads(lexical_path.read_text(encoding="utf-8"))
    raw["postings"]["2196-R"][0]["child_chunk_id"] = "not-in-snapshot"
    payload = {key: value for key, value in raw.items() if key != "artifact_hash"}
    raw["artifact_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    lexical_path.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path = workspace / "retrieval" / "chunk_manifest.json"
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_raw["lexical_index_hash"] = raw["artifact_hash"]
    manifest_path.write_text(
        json.dumps(manifest_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GenerationArtifactError, match="posting"):
        load_generation_child_chunks(
            workspace,
            expected_generation_id="g1",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )


def test_generation_validation_rejects_wrong_lexical_generation_or_manifest_hash(
    tmp_path: Path,
) -> None:
    """A lexical index belongs to one generation and one manifest only."""
    workspace = tmp_path / "workspace"
    manifest = freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "NPSH"))],
    )
    lexical_path = workspace / "retrieval" / "lexical_index.json"
    raw = json.loads(lexical_path.read_text(encoding="utf-8"))
    raw["generation_id"] = "g2"
    payload = {key: value for key, value in raw.items() if key != "artifact_hash"}
    raw["artifact_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    lexical_path.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path = workspace / "retrieval" / "chunk_manifest.json"
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_raw["lexical_index_hash"] = raw["artifact_hash"]
    manifest_path.write_text(
        json.dumps(manifest_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GenerationArtifactError, match="generation id"):
        load_generation_child_chunks(
            workspace,
            expected_generation_id="g1",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )

    freeze_generation_child_chunks(
        workspace,
        generation_id="g1",
        document_children=[(_document("doc-1", 1), _child("doc-1", "child-a", "NPSH"))],
        replace_existing=True,
    )
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_raw["lexical_index_hash"] = "not-the-index-hash"
    manifest_path.write_text(
        json.dumps(manifest_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GenerationArtifactError, match="hash"):
        load_generation_child_chunks(
            workspace,
            expected_generation_id="g1",
            expected_child_manifest_hash=manifest.child_manifest_hash,
        )
