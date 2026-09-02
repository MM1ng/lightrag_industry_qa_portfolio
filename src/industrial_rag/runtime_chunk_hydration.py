"""Exact-ID hydration for offline Runtime evidence replay.

This module reads an existing chunk registry.  It never accepts a question,
performs similarity search, calls Qdrant search, or consults evaluation data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from industrial_rag.citation_formatter import Citation, encode_source_ref
from industrial_rag.evidence_completion import ContextRecord

_FORBIDDEN_KEYS = frozenset(
    {
        "supporting_actual_chunk_ids",
        "expected_support_chunk_ids",
        "expected_evidence",
        "expected_answer",
        "golden",
        "validation",
        "holdout",
        "oracle",
        "evaluation",
    }
)


@dataclass(frozen=True, slots=True)
class HydratedChunk:
    chunk_id: str
    text: str
    hydration_status: str
    original_text_length: int
    hydrated_text_length: int
    truncated: bool
    hydration_source: str | None


class RuntimeChunkHydrator:
    def __init__(self, records: Mapping[str, tuple[str, str]]) -> None:
        self._records = dict(records)

    @classmethod
    def from_jsonl(cls, paths: Iterable[Path]) -> RuntimeChunkHydrator:
        records: dict[str, tuple[str, str]] = {}
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError(f"chunk registry row must be an object: {path}:{line_number}")
                _reject_evaluation_labels(value)
                chunk_id = str(value.get("chunk_id") or value.get("id") or "").strip()
                content = str(value.get("content") or "")
                if not chunk_id or not content:
                    continue
                previous = records.get(chunk_id)
                current = (content, str(path))
                if previous is not None and previous[0] != content:
                    raise ValueError(f"duplicate chunk_id has different text: {chunk_id}")
                records.setdefault(chunk_id, current)
        return cls(records)

    @classmethod
    def from_records(
        cls,
        values: Iterable[Mapping[str, Any]],
        *,
        source: str,
    ) -> RuntimeChunkHydrator:
        """Build a registry from already-validated in-memory artifact rows."""
        records: dict[str, tuple[str, str]] = {}
        for value in values:
            _reject_evaluation_labels(value)
            chunk_id = str(value.get("chunk_id") or value.get("id") or "").strip()
            content = str(value.get("content") or "")
            if not chunk_id or not content:
                continue
            previous = records.get(chunk_id)
            if previous is not None and previous[0] != content:
                raise ValueError(f"duplicate chunk_id has different text: {chunk_id}")
            records.setdefault(chunk_id, (content, source))
        return cls(records)

    @property
    def available_chunk_ids(self) -> frozenset[str]:
        return frozenset(self._records)

    def hydrate(
        self,
        chunk_ids: Iterable[str],
        *,
        max_text_chars: int | None = None,
    ) -> dict[str, HydratedChunk]:
        result: dict[str, HydratedChunk] = {}
        for raw_chunk_id in chunk_ids:
            chunk_id = str(raw_chunk_id).strip()
            if not chunk_id or chunk_id in result:
                continue
            record = self._records.get(chunk_id)
            if record is None:
                result[chunk_id] = HydratedChunk(
                    chunk_id=chunk_id,
                    text="",
                    hydration_status="missing",
                    original_text_length=0,
                    hydrated_text_length=0,
                    truncated=False,
                    hydration_source=None,
                )
                continue
            content, source = record
            hydrated = content
            truncated = False
            if max_text_chars is not None:
                if max_text_chars < 1:
                    raise ValueError("max_text_chars must be positive when provided")
                hydrated = content[:max_text_chars]
                truncated = len(hydrated) < len(content)
            result[chunk_id] = HydratedChunk(
                chunk_id=chunk_id,
                text=hydrated,
                hydration_status="hydrated",
                original_text_length=len(content),
                hydrated_text_length=len(hydrated),
                truncated=truncated,
                hydration_source=source,
            )
        return result


class UnresolvedChunkIdError(RuntimeError):
    """A LightRAG hit named a child chunk absent from the frozen snapshot."""


class ChunkRegistry:
    """Generation-local canonical child-chunk metadata keyed by ``child_chunk_id``.

    The registry is constructed from one already-validated generation snapshot.
    It deliberately has no filesystem or similarity-search behavior, so callers
    cannot fall back to mutable parsed/current or legacy context registries.
    """

    def __init__(
        self,
        records: Mapping[str, Mapping[str, Any]],
        *,
        source: str,
        parent_records: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._records = {chunk_id: dict(record) for chunk_id, record in records.items()}
        self._parent_records = {
            parent_chunk_id: dict(record)
            for parent_chunk_id, record in (parent_records or {}).items()
        }
        self._source = source

    @classmethod
    def from_records(
        cls,
        values: Iterable[Mapping[str, Any]],
        *,
        source: str,
        parent_records: Iterable[Mapping[str, Any]] = (),
    ) -> ChunkRegistry:
        records: dict[str, Mapping[str, Any]] = {}
        for value in values:
            _reject_evaluation_labels(value)
            chunk_id = str(value.get("chunk_id") or "").strip()
            if not chunk_id:
                raise ValueError("generation chunk registry row requires child_chunk_id")
            if chunk_id in records:
                raise ValueError(f"duplicate child_chunk_id in chunk registry: {chunk_id}")
            records[chunk_id] = dict(value)
        parents: dict[str, Mapping[str, Any]] = {}
        for value in parent_records:
            _reject_evaluation_labels(value)
            parent_chunk_id = str(value.get("parent_chunk_id") or "").strip()
            if not parent_chunk_id:
                raise ValueError("generation parent registry row requires parent_chunk_id")
            if parent_chunk_id in parents:
                raise ValueError(f"duplicate parent_chunk_id in chunk registry: {parent_chunk_id}")
            parents[parent_chunk_id] = dict(value)
        return cls(records, source=source, parent_records=parents)

    @property
    def available_chunk_ids(self) -> frozenset[str]:
        return frozenset(self._records)

    def record_for_chunk(self, chunk_id: str) -> Mapping[str, Any] | None:
        """Return immutable-snapshot metadata for one canonical child ID."""
        record = self._records.get(str(chunk_id).strip())
        return dict(record) if record is not None else None

    def hydrate(
        self,
        chunk_ids: Iterable[str],
        *,
        max_text_chars: int | None = None,
    ) -> dict[str, HydratedChunk]:
        result: dict[str, HydratedChunk] = {}
        for raw_chunk_id in chunk_ids:
            chunk_id = str(raw_chunk_id).strip()
            if not chunk_id or chunk_id in result:
                continue
            record = self._records.get(chunk_id)
            if record is None:
                result[chunk_id] = HydratedChunk(
                    chunk_id=chunk_id,
                    text="",
                    hydration_status="missing",
                    original_text_length=0,
                    hydrated_text_length=0,
                    truncated=False,
                    hydration_source=None,
                )
                continue
            content = str(record.get("content") or "")
            hydrated = content
            truncated = False
            if max_text_chars is not None:
                if max_text_chars < 1:
                    raise ValueError("max_text_chars must be positive when provided")
                hydrated = content[:max_text_chars]
                truncated = len(hydrated) < len(content)
            result[chunk_id] = HydratedChunk(
                chunk_id=chunk_id,
                text=hydrated,
                hydration_status="hydrated",
                original_text_length=len(content),
                hydrated_text_length=len(hydrated),
                truncated=truncated,
                hydration_source=self._source,
            )
        return result

    def context_records(
        self, *, knowledge_base_id: str, generation_id: str
    ) -> dict[str, ContextRecord]:
        """Adapt frozen child metadata for the existing evidence-completion logic."""
        records: dict[str, ContextRecord] = {}
        for chunk_id, row in self._records.items():
            metadata = row.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            records[chunk_id] = ContextRecord(
                knowledge_base_id=knowledge_base_id,
                generation_id=generation_id,
                document_id=str(row.get("document_id") or ""),
                document_name=str(row.get("document_name") or ""),
                chunk_id=chunk_id,
                text=str(row.get("content") or ""),
                page_start=int(row.get("page_start") or 1),
                section_path=tuple(str(item) for item in row.get("section_path", ()) or ()),
                parent_chunk_id=_optional_text(row.get("parent_chunk_id")),
                previous_chunk_id=_optional_text(
                    row.get("previous_chunk_id", metadata.get("previous_chunk_id"))
                ),
                next_chunk_id=_optional_text(
                    row.get("next_chunk_id", metadata.get("next_chunk_id"))
                ),
                table_id=_optional_text(row.get("table_id", metadata.get("table_id"))),
                table_header_chunk_id=_optional_text(
                    row.get("table_header_chunk_id", metadata.get("table_header_chunk_id"))
                ),
            )
        for parent_chunk_id, row in self._parent_records.items():
            records[parent_chunk_id] = ContextRecord(
                knowledge_base_id=knowledge_base_id,
                generation_id=generation_id,
                document_id=str(row.get("document_id") or ""),
                document_name=str(row.get("document_name") or ""),
                chunk_id=parent_chunk_id,
                text=str(row.get("content") or ""),
                page_start=int(row.get("page_start") or 1),
                section_path=tuple(str(item) for item in row.get("section_path", ()) or ()),
            )
        return records

    def hydrate_lightrag_evidence(self, evidence: Mapping[str, Any]) -> dict[str, Any]:
        """Replace each identified LightRAG hit with its exact frozen child record.

        Scores and retrieval-source fields remain LightRAG outputs.  All
        evidence content and provenance fields come from the immutable
        generation snapshot, keyed only by the returned child ID.
        """
        hydrated = dict(evidence)
        data = evidence.get("data")
        if not isinstance(data, Mapping):
            return hydrated
        hydrated_data = dict(data)
        for field in ("chunks", "references"):
            values = data.get(field)
            if not isinstance(values, list):
                continue
            rows: list[object] = []
            for value in values:
                if not isinstance(value, Mapping):
                    rows.append(value)
                    continue
                child_chunk_id = _lightrag_child_chunk_id(value)
                if child_chunk_id is None:
                    if _is_evidence_identifiable(value):
                        raise UnresolvedChunkIdError("unresolved child_chunk_id: missing")
                    rows.append(dict(value))
                    continue
                record = self._records.get(child_chunk_id)
                if record is None:
                    raise UnresolvedChunkIdError(
                        f"unresolved child_chunk_id: {child_chunk_id}"
                    )
                rows.append(_canonical_lightrag_hit(value, child_chunk_id, record))
            hydrated_data[field] = rows
        hydrated["data"] = hydrated_data
        return hydrated


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _lightrag_child_chunk_id(value: Mapping[str, Any]) -> str | None:
    chunk_id = str(value.get("child_chunk_id") or "").strip()
    return chunk_id or None


def _is_evidence_identifiable(value: Mapping[str, Any]) -> bool:
    if str(value.get("chunk_id") or "").strip():
        return True
    file_path = value.get("file_path")
    if isinstance(file_path, str) and file_path.startswith("rag-source::"):
        return True
    content = value.get("content")
    return isinstance(content, str) and "[[INDUSTRIAL_RAG_SOURCE " in content


def _canonical_lightrag_hit(
    hit: Mapping[str, Any], child_chunk_id: str, record: Mapping[str, Any]
) -> dict[str, Any]:
    document_name = str(record.get("document_name") or "").strip()
    page_number = int(record.get("page_start") or 1)
    if not document_name or page_number < 1:
        raise ValueError(f"generation chunk registry has invalid citation metadata: {child_chunk_id}")
    return {
        **dict(hit),
        "child_chunk_id": child_chunk_id,
        "chunk_id": child_chunk_id,
        "document_id": str(record.get("document_id") or ""),
        "document_name": document_name,
        "page_start": page_number,
        "page_end": record.get("page_end"),
        "section_path": tuple(str(item) for item in record.get("section_path", ()) or ()),
        "section_title": record.get("section_title"),
        "parent_chunk_id": record.get("parent_chunk_id"),
        "content": str(record.get("content") or ""),
        "file_path": encode_source_ref(Citation(document_name, page_number, child_chunk_id)),
    }


def _reject_evaluation_labels(value: Mapping[str, Any]) -> None:
    for key in value:
        normalized = str(key).casefold()
        if normalized in _FORBIDDEN_KEYS or any(
            marker in normalized for marker in ("golden", "holdout", "validation")
        ):
            raise ValueError("evaluation label is not allowed during hydration")


__all__ = [
    "ChunkRegistry",
    "HydratedChunk",
    "RuntimeChunkHydrator",
    "UnresolvedChunkIdError",
]
