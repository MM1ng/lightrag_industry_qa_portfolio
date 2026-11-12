"""Non-bypassable Promote eligibility checks for canonical validation evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from industrial_rag.config import Settings
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.repositories.validation_run_repository import ValidationRunRepository
from industrial_rag.services.generation_artifacts import GenerationArtifactError
from industrial_rag.services.generation_content_fingerprint import (
    GenerationContentFingerprintService,
    stable_hash,
)
from industrial_rag.services.golden_set_policy import RUNNER_VERSION


class ValidationGateService:
    def __init__(self, session, *, settings: Settings, qdrant_client_factory) -> None:
        self._session = session
        self._runs = ValidationRunRepository(session)
        self._fingerprints = GenerationContentFingerprintService(
            session,
            settings=settings,
            qdrant_client_factory=qdrant_client_factory,
        )
        self._settings = settings

    async def require_eligible(self, kb_id: str, generation):
        from industrial_rag.services.canonical_validation_runner import CanonicalValidationRunner
        from industrial_rag.services.validation_policy import (
            REQUIRED_GATES,
            resolve_validation_policy,
        )

        anchor = generation.accepted_policy_snapshot
        if not anchor:
            raise AppError(
                AppErrorCode.generation_validation_required,
                "G1 accepted policy is missing; revalidation required",
                status_code=409,
            )
        try:
            resolved = await resolve_validation_policy(
                self._session,
                kb_id,
                generation,
                suite_id=anchor.get("suite_id") if anchor.get("profile") == "bootstrap" else None,
                suite_sha256=anchor.get("suite_sha256")
                if anchor.get("profile") == "bootstrap"
                else None,
            )
        except (AppError, GenerationArtifactError):
            self._stale("validation policy or base Active no longer matches")
        if not resolved.admitted or resolved.fingerprint != stable_hash(anchor):
            self._stale("validation policy or approval no longer matches")
        policy = resolved.policy
        now = datetime.now(UTC)
        run = await self._runs.latest_eligible(
            generation.id,
            golden_set_version=policy.version,
            golden_set_sha256=policy.source_sha256,
            now=now,
            validation_policy_fingerprint=resolved.fingerprint,
        )
        if run is None:
            raise AppError(
                AppErrorCode.generation_validation_required,
                "Generation 没有当前有效的 canonical validation run。",
                status_code=409,
            )
        artifact = Path(run.result_artifact_path or "")
        if (
            not artifact.is_file()
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != run.result_artifact_sha256
        ):
            self._stale("validation artifact missing or changed")
        try:
            rows = [
                json.loads(line)
                for line in artifact.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            header, results = rows[0], rows[1:]
            summary = header["summary"]
            gates = summary["gates"]
            scored = CanonicalValidationRunner(self._settings, policy)._summarize(results)
            if (
                header.get("schema") != "g1-validation-v1"
                or summary.get("passed") is not True
                or summary.get("outcome") != "PASSED"
                or summary.get("policy_snapshot") != resolved.snapshot
                or not gates.keys() >= REQUIRED_GATES
                or any(
                    g.get("status") != "PASS" or g.get("passed") is not True for g in gates.values()
                )
                or not scored["golden_subset_regression"]
                or scored["http_success_rate"] != 1.0
                or scored["trace_complete_rate"] != 1.0
                or scored["negative_unsupported_answer_rate"] != 0.0
                or not scored["no_5xx"]
                or scored["fabricated_citation"]
                or scored["secret_leak"]
                or scored["old_document_references"]
                or any(r.get("actual_generation_id") != generation.id for r in results)
                or run.policy_snapshot != resolved.snapshot
                or run.effective_questions_sha256 != resolved.snapshot["effective_questions_sha256"]
            ):
                self._stale("validation artifact does not contain complete passing G1 evidence")
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            self._stale("validation artifact is malformed")
        try:
            current = await self._fingerprints.calculate(kb_id, generation)
        except GenerationArtifactError:
            self._stale("frozen generation snapshot is missing or changed")
        expected = {
            "validation_policy": resolved.fingerprint,
            "app_git_commit": current.app_git_commit,
            "strategy": current.strategy_fingerprint,
            "generation_manifest": current.generation_manifest_hash,
            "qdrant_content": current.qdrant_content_fingerprint,
            "document_registry": current.document_registry_fingerprint,
            "generation_content_epoch": current.generation_content_epoch,
            "qdrant_point_count": current.qdrant_point_count,
        }
        comparisons = {
            "runner": run.runner_version == RUNNER_VERSION,
            "app_git_commit": run.app_git_commit == current.app_git_commit,
            "model": run.configured_model == self._settings.llm_model,
            "strategy": run.strategy_fingerprint == current.strategy_fingerprint,
            "manifest": run.generation_manifest_hash == current.generation_manifest_hash,
            "qdrant": run.qdrant_content_fingerprint == current.qdrant_content_fingerprint,
            "documents": run.document_registry_fingerprint == current.document_registry_fingerprint,
            "content_epoch": run.generation_content_epoch == current.generation_content_epoch,
            "validated_fingerprint": generation.validated_fingerprint == stable_hash(expected),
        }
        if not all(comparisons.values()):
            failed = ",".join(name for name, passed in comparisons.items() if not passed)
            self._stale(f"validation evidence no longer matches: {failed}")
        return run

    @staticmethod
    def _stale(message: str) -> None:
        raise AppError(
            AppErrorCode.generation_validation_stale,
            message,
            status_code=409,
        )
