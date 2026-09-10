"""Immutable, canonical execution-input snapshots for UpdateJob runtime work."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_REQUIRED_BY_OPERATION = {
    "add": ("source",),
    "replace": ("source", "target", "replacement"),
    "delete": ("target",),
    "reparse": ("source", "target", "configuration"),
    # A KB without an active generation is a valid reindex baseline.  The
    # explicit null is part of the snapshot and therefore its digest; only the
    # accepted document set and runtime configuration must be present.
    "reindex": ("configuration", "document_bindings"),
}


def snapshot_document(document: Any) -> dict[str, Any]:
    """Capture the document fields that define an accepted update input."""
    return {
        "document_id": document.id,
        "version": document.version,
        "source_hash": document.file_hash,
        "stored_file_name": document.stored_file_name,
    }


def snapshot_configuration(knowledge_base: Any) -> dict[str, Any]:
    """Capture parser, chunking, and embedding configuration at acceptance time."""
    return {
        "parser": knowledge_base.parser_name,
        "parser_version": knowledge_base.parser_version,
        "chunking_strategy": knowledge_base.chunking_strategy,
        "chunking_version": knowledge_base.chunking_version,
        "embedding_model": knowledge_base.embedding_model,
        "embedding_dimension": knowledge_base.embedding_dimension,
    }


def snapshot_base(generation: Any | None) -> dict[str, Any] | None:
    """Capture the immutable identity of an active base generation."""
    if generation is None:
        return None
    return {"generation_id": generation.id, "manifest_hash": generation.document_manifest_hash}


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def build_input_snapshot(
    *,
    operation: str,
    knowledge_base_id: str,
    base_generation: dict[str, Any] | None,
    configuration: dict[str, Any] | None,
    source: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    replacement: dict[str, Any] | None = None,
    document_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a versioned immutable execution intent and its semantic digest."""
    if operation not in _REQUIRED_BY_OPERATION:
        raise ValueError(f"unsupported snapshot operation: {operation}")
    if not knowledge_base_id:
        raise ValueError("knowledge_base_id is required")
    values: dict[str, Any] = {
        "schema_version": 1,
        "operation": operation,
        "knowledge_base_id": knowledge_base_id,
        "base_generation": base_generation,
        "configuration": configuration,
        "source": source,
        "target": target,
        "replacement": replacement,
        "document_bindings": document_bindings,
    }
    for key in _REQUIRED_BY_OPERATION[operation]:
        if values[key] in (None, [], {}):
            raise ValueError(f"{key} is required for {operation} snapshot")
    values["input_digest"] = hashlib.sha256(_canonical_bytes(values)).hexdigest()
    return values
