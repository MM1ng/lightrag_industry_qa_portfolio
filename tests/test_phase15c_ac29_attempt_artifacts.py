"""AC-29: a claimed attempt owns the parsed artifact its candidate consumes."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from industrial_rag.config import Settings
from industrial_rag.db.models import (
    Base,
    ClaimedExecutionContext,
    Document,
    KBOperationLease,
    KnowledgeBase,
    ParsedArtifactReference,
    UpdateJob,
    UpdateJobStatus,
    UpdateOperation,
)
from industrial_rag.parser_models import ChildChunk
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.services.update_job_worker import ClaimedUpdateJob, UpdateJobWorker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _reference(*, attempt: int, root: Path) -> ParsedArtifactReference:
    return ParsedArtifactReference(
        job_id="a" * 32,
        knowledge_base_id="b" * 32,
        attempt=attempt,
        document_id="c" * 32,
        artifact_path=str(root / str(attempt)),
    )


def _context(*, attempt: int, reference: ParsedArtifactReference) -> ClaimedExecutionContext:
    return ClaimedExecutionContext(
        job_id="a" * 32,
        knowledge_base_id="b" * 32,
        attempt=attempt,
        worker_id="worker",
        lease_token="lease",
        fencing_token=attempt,
        lease_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        parsed_artifact_reference=reference,
    )


def test_attempt_a_and_b_contexts_cannot_share_parsed_artifact(tmp_path: Path) -> None:
    """Changing an attempt's artifact path must fail context ownership validation."""
    artifact_a = _reference(attempt=1, root=tmp_path)
    artifact_b = _reference(attempt=2, root=tmp_path)

    assert artifact_a.artifact_path != artifact_b.artifact_path
    assert _context(attempt=1, reference=artifact_a).parsed_artifact_reference == artifact_a
    assert _context(attempt=2, reference=artifact_b).parsed_artifact_reference == artifact_b
    with pytest.raises(ValueError, match="parsed artifact"):
        replace(_context(attempt=1, reference=artifact_a), parsed_artifact_reference=artifact_b)


@pytest.mark.asyncio
async def test_fenced_registration_persists_reference_and_rebuilds_context(tmp_path: Path) -> None:
    """Candidate execution after a new session reads the exact registered attempt artifact."""
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id, job_id, document_id = "b" * 32, "a" * 32, "c" * 32
    reference = ParsedArtifactReference(
        job_id=job_id,
        knowledge_base_id=kb_id,
        attempt=1,
        document_id=document_id,
        artifact_path=str(tmp_path / "attempt-a"),
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="ac29"))
            session.add(
                UpdateJob(
                    id=job_id,
                    knowledge_base_id=kb_id,
                    operation=UpdateOperation.add,
                    checkpoint={
                        "input_snapshot": {"accepted": "input"},
                        "ownership": {"accepted_by": "api"},
                    },
                )
            )
            await session.commit()

        claimed = await UpdateJobWorker(factory, worker_id="worker").claim_one(now=now)
        assert claimed is not None
        async with factory() as session:
            registered = await UpdateJobRepository(session).register_parsed_artifact_reference(
                job_id,
                worker_id="worker",
                lease_token=claimed.lease.lease_token,
                fencing_token=claimed.fencing_token,
                attempt=claimed.attempt,
                reference=reference,
                now=now,
            )
        assert registered is True

        async with factory() as session:
            persisted = await session.get(UpdateJob, job_id)
            assert persisted is not None
            rebuilt = ClaimedUpdateJob(
                job_id=job_id,
                knowledge_base_id=kb_id,
                lease=claimed.lease,
                attempt=claimed.attempt,
                checkpoint=persisted.checkpoint,
            ).execution_context
        assert rebuilt.parsed_artifact_reference == reference
        assert persisted.checkpoint == {
            "input_snapshot": {"accepted": "input"},
            "ownership": {"accepted_by": "api"},
            "parsed_artifact_reference": reference.to_checkpoint(),
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_claimant_cannot_register_parsed_artifact_reference(tmp_path: Path) -> None:
    """A superseded fencing identity cannot alter the current attempt checkpoint."""
    from industrial_rag.repositories.update_job_repository import UpdateJobRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    kb_id, job_id = "d" * 32, "e" * 32
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(KnowledgeBase(id=kb_id, name="stale-ac29"))
            session.add(
                Document(
                    id="f" * 32,
                    knowledge_base_id=kb_id,
                    original_file_name="manual.pdf",
                    stored_file_name="manual.pdf",
                    file_hash="a" * 64,
                    file_size=1,
                )
            )
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
                    checkpoint={"input_snapshot": {"immutable": True}},
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
                reference=ParsedArtifactReference(
                    job_id=job_id, knowledge_base_id=kb_id, attempt=1,
                    document_id="f" * 32, artifact_path=str(tmp_path / "stale"),
                ),
                now=now,
            )
            assert registered is False
            metadata_written = await DocumentRepository(session).mark_parsed_fenced(
                "f" * 32,
                job_id=job_id,
                knowledge_base_id=kb_id,
                worker_id="worker-a",
                lease_token="lease-a",
                fencing_token=1,
                attempt=1,
                page_count=9,
                parent_chunk_count=8,
                child_chunk_count=7,
                now=now,
            )
            assert metadata_written is False

        async with factory() as session:
            persisted = await session.get(UpdateJob, job_id)
            assert persisted is not None
            assert persisted.checkpoint == {"input_snapshot": {"immutable": True}}
            document = await session.get(Document, "f" * 32)
            assert document is not None
            assert document.parse_status == "pending"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_build_consumes_only_its_context_owned_attempt_artifact(
    tmp_path: Path,
) -> None:
    """Changing the reference must change only that attempt's candidate input."""
    from industrial_rag.services.incremental_update_service import IncrementalUpdateService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kb_id, job_id, document_id = "1" * 32, "2" * 32, "3" * 32

    def write_artifact(
        reference: ParsedArtifactReference, content: str
    ) -> ParsedArtifactReference:
        artifact_dir = Path(reference.artifact_path)
        artifact_dir.mkdir(parents=True)
        child = ChildChunk(
            chunk_id=f"child-{reference.attempt}",
            parent_chunk_id=f"parent-{reference.attempt}",
            document_id=document_id,
            document_name="manual.pdf",
            content=content,
            embedding_content=content,
        )
        (artifact_dir / "child_chunks.jsonl").write_text(
            json.dumps(child.to_dict()) + "\n", encoding="utf-8"
        )
        (artifact_dir / "parent_chunks.jsonl").write_text(
            json.dumps({"parent_chunk_id": child.parent_chunk_id}) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "complete.json").write_text(
            json.dumps(
                {
                    "document_id": document_id,
                    "child_chunk_count": 1,
                    "parent_chunk_count": 1,
                }
            ),
            encoding="utf-8",
        )
        digest = hashlib.sha256()
        for name in ("complete.json", "parent_chunks.jsonl", "child_chunks.jsonl"):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update((artifact_dir / name).read_bytes())
            digest.update(b"\0")
        return replace(reference, artifact_digest=digest.hexdigest())

    artifact_a = _reference(attempt=1, root=tmp_path)
    artifact_b = _reference(attempt=2, root=tmp_path)
    artifact_a = replace(artifact_a, job_id=job_id, knowledge_base_id=kb_id, document_id=document_id)
    artifact_b = replace(artifact_b, job_id=job_id, knowledge_base_id=kb_id, document_id=document_id)
    artifact_a = write_artifact(artifact_a, "attempt A")
    artifact_b = write_artifact(artifact_b, "attempt B")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            kb = KnowledgeBase(id=kb_id, name="candidate-artifact")
            document = Document(
                id=document_id,
                knowledge_base_id=kb_id,
                original_file_name="manual.pdf",
                stored_file_name="manual.pdf",
                file_hash="a" * 64,
                file_size=1,
            )
            session.add_all((kb, document))
            await session.commit()
            service = IncrementalUpdateService(session, settings=Settings(api_key="ac29"))
            job_a = UpdateJob(id=job_id, knowledge_base_id=kb_id, operation=UpdateOperation.add, attempt=1)
            job_b = UpdateJob(id=job_id, knowledge_base_id=kb_id, operation=UpdateOperation.add, attempt=2)
            pairs_a, _, _ = await service._candidate_snapshot_pairs(
                kb_id=kb_id, active=None, job=job_a, changed_document=document,
                old_document=None, parsed_artifact_reference=artifact_a,
            )
            pairs_b, _, _ = await service._candidate_snapshot_pairs(
                kb_id=kb_id, active=None, job=job_b, changed_document=document,
                old_document=None, parsed_artifact_reference=artifact_b,
            )
            assert [child.content for _, child in pairs_a] == ["attempt A"]
            assert [child.content for _, child in pairs_b] == ["attempt B"]
            with pytest.raises(RuntimeError, match="does not match candidate ownership"):
                await service._candidate_snapshot_pairs(
                    kb_id=kb_id, active=None, job=job_a, changed_document=document,
                    old_document=None, parsed_artifact_reference=artifact_b,
                )
    finally:
        await engine.dispose()
