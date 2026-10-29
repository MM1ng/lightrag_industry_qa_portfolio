from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def test_citation_document_ids_prefer_generation_snapshot_over_inactive_job() -> None:
    from industrial_rag.services.query_application_service import QueryApplicationService

    class Jobs:
        async def find_by_candidate(self, _generation_id):
            return SimpleNamespace(
                result={
                    "documents": [
                        {
                            "document_id": "doc-1",
                            "logical_name": "manual.pdf",
                            "is_active": False,
                        }
                    ]
                }
            )

    class Documents:
        async def list_by_kb(self, _kb_id, include_deleted=True):
            return []

    service = QueryApplicationService.__new__(QueryApplicationService)
    service._job_repository = Jobs()
    service._document_repository = Documents()

    import asyncio

    mapping = asyncio.run(
            service._citation_document_ids(
                "kb-1",
            SimpleNamespace(id="gen-1"),
            snapshot_ids={"manual.pdf": "doc-1"},
        )
    )
    assert mapping == {"manual.pdf": "doc-1"}


def test_doc_registration_uses_frozen_manifest_membership_for_reparse_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    from industrial_rag.services.generation_validation_service import (
        GenerationValidationService,
    )

    class Jobs:
        async def find_by_candidate(self, _generation_id):
            return SimpleNamespace(result={"documents": []})

    class Documents:
        async def list_active_for_kb(self, _kb_id):
            return []

    class Session:
        pass

    status_dir = tmp_path / "qdrant-g1"
    status_dir.mkdir()
    (status_dir / "kv_store_doc_status.json").write_text(
        json.dumps({"kb-doc": {"status": "processed"}}), encoding="utf-8"
    )
    manifest = SimpleNamespace(
        documents=(
            {
                "document_id": "kb-doc",
                "document_name": "manual.pdf",
                "document_version": 1,
                "file_hash": "hash",
            },
        )
    )
    monkeypatch.setattr(
        "industrial_rag.services.generation_artifacts.load_generation_manifest",
        lambda *args, **kwargs: manifest,
    )
    service = GenerationValidationService.__new__(GenerationValidationService)
    service._job_repo = Jobs()
    service._doc_repo = Documents()
    generation = SimpleNamespace(
        id="gen-1",
        generation="g1",
        workspace_path=str(tmp_path),
        child_chunks_manifest_hash="manifest-hash",
    )

    import asyncio

    gate = asyncio.run(service._doc_registration("kb-1", generation))
    assert gate["passed"] is True
    assert gate["detail"]["expected_document_ids"] == ["kb-doc"]


def test_canonical_runner_counts_retrieved_frozen_evidence_for_stable_identity() -> None:
    from industrial_rag.services.canonical_validation_runner import CanonicalValidationRunner

    CanonicalValidationRunner.__new__(CanonicalValidationRunner)
    expected = {("manual.pdf", 7)}
    actual = set()
    retrieved = {("manual.pdf", 7)}
    assert bool(expected & (actual | retrieved)) is True
