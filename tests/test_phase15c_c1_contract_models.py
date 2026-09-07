"""C1 data-contract checks only; no workers, services, or external calls."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from industrial_rag.db.models import (
    Base,
    CandidateAttemptReference,
    ClaimedExecutionContext,
    KnowledgeBase,
    UpdateJob,
    UpdateJobExecutionStatus,
    UpdateJobStatus,
    UpdateOperation,
)
from industrial_rag.db.session import reset_for_testing
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_legacy_lifecycle_values_are_unchanged() -> None:
    assert {value.value for value in UpdateJobStatus} == {
        "pending",
        "claimed",
        "running",
        "building",
        "validating",
        "ready",
        "succeeded",
        "failed",
        "cancelled",
        "recovery_required",
        "promoted",
        "rolled_back",
    }


@pytest.mark.parametrize("status", list(UpdateJobStatus))
def test_unclassified_legacy_jobs_remain_nullable(status: UpdateJobStatus) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(KnowledgeBase(id="kb", name="contract"))
            session.add(
                UpdateJob(
                    id="job", knowledge_base_id="kb", operation=UpdateOperation.add, status=status
                )
            )
            session.commit()
            row = session.get(UpdateJob, "job")
            assert row.status is status
            assert row.execution_status is None
            assert row.next_run_at is None
            assert row.cancel_requested_at is None
            assert row.execution_finished_at is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("lifecycle", "execution"),
    [
        ("pending", "PENDING"),
        ("building", "RUNNING"),
        ("building", "SUCCEEDED"),
        ("recovery_required", "RECOVERY_REQUIRED"),
        ("failed", "FAILED"),
        ("failed", "SUCCEEDED"),
        ("cancelled", "CANCELLED"),
        ("cancelled", "SUCCEEDED"),
        ("ready", "SUCCEEDED"),
        ("promoted", "SUCCEEDED"),
    ],
)
def test_execution_values_round_trip_without_replacing_lifecycle(
    lifecycle: str,
    execution: str,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(KnowledgeBase(id="kb", name="contract"))
            session.add(
                UpdateJob(
                    id="job",
                    knowledge_base_id="kb",
                    operation=UpdateOperation.add,
                    status=UpdateJobStatus(lifecycle),
                    execution_status=UpdateJobExecutionStatus(execution),
                )
            )
            session.commit()
            session.expire_all()
            row = session.get(UpdateJob, "job")
            assert row.status.value == lifecycle
            assert row.execution_status.value == execution
            assert (
                session.execute(text("SELECT execution_status FROM update_jobs")).scalar_one()
                == execution
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("lifecycle", "execution"),
    [("promoted", "PENDING"), ("ready", "RUNNING"), ("pending", "BOGUS")],
)
def test_database_rejects_invalid_execution_pairs(lifecycle: str, execution: str) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(KnowledgeBase(id="kb", name="contract"))
            session.add(UpdateJob(id="job", knowledge_base_id="kb", operation=UpdateOperation.add))
            session.commit()
            with pytest.raises(IntegrityError):
                session.execute(
                    text("UPDATE update_jobs SET status=:status, execution_status=:execution"),
                    {"status": lifecycle, "execution": execution},
                )
                session.commit()
            session.rollback()
    finally:
        engine.dispose()


def _context() -> ClaimedExecutionContext:
    return ClaimedExecutionContext(
        job_id="job",
        knowledge_base_id="kb",
        attempt=1,
        worker_id="worker",
        lease_token="secret-token",
        fencing_token=7,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )


def test_ownership_context_is_immutable_and_hides_lease_secret() -> None:
    context = _context()
    assert context.candidate_reference is None
    assert "secret-token" not in repr(context)
    with pytest.raises(FrozenInstanceError):
        context.attempt = 2


def test_candidate_binding_cannot_cross_attempt_or_kb() -> None:
    reference = CandidateAttemptReference(
        job_id="job",
        knowledge_base_id="kb",
        attempt=1,
        candidate_generation_id="candidate",
    )
    context = replace(_context(), candidate_reference=reference)
    assert context.candidate_reference == reference
    for mismatched in (
        replace(reference, attempt=2),
        replace(reference, job_id="other"),
        replace(reference, knowledge_base_id="other"),
    ):
        with pytest.raises(ValueError, match="candidate"):
            replace(context, candidate_reference=mismatched)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": 0},
        {"fencing_token": 0},
        {"lease_token": ""},
        {"worker_id": " "},
        {"lease_expires_at": datetime(2026, 9, 7)},
    ],
)
def test_ownership_context_rejects_incomplete_claim(changes: dict) -> None:
    with pytest.raises(ValueError):
        replace(_context(), **changes)


def test_attempt_staging_paths_are_isolated() -> None:
    from industrial_rag.storage_layout import kb_parsed_attempt_staging_dir

    first = kb_parsed_attempt_staging_dir("a" * 32, "b" * 32, 1, "c" * 32)
    second = kb_parsed_attempt_staging_dir("a" * 32, "b" * 32, 2, "c" * 32)
    assert first != second
    assert first.parts[-5:] == ("attempts", "b" * 32, "1", "c" * 32, "staging")
    assert second.parts[-5:] == ("attempts", "b" * 32, "2", "c" * 32, "staging")


@pytest.mark.skip(reason="C1 contract only: single-claim service integration is not enabled")
def test_claimed_context_is_consumed_without_second_claim_skeleton() -> None:
    """Future boundary check: IncrementalUpdateService consumes an existing lease."""


def test_active_migration_adds_execution_contract_without_classifying_legacy_rows(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "phase15c-c1b.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    reset_for_testing()
    config = Config("alembic.ini")
    command.upgrade(config, "f15b0a1c2d3e")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO update_jobs (id, knowledge_base_id, operation, status,
                retry_count, created_by, created_at, updated_at)
                VALUES ('job', 'kb', 'add', 'ready', 0, 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""
            )
        )
    command.upgrade(config, "head")
    columns = {column["name"] for column in inspect(engine).get_columns("update_jobs")}
    assert {"execution_status", "next_run_at", "cancel_requested_at", "execution_finished_at"} <= columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT execution_status FROM update_jobs WHERE id='job'")).scalar_one() is None
    engine.dispose()
    reset_for_testing()
