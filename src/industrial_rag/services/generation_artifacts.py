"""Immutable, generation-scoped retrieval inputs.

Generation runtimes must resolve ChildChunks from their own workspace, never
from the mutable parsed-document ``current`` directories.  The manifest is
published last, which makes a partially written snapshot unobservable.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from industrial_rag.runtime_chunk_hydration import RuntimeChunkHydrator

CHUNK_MANIFEST_SCHEMA_VERSION = 1
_RETRIEVAL_DIRNAME = "retrieval"
_SNAPSHOT_FILENAME = "child_chunks.jsonl"
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

    def to_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "schema_version": self.schema_version,
            "count": self.count,
            "child_manifest_hash": self.child_manifest_hash,
            "documents": list(self.documents),
            "created_at": self.created_at,
        }


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
    replace_existing: bool = False,
) -> GenerationChunkManifest:
    """Atomically publish one generation's canonical ChildChunk snapshot.

    ``replace_existing`` is only for a new candidate workspace copied from an
    earlier generation; callers must still publish a distinct generation id.
    """
    if not generation_id:
        raise ValueError("generation_id is required")
    pairs = list(document_children)
    records = _snapshot_records(pairs)
    manifest = GenerationChunkManifest(
        generation_id=generation_id,
        schema_version=CHUNK_MANIFEST_SCHEMA_VERSION,
        count=len(records),
        child_manifest_hash=_hash_records(records),
        documents=_document_bindings(pairs, records),
        created_at=datetime.now(UTC).isoformat(),
    )
    retrieval_dir = generation_retrieval_dir(workspace)
    snapshot_path = retrieval_dir / _SNAPSHOT_FILENAME
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
    # Publish the manifest only after the entire snapshot is durable.
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
    )
    return manifest


def load_generation_manifest(
    workspace: Path,
    *,
    expected_generation_id: str | None = None,
    expected_child_manifest_hash: str | None = None,
) -> GenerationChunkManifest:
    """Read and validate the manifest and exact snapshot bytes."""
    retrieval_dir = generation_retrieval_dir(workspace)
    manifest_path = retrieval_dir / _MANIFEST_FILENAME
    snapshot_path = retrieval_dir / _SNAPSHOT_FILENAME
    if not manifest_path.is_file() or not snapshot_path.is_file():
        raise GenerationArtifactError("generation retrieval snapshot or manifest is missing")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationArtifactError("generation chunk manifest is unreadable") from error
    manifest = _manifest_from_dict(raw)
    if expected_generation_id is not None and manifest.generation_id != expected_generation_id:
        raise GenerationArtifactError("generation id does not match the chunk manifest")
    if (
        expected_child_manifest_hash is not None
        and manifest.child_manifest_hash != expected_child_manifest_hash
    ):
        raise GenerationArtifactError("expected child manifest hash does not match the chunk manifest")
    records = _read_snapshot_records(snapshot_path)
    if len(records) != manifest.count:
        raise GenerationArtifactError("generation snapshot count does not match the chunk manifest")
    if _hash_records(records) != manifest.child_manifest_hash:
        raise GenerationArtifactError("generation snapshot hash does not match the chunk manifest")
    _validate_document_bindings(records, manifest.documents)
    return manifest


def load_generation_child_chunks(
    workspace: Path,
    *,
    expected_generation_id: str,
    expected_child_manifest_hash: str,
) -> list[Any]:
    """Load ChildChunks from a validated generation-local snapshot only."""
    load_generation_manifest(
        workspace,
        expected_generation_id=expected_generation_id,
        expected_child_manifest_hash=expected_child_manifest_hash,
    )
    from industrial_rag.parser_models import ChildChunk

    return [
        ChildChunk.from_dict(record)
        for record in _read_snapshot_records(
            generation_retrieval_dir(workspace) / _SNAPSHOT_FILENAME
        )
    ]


class GenerationArtifactResolver:
    """Resolve generation-local registries with manifest-aware cache invalidation."""

    def __init__(self) -> None:
        self._registries: dict[tuple[str, str], RuntimeChunkHydrator] = {}

    def resolve_registry(
        self,
        workspace: Path,
        *,
        expected_generation_id: str,
        expected_child_manifest_hash: str,
    ) -> RuntimeChunkHydrator:
        manifest = load_generation_manifest(
            workspace,
            expected_generation_id=expected_generation_id,
            expected_child_manifest_hash=expected_child_manifest_hash,
        )
        workspace_key = str(Path(workspace).resolve())
        key = (workspace_key, manifest.child_manifest_hash)
        stale = [cached for cached in self._registries if cached[0] == workspace_key and cached != key]
        for cached in stale:
            self._registries.pop(cached, None)
        registry = self._registries.get(key)
        if registry is None:
            registry = RuntimeChunkHydrator.from_jsonl(
                [generation_retrieval_dir(workspace) / _SNAPSHOT_FILENAME]
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
        records.append(record)
    records.sort(key=lambda item: (str(item["document_id"]), str(item["chunk_id"])))
    duplicate_ids = [
        str(records[index]["chunk_id"])
        for index in range(1, len(records))
        if records[index - 1]["chunk_id"] == records[index]["chunk_id"]
    ]
    if duplicate_ids:
        raise GenerationArtifactError(f"duplicate child_chunk_id in generation snapshot: {duplicate_ids[0]}")
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
    else:
        raw = {
            key: value
            for key, value in vars(child).items()
            if not key.startswith("_")
        }
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
        if schema_version != CHUNK_MANIFEST_SCHEMA_VERSION or count < 0:
            raise ValueError
        if not isinstance(documents_raw, list) or not all(isinstance(item, Mapping) for item in documents_raw):
            raise ValueError
        return GenerationChunkManifest(
            generation_id=str(value["generation_id"]),
            schema_version=schema_version,
            count=count,
            child_manifest_hash=str(value["child_manifest_hash"]),
            documents=tuple(dict(item) for item in documents_raw),
            created_at=str(value["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GenerationArtifactError("generation chunk manifest is invalid") from error


def _read_snapshot_records(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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


def _validate_document_bindings(
    records: list[dict[str, object]], documents: tuple[dict[str, object], ...]
) -> None:
    bound_ids = {str(document.get("document_id") or "") for document in documents}
    snapshot_ids = {str(record.get("document_id") or "") for record in records}
    if "" in snapshot_ids or snapshot_ids != bound_ids:
        raise GenerationArtifactError("generation snapshot document bindings do not match manifest")


__all__ = [
    "CHUNK_MANIFEST_SCHEMA_VERSION",
    "GenerationArtifactError",
    "GenerationArtifactResolver",
    "GenerationChunkManifest",
    "child_manifest_hash",
    "freeze_generation_child_chunks",
    "generation_retrieval_dir",
    "load_generation_child_chunks",
    "load_generation_manifest",
]
