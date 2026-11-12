"""Immutable, generation-scoped retrieval inputs.

Generation runtimes must resolve ChildChunks from their own workspace, never
from the mutable parsed-document ``current`` directories.  The manifest is
published last, after the child snapshot and lexical index, which makes a
partially written generation artifact unobservable.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from industrial_rag.runtime_chunk_hydration import ChunkRegistry

CHUNK_MANIFEST_SCHEMA_VERSION = 2
_LEGACY_CHUNK_MANIFEST_SCHEMA_VERSION = 1
_RETRIEVAL_DIRNAME = "retrieval"
_SNAPSHOT_FILENAME = "child_chunks.jsonl"
_PARENT_SNAPSHOT_FILENAME = "parent_chunks.jsonl"
_LEXICAL_INDEX_FILENAME = "lexical_index.json"
_MANIFEST_FILENAME = "chunk_manifest.json"


class GenerationArtifactError(RuntimeError):
    """A generation retrieval artifact is missing, corrupt, or mismatched."""


@dataclass(frozen=True, slots=True)
class GenerationChunkManifest:
    generation_id: str
    schema_version: int
    count: int
    child_manifest_hash: str
    documents: tuple[dict[str, object], ...]
    created_at: str
    lexical_index_hash: str = ""
    lexical_index_bytes_sha256: str = ""
    parent_snapshot_hash: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "schema_version": self.schema_version,
            "count": self.count,
            "child_manifest_hash": self.child_manifest_hash,
            "lexical_index_hash": self.lexical_index_hash,
            "lexical_index_bytes_sha256": self.lexical_index_bytes_sha256,
            "parent_snapshot_hash": self.parent_snapshot_hash,
            "documents": list(self.documents),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class GenerationArtifactEvidence:
    """One immutable read of the manifest and snapshot used for verification."""

    manifest: GenerationChunkManifest
    records: tuple[dict[str, object], ...]
    manifest_bytes_sha256: str
    snapshot_bytes_sha256: str
    lexical_index_bytes_sha256: str = ""
    parent_records: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class LegacyLexicalBackfillResult:
    """Outcome of an explicit, snapshot-only legacy lexical artifact migration."""

    status: str
    detail: str


def generation_retrieval_dir(workspace: Path) -> Path:
    return Path(workspace) / _RETRIEVAL_DIRNAME


def child_manifest_hash(document_children: Iterable[tuple[Any, Any]]) -> str:
    """Return the stable hash for the exact ChildChunk snapshot payload."""
    return _hash_records(_snapshot_records(document_children))


def freeze_generation_child_chunks(
    workspace: Path,
    *,
    generation_id: str,
    document_children: Iterable[tuple[Any, Any]],
    document_parents: Iterable[tuple[Any, Any]] = (),
    replace_existing: bool = False,
) -> GenerationChunkManifest:
    """Atomically publish one generation's canonical ChildChunk snapshot.

    ``replace_existing`` is only for a new candidate workspace copied from an
    earlier generation; callers must still publish a distinct generation id.
    """
    if not generation_id:
        raise ValueError("generation_id is required")
    pairs = list(document_children)
    parent_pairs = list(document_parents)
    records = _snapshot_records(pairs)
    parent_records = _parent_snapshot_records(parent_pairs)
    child_hash = _hash_records(records)
    from industrial_rag.services.lexical_retrieval import build_lexical_index, lexical_index_bytes

    lexical_index = build_lexical_index(
        records,
        generation_id=generation_id,
        child_manifest_hash=child_hash,
    )
    lexical_payload = lexical_index_bytes(lexical_index)
    manifest = GenerationChunkManifest(
        generation_id=generation_id,
        schema_version=CHUNK_MANIFEST_SCHEMA_VERSION,
        count=len(records),
        child_manifest_hash=child_hash,
        lexical_index_hash=lexical_index.artifact_hash,
        lexical_index_bytes_sha256=hashlib.sha256(lexical_payload).hexdigest(),
        parent_snapshot_hash=_hash_records(parent_records),
        documents=_document_bindings(pairs, records),
        created_at=datetime.now(UTC).isoformat(),
    )
    retrieval_dir = generation_retrieval_dir(workspace)
    snapshot_path = retrieval_dir / _SNAPSHOT_FILENAME
    parent_snapshot_path = retrieval_dir / _PARENT_SNAPSHOT_FILENAME
    lexical_index_path = retrieval_dir / _LEXICAL_INDEX_FILENAME
    manifest_path = retrieval_dir / _MANIFEST_FILENAME
    if manifest_path.exists() and not replace_existing:
        raise GenerationArtifactError("generation snapshot already exists and is immutable")
    retrieval_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        snapshot_path,
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        ),
    )
    _atomic_write_text(
        parent_snapshot_path,
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for record in parent_records
        ),
    )
    _atomic_write_text(lexical_index_path, lexical_payload.decode("utf-8"))
    # Publish the manifest only after snapshot and lexical artifacts are durable.
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )
    return manifest


def load_generation_manifest(
    workspace: Path,
    *,
    expected_generation_id: str | None = None,
    expected_child_manifest_hash: str | None = None,
) -> GenerationChunkManifest:
    """Read and validate the manifest and exact snapshot bytes."""
    return generation_artifact_evidence(
        workspace,
        expected_generation_id=expected_generation_id,
        expected_child_manifest_hash=expected_child_manifest_hash,
    ).manifest


def generation_artifact_evidence(
    workspace: Path,
    *,
    expected_generation_id: str | None = None,
    expected_child_manifest_hash: str | None = None,
) -> GenerationArtifactEvidence:
    """Return hashes for the exact manifest/snapshot bytes that were validated."""
    return _load_validated_generation_artifact(
        workspace,
        expected_generation_id=expected_generation_id,
        expected_child_manifest_hash=expected_child_manifest_hash,
    )


def _load_validated_generation_artifact(
    workspace: Path,
    *,
    expected_generation_id: str | None,
    expected_child_manifest_hash: str | None,
) -> GenerationArtifactEvidence:
    retrieval_dir = generation_retrieval_dir(workspace)
    manifest_path = retrieval_dir / _MANIFEST_FILENAME
    snapshot_path = retrieval_dir / _SNAPSHOT_FILENAME
    parent_snapshot_path = retrieval_dir / _PARENT_SNAPSHOT_FILENAME
    lexical_index_path = retrieval_dir / _LEXICAL_INDEX_FILENAME
    if not manifest_path.is_file():
        raise GenerationArtifactError("generation retrieval chunk manifest is missing")
    try:
        manifest_bytes = manifest_path.read_bytes()
        raw = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GenerationArtifactError("generation chunk manifest is unreadable") from error
    manifest = _manifest_from_dict(raw)
    if expected_generation_id is not None and manifest.generation_id != expected_generation_id:
        raise GenerationArtifactError("generation id does not match the chunk manifest")
    if (
        expected_child_manifest_hash is not None
        and manifest.child_manifest_hash != expected_child_manifest_hash
    ):
        raise GenerationArtifactError(
            "expected child manifest hash does not match the chunk manifest"
        )
    if not snapshot_path.is_file() or not lexical_index_path.is_file():
        raise GenerationArtifactError("generation retrieval snapshot or lexical index is missing")
    try:
        snapshot_bytes = snapshot_path.read_bytes()
    except OSError as error:
        raise GenerationArtifactError("generation child snapshot is unreadable") from error
    try:
        records = _read_snapshot_records(snapshot_path, payload=snapshot_bytes.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise GenerationArtifactError("generation child snapshot is unreadable") from error
    if len(records) != manifest.count:
        raise GenerationArtifactError("generation snapshot count does not match the chunk manifest")
    if _hash_records(records) != manifest.child_manifest_hash:
        raise GenerationArtifactError("generation snapshot hash does not match the chunk manifest")
    _validate_document_bindings(records, manifest.documents)
    parent_records: list[dict[str, object]] = []
    if manifest.parent_snapshot_hash:
        if not parent_snapshot_path.is_file():
            raise GenerationArtifactError("generation parent snapshot is missing")
        try:
            parent_records = _read_parent_snapshot_records(parent_snapshot_path)
        except (OSError, UnicodeDecodeError) as error:
            raise GenerationArtifactError("generation parent snapshot is unreadable") from error
        if _hash_records(parent_records) != manifest.parent_snapshot_hash:
            raise GenerationArtifactError("generation parent snapshot hash does not match the chunk manifest")
    try:
        lexical_index_bytes = lexical_index_path.read_bytes()
        from industrial_rag.services.lexical_retrieval import (
            load_lexical_index,
            validate_lexical_index,
        )

        lexical_index = load_lexical_index(lexical_index_bytes)
        if hashlib.sha256(lexical_index_bytes).hexdigest() != manifest.lexical_index_bytes_sha256:
            raise ValueError("lexical index bytes hash does not match the chunk manifest")
        if lexical_index.artifact_hash != manifest.lexical_index_hash:
            raise ValueError("lexical index hash does not match the chunk manifest")
        validate_lexical_index(
            lexical_index,
            records,
            generation_id=manifest.generation_id,
            child_manifest_hash=manifest.child_manifest_hash,
        )
    except (OSError, ValueError) as error:
        raise GenerationArtifactError(f"generation lexical index is invalid: {error}") from error
    return GenerationArtifactEvidence(
        manifest=manifest,
        records=tuple(records),
        manifest_bytes_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        snapshot_bytes_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
        lexical_index_bytes_sha256=hashlib.sha256(lexical_index_bytes).hexdigest(),
        parent_records=tuple(parent_records),
    )


def load_generation_child_chunks(
    workspace: Path,
    *,
    expected_generation_id: str,
    expected_child_manifest_hash: str,
) -> list[Any]:
    """Load ChildChunks from a validated generation-local snapshot only."""
    evidence = _load_validated_generation_artifact(
        workspace,
        expected_generation_id=expected_generation_id,
        expected_child_manifest_hash=expected_child_manifest_hash,
    )
    from industrial_rag.parser_models import ChildChunk

    return [ChildChunk.from_dict(record) for record in evidence.records]


def load_generation_parent_records(
    workspace: Path,
    *,
    expected_generation_id: str,
    expected_child_manifest_hash: str,
) -> list[dict[str, object]]:
    """Load parent context only from the validated generation-local snapshot."""
    evidence = _load_validated_generation_artifact(
        workspace,
        expected_generation_id=expected_generation_id,
        expected_child_manifest_hash=expected_child_manifest_hash,
    )
    return [dict(record) for record in evidence.parent_records]


class GenerationArtifactResolver:
    """Resolve generation-local registries with manifest-aware cache invalidation."""

    def __init__(self) -> None:
        self._registries: dict[tuple[str, str, str, str], ChunkRegistry] = {}

    def resolve_registry(
        self,
        workspace: Path,
        *,
        expected_generation_id: str,
        expected_child_manifest_hash: str,
    ) -> ChunkRegistry:
        evidence = _load_validated_generation_artifact(
            workspace,
            expected_generation_id=expected_generation_id,
            expected_child_manifest_hash=expected_child_manifest_hash,
        )
        workspace_key = str(Path(workspace).resolve())
        key = (
            workspace_key,
            evidence.manifest.generation_id,
            evidence.manifest.child_manifest_hash,
            evidence.manifest.parent_snapshot_hash,
        )
        stale = [
            cached for cached in self._registries if cached[0] == workspace_key and cached != key
        ]
        for cached in stale:
            self._registries.pop(cached, None)
        registry = self._registries.get(key)
        if registry is None:
            registry = ChunkRegistry.from_records(
                evidence.records,
                source=str(generation_retrieval_dir(workspace) / _SNAPSHOT_FILENAME),
                parent_records=evidence.parent_records,
            )
            self._registries[key] = registry
        return registry

    def invalidate(self, workspace: Path) -> None:
        workspace_key = str(Path(workspace).resolve())
        for cached in [key for key in self._registries if key[0] == workspace_key]:
            self._registries.pop(cached, None)


def _snapshot_records(document_children: Iterable[tuple[Any, Any]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for document, child in document_children:
        record = _child_to_dict(child)
        document_id = str(getattr(document, "id", record.get("document_id", ""))).strip()
        if not document_id:
            raise GenerationArtifactError("snapshot child has no document_id")
        chunk_id = str(record.get("chunk_id") or "").strip()
        if not chunk_id:
            raise GenerationArtifactError("snapshot child has no chunk_id")
        record["document_id"] = document_id
        record["document_version"] = str(int(getattr(document, "version", 1)))
        record["document_file_hash"] = str(getattr(document, "file_hash", ""))
        record["document_name"] = str(getattr(document, "original_file_name", ""))
        records.append(record)
    records.sort(key=lambda item: (str(item["document_id"]), str(item["chunk_id"])))
    duplicate_ids = [
        str(records[index]["chunk_id"])
        for index in range(1, len(records))
        if records[index - 1]["chunk_id"] == records[index]["chunk_id"]
    ]
    if duplicate_ids:
        raise GenerationArtifactError(
            f"duplicate child_chunk_id in generation snapshot: {duplicate_ids[0]}"
        )
    return records


def _parent_snapshot_records(document_parents: Iterable[tuple[Any, Any]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for document, parent in document_parents:
        record = _child_to_dict(parent)
        document_id = str(getattr(document, "id", record.get("document_id", ""))).strip()
        parent_chunk_id = str(record.get("parent_chunk_id") or "").strip()
        if not document_id or not parent_chunk_id:
            raise GenerationArtifactError("parent snapshot record requires document_id and parent_chunk_id")
        record["document_id"] = document_id
        record["document_version"] = str(int(getattr(document, "version", 1)))
        record["document_file_hash"] = str(getattr(document, "file_hash", ""))
        record["document_name"] = str(getattr(document, "original_file_name", ""))
        records.append(record)
    records.sort(key=lambda item: (str(item["document_id"]), str(item["parent_chunk_id"])))
    duplicate_ids = [
        str(records[index]["parent_chunk_id"])
        for index in range(1, len(records))
        if records[index - 1]["parent_chunk_id"] == records[index]["parent_chunk_id"]
    ]
    if duplicate_ids:
        raise GenerationArtifactError(
            f"duplicate parent_chunk_id in generation snapshot: {duplicate_ids[0]}"
        )
    return records


def _document_bindings(
    document_children: Iterable[tuple[Any, Any]], records: list[dict[str, object]]
) -> tuple[dict[str, object], ...]:
    documents: dict[str, dict[str, object]] = {}
    for document, _child in document_children:
        document_id = str(getattr(document, "id", "")).strip()
        if not document_id:
            raise GenerationArtifactError("document binding has no document id")
        binding = {
            "document_id": document_id,
            "document_version": int(getattr(document, "version", 1)),
            "file_hash": str(getattr(document, "file_hash", "")),
            "document_name": str(getattr(document, "original_file_name", "")),
        }
        previous = documents.setdefault(document_id, binding)
        if previous != binding:
            raise GenerationArtifactError("document has inconsistent generation bindings")
    snapshot_document_ids = {str(record["document_id"]) for record in records}
    if snapshot_document_ids != set(documents):
        raise GenerationArtifactError("chunk snapshot and document bindings disagree")
    return tuple(documents[document_id] for document_id in sorted(documents))


def _child_to_dict(child: Any) -> dict[str, object]:
    if isinstance(child, Mapping):
        raw = dict(child)
    elif hasattr(child, "to_dict"):
        raw = dict(child.to_dict())
    elif is_dataclass(child):
        raw = asdict(child)
    else:
        raw = {key: value for key, value in vars(child).items() if not key.startswith("_")}
    return _json_value(raw)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return _json_value(value.value)
    return value


def _hash_records(records: list[dict[str, object]]) -> str:
    payload = "\n".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifest_from_dict(value: Any) -> GenerationChunkManifest:
    if not isinstance(value, Mapping):
        raise GenerationArtifactError("generation chunk manifest must be an object")
    try:
        schema_version = int(value["schema_version"])
        count = int(value["count"])
        documents_raw = value["documents"]
        if schema_version == _LEGACY_CHUNK_MANIFEST_SCHEMA_VERSION:
            raise GenerationArtifactError(
                "legacy generation manifest requires explicit lexical artifact migration"
            )
        if schema_version != CHUNK_MANIFEST_SCHEMA_VERSION or count < 0:
            raise ValueError
        if not isinstance(documents_raw, list) or not all(
            isinstance(item, Mapping) for item in documents_raw
        ):
            raise ValueError
        required_document_fields = {
            "document_id",
            "document_version",
            "file_hash",
            "document_name",
        }
        if any(not required_document_fields <= set(item) for item in documents_raw):
            raise ValueError
        return GenerationChunkManifest(
            generation_id=str(value["generation_id"]),
            schema_version=schema_version,
            count=count,
            child_manifest_hash=str(value["child_manifest_hash"]),
            lexical_index_hash=str(value["lexical_index_hash"]),
            lexical_index_bytes_sha256=str(value["lexical_index_bytes_sha256"]),
            parent_snapshot_hash=str(value.get("parent_snapshot_hash") or ""),
            documents=tuple(dict(item) for item in documents_raw),
            created_at=str(value["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GenerationArtifactError("generation chunk manifest is invalid") from error


def migrate_legacy_lexical_artifact(
    workspace: Path, *, apply: bool = False
) -> LegacyLexicalBackfillResult:
    """Plan or safely upgrade one schema-v1 snapshot without reading mutable inputs.

    Only an absent lexical file may be created. Existing artifacts, including
    corrupt partially migrated workspaces, are reported as incompatible rather
    than overwritten.
    """
    retrieval_dir = generation_retrieval_dir(workspace)
    manifest_path = retrieval_dir / _MANIFEST_FILENAME
    snapshot_path = retrieval_dir / _SNAPSHOT_FILENAME
    lexical_index_path = retrieval_dir / _LEXICAL_INDEX_FILENAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return LegacyLexicalBackfillResult("incompatible", f"manifest unreadable: {error}")
    if not isinstance(raw, Mapping):
        return LegacyLexicalBackfillResult("incompatible", "manifest is not an object")
    schema_version = raw.get("schema_version")
    if schema_version == CHUNK_MANIFEST_SCHEMA_VERSION:
        try:
            _load_validated_generation_artifact(
                workspace,
                expected_generation_id=None,
                expected_child_manifest_hash=None,
            )
        except GenerationArtifactError as error:
            return LegacyLexicalBackfillResult("incompatible", str(error))
        return LegacyLexicalBackfillResult("already_current", "schema-v2 artifacts are valid")
    if schema_version != _LEGACY_CHUNK_MANIFEST_SCHEMA_VERSION:
        return LegacyLexicalBackfillResult("incompatible", "unsupported manifest schema version")
    if lexical_index_path.exists():
        return LegacyLexicalBackfillResult(
            "incompatible", "legacy workspace already has a lexical artifact; refusing overwrite"
        )
    try:
        legacy_manifest = _legacy_manifest_from_dict(raw)
        snapshot_bytes = snapshot_path.read_bytes()
        records = _read_snapshot_records(snapshot_path, payload=snapshot_bytes.decode("utf-8"))
        if len(records) != legacy_manifest.count:
            raise GenerationArtifactError(
                "generation snapshot count does not match the chunk manifest"
            )
        if _hash_records(records) != legacy_manifest.child_manifest_hash:
            raise GenerationArtifactError(
                "generation snapshot hash does not match the chunk manifest"
            )
        _validate_document_bindings(records, legacy_manifest.documents)
    except (OSError, UnicodeDecodeError, GenerationArtifactError) as error:
        return LegacyLexicalBackfillResult("incompatible", str(error))
    if not apply:
        return LegacyLexicalBackfillResult(
            "would_migrate", "validated frozen snapshot can be upgraded"
        )
    from industrial_rag.services.lexical_retrieval import build_lexical_index, lexical_index_bytes

    lexical_index = build_lexical_index(
        records,
        generation_id=legacy_manifest.generation_id,
        child_manifest_hash=legacy_manifest.child_manifest_hash,
    )
    lexical_payload = lexical_index_bytes(lexical_index)
    upgraded = GenerationChunkManifest(
        generation_id=legacy_manifest.generation_id,
        schema_version=CHUNK_MANIFEST_SCHEMA_VERSION,
        count=legacy_manifest.count,
        child_manifest_hash=legacy_manifest.child_manifest_hash,
        documents=legacy_manifest.documents,
        created_at=legacy_manifest.created_at,
        lexical_index_hash=lexical_index.artifact_hash,
        lexical_index_bytes_sha256=hashlib.sha256(lexical_payload).hexdigest(),
    )
    try:
        _atomic_write_text(lexical_index_path, lexical_payload.decode("utf-8"))
        _atomic_write_text(
            manifest_path,
            json.dumps(
                upgraded.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n",
        )
    except OSError as error:
        return LegacyLexicalBackfillResult("incompatible", f"migration write failed: {error}")
    return LegacyLexicalBackfillResult("migrated", "schema-v1 snapshot upgraded without reparsing")


def _legacy_manifest_from_dict(value: Mapping[str, object]) -> GenerationChunkManifest:
    """Parse only the frozen snapshot fields needed for an explicit v1 upgrade."""
    try:
        count = int(value["count"])
        documents_raw = value["documents"]
        if (
            count < 0
            or not isinstance(documents_raw, list)
            or not all(isinstance(item, Mapping) for item in documents_raw)
        ):
            raise ValueError
        required_document_fields = {"document_id", "document_version", "file_hash", "document_name"}
        if any(not required_document_fields <= set(item) for item in documents_raw):
            raise ValueError
        return GenerationChunkManifest(
            generation_id=str(value["generation_id"]),
            schema_version=_LEGACY_CHUNK_MANIFEST_SCHEMA_VERSION,
            count=count,
            child_manifest_hash=str(value["child_manifest_hash"]),
            documents=tuple(dict(item) for item in documents_raw),
            created_at=str(value["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GenerationArtifactError("legacy generation chunk manifest is invalid") from error


def _read_snapshot_records(path: Path, *, payload: str | None = None) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    try:
        source = payload if payload is not None else path.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, Mapping):
                raise GenerationArtifactError(f"snapshot row must be an object: {line_number}")
            records.append(dict(record))
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationArtifactError("generation child snapshot is unreadable") from error
    records.sort(key=lambda item: (str(item.get("document_id", "")), str(item.get("chunk_id", ""))))
    return records


def _read_parent_snapshot_records(path: Path) -> list[dict[str, object]]:
    records = _read_snapshot_records(path)
    for record in records:
        if not str(record.get("parent_chunk_id") or "").strip():
            raise GenerationArtifactError("parent snapshot row requires parent_chunk_id")
    records.sort(key=lambda item: (str(item.get("document_id", "")), str(item["parent_chunk_id"])))
    return records


def _validate_document_bindings(
    records: list[dict[str, object]], documents: tuple[dict[str, object], ...]
) -> None:
    bindings = {str(document.get("document_id") or ""): document for document in documents}
    snapshot_ids = {str(record.get("document_id") or "") for record in records}
    if "" in snapshot_ids or snapshot_ids != set(bindings):
        raise GenerationArtifactError("generation snapshot document bindings do not match manifest")
    for record in records:
        binding = bindings[str(record["document_id"])]
        if (
            str(record.get("document_version") or "") != str(binding["document_version"])
            or str(record.get("document_file_hash") or "") != str(binding["file_hash"])
            or str(record.get("document_name") or "") != str(binding["document_name"])
        ):
            raise GenerationArtifactError(
                "generation snapshot document binding does not match manifest"
            )


__all__ = [
    "CHUNK_MANIFEST_SCHEMA_VERSION",
    "GenerationArtifactError",
    "GenerationArtifactEvidence",
    "GenerationArtifactResolver",
    "GenerationChunkManifest",
    "LegacyLexicalBackfillResult",
    "child_manifest_hash",
    "freeze_generation_child_chunks",
    "generation_artifact_evidence",
    "generation_retrieval_dir",
    "load_generation_child_chunks",
    "load_generation_manifest",
    "load_generation_parent_records",
    "migrate_legacy_lexical_artifact",
]
