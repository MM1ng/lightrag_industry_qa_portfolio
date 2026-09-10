"""Generation-stable evidence identity helpers for evaluation contracts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePath
from typing import Any

StableEvidenceIdentity = tuple[str, int]


def normalize_source(value: object) -> str:
    """Normalize a document/source name without tying it to a generation."""
    raw = str(value or "").replace("\\", "/").strip()
    return PurePath(raw).name.casefold()


def normalize_text(value: object) -> str:
    """Normalize text for optional same-page evidence diagnostics."""
    return " ".join(str(value or "").casefold().split())


def stable_evidence_identity(value: Mapping[str, Any]) -> StableEvidenceIdentity | None:
    """Return the cross-generation identity: normalized source and page."""
    source = normalize_source(
        value.get("document_name") or value.get("source_file") or value.get("file_path")
    )
    page = value.get("page_number", value.get("page"))
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        return None
    if not source or page_number < 1:
        return None
    return source, page_number
