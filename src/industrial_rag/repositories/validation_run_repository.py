"""Persistence operations for immutable canonical validation runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import ValidationRun, ValidationRunStatus


class ValidationRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: Any) -> ValidationRun:
        run = ValidationRun(status=ValidationRunStatus.running, **values)
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: str) -> ValidationRun | None:
        return await self._session.get(ValidationRun, run_id)

    async def finalize(
        self,
        run_id: str,
        *,
        passed: bool,
        metrics: dict[str, Any],
        artifact_path: str,
        artifact_sha256: str,
        finished_at: datetime,
        blocked: bool = False,
    ) -> ValidationRun | None:
        run = await self.get(run_id)
        if run is None or run.status is not ValidationRunStatus.running:
            return None
        if passed and blocked:
            raise ValueError("blocked validation cannot pass")
        from industrial_rag.services.generation_content_fingerprint import stable_hash
        from industrial_rag.services.golden_set_policy import RUNNER_VERSION

        if passed and run.runner_version == RUNNER_VERSION:
            snapshot = run.policy_snapshot or {}
            if (
                snapshot.get("admission") != "ADMITTED"
                or len(snapshot.get("questions", [])) != 20
                or run.validation_policy_fingerprint != stable_hash(snapshot)
                or run.effective_questions_sha256 != stable_hash(snapshot.get("questions"))
                or not run.qdrant_content_fingerprint
            ):
                raise ValueError("passing G1 run requires complete policy and measured content evidence")
        run.status = (
            ValidationRunStatus.blocked
            if blocked
            else ValidationRunStatus.passed
            if passed
            else ValidationRunStatus.failed
        )
        run.passed = passed
        run.metrics = metrics
        run.result_artifact_path = artifact_path
        run.result_artifact_sha256 = artifact_sha256
        run.finished_at = finished_at
        await self._session.flush()
        return run

    async def latest_eligible(
        self,
        generation_id: str,
        *,
        golden_set_version: str,
        golden_set_sha256: str,
        now: datetime,
        validation_policy_fingerprint: str | None = None,
    ) -> ValidationRun | None:
        statement = (
            select(ValidationRun)
            .where(
                ValidationRun.generation_id == generation_id,
                ValidationRun.status == ValidationRunStatus.passed,
                ValidationRun.passed.is_(True),
                ValidationRun.golden_set_version == golden_set_version,
                ValidationRun.golden_set_sha256 == golden_set_sha256,
                ValidationRun.expires_at > now,
            )
            .order_by(ValidationRun.finished_at.desc())
            .limit(1)
        )
        if validation_policy_fingerprint is not None:
            statement = statement.where(
                ValidationRun.validation_policy_fingerprint == validation_policy_fingerprint
            )
        result = await self._session.execute(statement)
        return result.scalars().first()
