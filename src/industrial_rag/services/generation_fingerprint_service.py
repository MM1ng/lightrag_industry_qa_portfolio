"""Deterministic fingerprints for complete vector-index inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from industrial_rag.services.generation_artifacts import child_manifest_hash


@dataclass(frozen=True, slots=True)
class GenerationFingerprint:
    document_manifest_hash: str
    child_chunks_manifest_hash: str
    embedding_config_hash: str
    chunking_config_hash: str


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_generation_fingerprint(
    knowledge_base: Any,
    document_children: Iterable[tuple[Any, Any]],
) -> GenerationFingerprint:
    """Hash active documents, ChildChunks, embedding, and chunking reproducibly."""
    pairs = list(document_children)
    documents = {
        str(doc.id): {
            "id": str(doc.id),
            "version": int(doc.version),
            "file_hash": str(doc.file_hash),
        }
        for doc, _ in pairs
    }
    return GenerationFingerprint(
        document_manifest_hash=_hash([documents[key] for key in sorted(documents)]),
        child_chunks_manifest_hash=child_manifest_hash(pairs),
        embedding_config_hash=_hash(
            {
                "model": knowledge_base.embedding_model,
                "dimension": knowledge_base.embedding_dimension,
            }
        ),
        chunking_config_hash=_hash(
            {
                "strategy": knowledge_base.chunking_strategy,
                "version": knowledge_base.chunking_version,
                "config": knowledge_base.chunking_config or {},
            }
        ),
    )
