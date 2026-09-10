"""AC-30: candidate input must be a complete, immutable attempt artifact."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    Document,
    DocumentStatus,
    KBOperationLease,
    KnowledgeBase,
    ParsedArtifactReference,
    UpdateJob,
    UpdateJobStatus,
    UpdateOperation,
)
from industrial_rag.parser_models import ChildChunk
from industrial_rag.services.incremental_update_service import (
    IncrementalUpdateService,
    finalize_timed_out_lightrag_status,
    validate_attempt_parsed_artifact,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for name in ("complete.json", "parent_chunks.jsonl", "child_chunks.jsonl"):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((directory / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _reference(directory: Path, *, digest: str | None = None) -> ParsedArtifactReference:
    return ParsedArtifactReference(
        job_id="a" * 32,
        knowledge_base_id="b" * 32,
        attempt=1,
        document_id="c" * 32,
        artifact_path=str(directory),
        artifact_digest=digest,
    )


def _write_complete_artifact(directory: Path) -> None:
    directory.mkdir(parents=True)
    child = ChildChunk(
        chunk_id="child-1",
        parent_chunk_id="parent-1",
        document_id="c" * 32,
        document_name="manual.pdf",
        content="complete artifact",
        embedding_content="complete artifact",
    )
    (directory / "complete.json").write_text(
        json.dumps({"document_id": "c" * 32, "child_chunk_count": 1, "parent_chunk_count": 1}),
        encoding="utf-8",
    )
    (directory / "parent_chunks.jsonl").write_text(
        json.dumps({"parent_chunk_id": "parent-1"}) + "\n", encoding="utf-8"
    )
    (directory / "child_chunks.jsonl").write_text(
        json.dumps(child.to_dict()) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("missing", ("complete.json", "parent_chunks.jsonl", "child_chunks.jsonl"))
def test_partial_attempt_artifact_cannot_be_consumed(tmp_path: Path, missing: str) -> None:
    """Removing any required artifact file makes a candidate input invalid."""
    artifact_dir = tmp_path / "artifact"
    _write_complete_artifact(artifact_dir)
    (artifact_dir / missing).unlink()

    with pytest.raises(RuntimeError, match="incomplete"):
        validate_attempt_parsed_artifact(_reference(artifact_dir, digest="0" * 64))


def test_wrong_artifact_digest_reference_is_rejected(tmp_path: Path) -> None:
    """A reference digest must bind exactly the bytes candidate build consumes."""
    artifact_dir = tmp_path / "artifact"
    _write_complete_artifact(artifact_dir)

    with pytest.raises(RuntimeError, match="digest"):
        validate_attempt_parsed_artifact(_reference(artifact_dir, digest="0" * 64))
    assert validate_attempt_parsed_artifact(
        _reference(artifact_dir, digest=_digest(artifact_dir))
    ) == artifact_dir


def test_timeout_status_finalizer_marks_only_complete_document_processed(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "kv_store_doc_status.json"
    status_path.write_text(
        json.dumps(
            {
                "kb-doc": {
                    "status": "processing",
                    "chunks_count": 2,
                    "chunks_list": ["chunk-1", "chunk-2"],
                    "metadata": {"keep": "this"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert finalize_timed_out_lightrag_status(
        tmp_path, "kb-doc", expected_chunk_count=2
    ) is True
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["kb-doc"]["status"] == "processed"
    assert data["kb-doc"]["metadata"] == {"keep": "this"}


def test_timeout_status_finalizer_refuses_partial_document(tmp_path: Path) -> None:
    status_path = tmp_path / "kv_store_doc_status.json"
    status_path.write_text(
        json.dumps(
            {"kb-doc": {"status": "processing", "chunks_count": 1, "chunks_list": []}}
        ),
        encoding="utf-8",
    )

    assert finalize_timed_out_lightrag_status(
        tmp_path, "kb-doc", expected_chunk_count=2
    ) is False
    assert json.loads(status_path.read_text(encoding="utf-8"))["kb-doc"]["status"] == "failed"


@pytest.mark.asyncio
async def test_duplicate_upload_reuses_failed_non_deleted_document() -> None:
    from industrial_rag.repositories.document_repository import DocumentRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id="b" * 32, name="dedupe"))
            session.add(
                Document(
                    id="c" * 32,
                    knowledge_base_id="b" * 32,
                    original_file_name="manual.pdf",
                    logical_name="manual.pdf",
                    source_type="application/pdf",
                    stored_file_name="manual.pdf",
                    file_path="manual.pdf",
                    file_hash="a" * 64,
                    file_size=1,
                    mime_type="application/pdf",
                    parser_name="PyMuPDF",
                    chunking_strategy="fixed_character",
                    chunking_version="1",
                    status=DocumentStatus.failed,
                    is_active=False,
                )
            )
            await session.commit()
            found = await DocumentRepository(session).find_by_hash("b" * 32, "a" * 64)
            assert found is not None
            assert found.status is DocumentStatus.failed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_late_stale_artifact_cannot_replace_current_attempt_reference(
    tmp_path: Path,
) -> None:
    """A late A registration cannot overwrite the current B artifact reference."""
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id, job_id, document_id = "b" * 32, "a" * 32, "c" * 32
    current = ParsedArtifactReference(
        job_id=job_id,
        knowledge_base_id=kb_id,
        attempt=2,
        document_id=document_id,
        artifact_path=str(tmp_path / "current-b"),
        artifact_digest="b" * 64,
    )
    late = ParsedArtifactReference(
        job_id=job_id,
        knowledge_base_id=kb_id,
        attempt=1,
        document_id=document_id,
        artifact_path=str(tmp_path / "late-a"),
        artifact_digest="a" * 64,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="late-artifact"))
            session.add(
                UpdateJob(
                    id=job_id,
                    knowledge_base_id=kb_id,
                    operation=UpdateOperation.add,
                    status=UpdateJobStatus.claimed,
                    worker_id="worker-b",
                    lease_token="lease-b",
                    fencing_token=2,
                    attempt=2,
                    lease_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                    checkpoint={"parsed_artifact_reference": current.to_checkpoint()},
                )
            )
            session.add(
                KBOperationLease(
                    knowledge_base_id=kb_id,
                    lock_owner="worker-b",
                    lease_token="lease-b",
                    fencing_token=2,
                    expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
            )
            await session.commit()
            registered = await UpdateJobRepository(session).register_parsed_artifact_reference(
                job_id,
                worker_id="worker-a",
                lease_token="lease-a",
                fencing_token=1,
                attempt=1,
                reference=late,
                now=now,
            )
            assert registered is False

        async with factory() as session:
            job = await session.get(UpdateJob, job_id)
            assert job is not None
            assert job.checkpoint["parsed_artifact_reference"] == current.to_checkpoint()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_ingest_marks_its_frozen_workspace_before_lightrag_initialization(
    tmp_path: Path,
) -> None:
    """Candidate-owned snapshots are valid LightRAG workspaces, not legacy storage."""
    candidate_workspace = tmp_path / "candidate"
    _write_complete_artifact(candidate_workspace)
    observed: dict[str, object] = {}

    class _Backend:
        async def ainsert(self, **_kwargs) -> None:
            return None

    class _LightRAG:
        _backend = _Backend()

        async def initialize(self) -> None:
            marker = candidate_workspace / "industrial_rag_index.json"
            observed["marker"] = marker.is_file()
            observed["vector_workspace"] = (
                candidate_workspace / "qdrant-candidate-generation"
            ).is_dir()

        async def close(self) -> None:
            return None

    service = IncrementalUpdateService(
        None,  # type: ignore[arg-type]
        settings=Settings.from_mapping(
            {
                "DASHSCOPE_API_KEY": "test-key",
                "QDRANT_URL": "http://127.0.0.1:6333",
                "VECTOR_BACKEND": "qdrant",
            }
        ),
        lightrag_service_factory=lambda _settings: _LightRAG(),
    )
    service._doc_repo = SimpleNamespace(update=AsyncMock())
    kb = SimpleNamespace(
        id="b" * 32,
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
    )
    generation = SimpleNamespace(generation="candidate-generation", collections={"chunks": "chunks"})
    document = SimpleNamespace(id="c" * 32, original_file_name="manual.pdf")
    children = [
        ChildChunk(
            chunk_id="child-1",
            parent_chunk_id="parent-1",
            document_id=document.id,
            document_name=document.original_file_name,
            content="complete artifact",
            embedding_content="complete artifact",
        )
    ]

    await service._ingest_document(kb, generation, candidate_workspace, document, children=children)

    assert observed["marker"] is True
    assert observed["vector_workspace"] is True
