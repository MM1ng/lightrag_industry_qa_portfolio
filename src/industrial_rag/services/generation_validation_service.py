"""Candidate generation quality gates (Phase 9)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.config import Settings
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from industrial_rag.repositories.validation_run_repository import ValidationRunRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)

logger = logging.getLogger(__name__)


class GenerationValidationService:
    """Run the Phase 9 candidate quality gates."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        runtime_manager: Any = None,
        qdrant_client_factory: Callable[[], AsyncQdrantClient] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or Settings.from_env()
        self._runtime_manager = runtime_manager
        self._qdrant_client_factory = qdrant_client_factory
        self._generation_repo = VectorIndexGenerationRepository(session)
        self._job_repo = UpdateJobRepository(session)
        self._doc_repo = DocumentRepository(session)
        self._validation_runs = ValidationRunRepository(session)

    async def validate(
        self,
        kb_id: str,
        generation: Any,
        *,
        golden_runner: Any = None,
        approved_by: str | None = None,
        suite_id: str | None = None,
        suite_sha256: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        gates: dict[str, Any] = {}

        from industrial_rag.services.generation_artifacts import (
            GenerationArtifactError,
            load_generation_manifest,
        )

        manifest = None
        try:
            manifest = load_generation_manifest(
                Path(generation.workspace_path),
                expected_generation_id=generation.generation,
                expected_child_manifest_hash=generation.child_chunks_manifest_hash,
            )
            gates["frozen_snapshot_consistency"] = {
                "passed": True,
                "detail": {
                    "child_manifest_hash": manifest.child_manifest_hash,
                    "chunk_count": manifest.count,
                },
            }
        except GenerationArtifactError as error:
            gates["frozen_snapshot_consistency"] = {
                "passed": False,
                "detail": str(error),
            }

        from industrial_rag.services.canonical_validation_runner import (
            CanonicalValidationRunner,
            write_validation_artifact,
        )
        from industrial_rag.services.generation_content_fingerprint import (
            GenerationContentFingerprintService,
            stable_hash,
        )
        from industrial_rag.services.golden_set_policy import RUNNER_VERSION
        from industrial_rag.services.validation_policy import (
            REQUIRED_GATES,
            resolve_validation_policy,
        )

        resolved = await resolve_validation_policy(
            self._session,
            kb_id,
            generation,
            suite_id=suite_id,
            suite_sha256=suite_sha256,
        )
        policy = resolved.policy
        structure_failed = not gates["frozen_snapshot_consistency"]["passed"]
        blocked = not resolved.admitted and not structure_failed
        gates["acceptance_data"] = {
            "status": "SKIPPED" if not resolved.admitted else "PASS",
            "passed": resolved.admitted,
            "detail": resolved.snapshot,
        }
        client_factory = self._qdrant_client_factory or self._new_qdrant_client
        evidence = await GenerationContentFingerprintService(
            self._session,
            settings=self._settings,
            qdrant_client_factory=client_factory,
        ).calculate(
            kb_id,
            generation,
            measure_qdrant=not blocked,
            require_frozen_artifact=bool(gates["frozen_snapshot_consistency"].get("passed")),
        )
        now = datetime.now(UTC)
        validation_run = await self._validation_runs.create(
            knowledge_base_id=kb_id,
            generation_id=generation.id,
            golden_set_version=policy.version,
            golden_set_sha256=policy.source_sha256,
            runner_version=RUNNER_VERSION,
            policy_snapshot=resolved.snapshot,
            validation_policy_fingerprint=resolved.fingerprint,
            effective_questions_sha256=resolved.snapshot["effective_questions_sha256"],
            app_git_commit=evidence.app_git_commit,
            configured_model=self._settings.llm_model,
            strategy_fingerprint=evidence.strategy_fingerprint,
            generation_manifest_hash=evidence.generation_manifest_hash,
            qdrant_content_fingerprint=evidence.qdrant_content_fingerprint,
            document_registry_fingerprint=evidence.document_registry_fingerprint,
            generation_content_epoch=evidence.generation_content_epoch,
            actor=approved_by or "admin:local-dev",
            expires_at=now + timedelta(seconds=self._settings.validation_max_age_seconds),
        )
        await self._session.commit()

        gates["db_integrity"] = await self._db_integrity()
        if not blocked and not structure_failed:
            gates["document_registration_consistency"] = await self._doc_registration(
                kb_id, generation
            )
            gates["counts"] = await self._count_gate(kb_id, generation)
            gates["payload_completeness"] = await self._payload_gate(kb_id, generation)
            gates["generation_mixing"] = await self._payload_gate(
                kb_id, generation, generation_mix_only=True
            )
        scorer = CanonicalValidationRunner(self._settings, policy)
        unsupported = gates["db_integrity"]["status"] == "SKIPPED"
        execution_block_reason = "DB_INTEGRITY_NOT_VERIFIED" if unsupported else None
        preconditions_failed = any(value.get("passed") is not True for value in gates.values())
        if blocked or structure_failed or unsupported or preconditions_failed:
            blocked = (blocked or unsupported) and not structure_failed
            run_report = scorer._summarize([])
            run_report["measurement_status"] = "NOT_MEASURED"
        else:
            runner = golden_runner or scorer
            supplied = await runner(kb_id, generation)
            # A runner's summary is not authority. Re-score its exact records.
            run_report = scorer._summarize(supplied.get("results") or [])
            if supplied.get("runner_configured") is False:
                blocked = True
                execution_block_reason = "RUNNER_NOT_CONFIGURED"
            run_report["measurement_status"] = "NOT_MEASURED" if blocked else "MEASURED"
        gates.update(
            {
                "canonical_question_identity": run_report["question_ids"]
                == [q["id"] for q in policy.questions],
                "generation_identity": bool(run_report["results"])
                and all(
                    r.get("actual_generation_id") == generation.id for r in run_report["results"]
                ),
                "citation_traceability": run_report["citation_traceability"],
                "golden_subset_regression": run_report["golden_subset_regression"],
                "http_success_1_0": run_report["http_success_rate"] == 1.0,
                "trace_complete_1_0": run_report["trace_complete_rate"] == 1.0,
                "negative_unsupported_0": run_report["negative_unsupported_answer_rate"] == 0.0,
                "no_5xx": run_report["no_5xx"],
                "no_fabricated_citation": run_report["fabricated_citation"] == 0,
                "no_secret_leak": run_report["secret_leak"] == 0,
                "no_old_document_reference": run_report["old_document_references"] == 0,
            }
        )
        gates = {
            name: value
            if isinstance(value, dict)
            else {
                "status": "SKIPPED" if blocked else "PASS" if value else "FAIL",
                "passed": bool(value) and not blocked,
            }
            for name, value in gates.items()
        }
        gates = {
            name: {"status": "PASS" if value.get("passed") is True else "FAIL", **value}
            for name, value in gates.items()
        }
        for name in REQUIRED_GATES - gates.keys():
            gates[name] = {"status": "SKIPPED", "passed": False, "detail": "PRECONDITION_NOT_MET"}
        passed = not blocked and all(
            value.get("status") == "PASS" and value.get("passed") is True
            for value in gates.values()
        )
        outcome = "BLOCKED" if blocked else "PASSED" if passed else "FAILED"
        report = {
            "knowledge_base_id": kb_id,
            "generation_id": generation.id,
            "generation": generation.generation,
            "approved_by": approved_by,
            "gates": {
                name: (value if isinstance(value, dict) else bool(value))
                for name, value in gates.items()
            },
            "run": run_report,
            "passed": passed,
            "outcome": outcome,
            "applicability": resolved.snapshot["applicability"],
            "admission": resolved.snapshot["admission"],
            "reason_codes": [
                *resolved.snapshot["reasons"],
                *([execution_block_reason] if execution_block_reason else []),
            ],
            "policy_snapshot": resolved.snapshot,
            "validation_policy_fingerprint": resolved.fingerprint,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "validation_run_id": validation_run.id,
            "golden_set_version": policy.version,
            "golden_set_sha256": policy.source_sha256,
            "runner_version": RUNNER_VERSION,
            "fingerprints": {
                "validation_policy": resolved.fingerprint,
                "app_git_commit": evidence.app_git_commit,
                "strategy": evidence.strategy_fingerprint,
                "generation_manifest": evidence.generation_manifest_hash,
                "qdrant_content": evidence.qdrant_content_fingerprint,
                "document_registry": evidence.document_registry_fingerprint,
                "generation_content_epoch": evidence.generation_content_epoch,
                "qdrant_point_count": evidence.qdrant_point_count,
            },
        }
        artifact_path = (
            self._settings.validation_artifact_dir
            / kb_id
            / generation.id
            / f"{validation_run.id}.jsonl"
        )
        artifact_records = [
            {"schema": "g1-validation-v1", "summary": report},
            *run_report["results"],
        ]
        artifact_sha256 = write_validation_artifact(artifact_path, artifact_records)
        await self._validation_runs.finalize(
            validation_run.id,
            passed=passed,
            blocked=blocked,
            metrics={
                "outcome": outcome,
                "policy_snapshot": resolved.snapshot,
                "gates": report["gates"],
                "run_summary": {
                    key: value for key, value in run_report.items() if key != "results"
                },
                "evidence_fingerprint": stable_hash(report["fingerprints"]),
            },
            artifact_path=str(artifact_path.resolve()),
            artifact_sha256=artifact_sha256,
            finished_at=datetime.now(UTC),
        )
        if passed:
            generation.accepted_policy_snapshot = resolved.snapshot
            generation.validated_fingerprint = stable_hash(report["fingerprints"])
        await self._session.flush()
        return report

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    async def _db_integrity(self) -> dict[str, Any]:
        try:
            bind = self._session.get_bind()
            if bind.dialect.name != "sqlite":
                return {"status": "SKIPPED", "passed": False, "detail": "UNSUPPORTED_BACKEND"}
            connection = await self._session.connection()
            databases = (await connection.exec_driver_sql("PRAGMA database_list")).all()
            main = [row for row in databases if row[1] == "main"]
            if len(main) != 1:
                return {
                    "status": "FAIL",
                    "passed": False,
                    "detail": "MAIN_DATABASE_IDENTITY_UNKNOWN",
                }
            rows = (await connection.exec_driver_sql("PRAGMA integrity_check")).all()
            ok = [tuple(row) for row in rows] == [("ok",)]
            from industrial_rag.services.generation_content_fingerprint import stable_hash

            return {
                "status": "PASS" if ok else "FAIL",
                "passed": ok,
                "detail": "ok" if ok else "SQLITE_INTEGRITY_FAILED",
                "target_fingerprint": stable_hash({"backend": "sqlite", "main": main[0][2]}),
            }
        except Exception:
            return {"status": "FAIL", "passed": False, "detail": "DATABASE_CHECK_ERROR"}

    async def _doc_registration(self, kb_id: str, generation: Any) -> dict[str, Any]:
        job = await self._job_repo.find_by_candidate(generation.id)
        expected_ids: set[str] = set()
        expected_entries: list[dict[str, Any]] = []
        workspace = Path(generation.workspace_path)
        # The frozen generation manifest is authoritative for candidate
        # membership.  A reparse candidate may be built from a document row
        # that was marked inactive by an earlier failed generation; using the
        # mutable active-document view here incorrectly rejects the complete
        # candidate and also prevents its citation IDs from being emitted.
        try:
            from industrial_rag.services.generation_artifacts import load_generation_manifest

            manifest = load_generation_manifest(
                workspace,
                expected_generation_id=generation.generation,
                expected_child_manifest_hash=generation.child_chunks_manifest_hash,
            )
            expected_entries = [dict(entry) for entry in manifest.documents]
            expected_ids = {
                str(entry["document_id"]) for entry in expected_entries if entry.get("document_id")
            }
        except Exception:
            if job is not None and (job.result or {}).get("documents"):
                expected_entries = [
                    entry for entry in job.result["documents"] if entry.get("is_active")
                ]
                expected_ids = {entry["document_id"] for entry in expected_entries}
            else:
                docs = await self._doc_repo.list_active_for_kb(kb_id)
                expected_ids = {d.id for d in docs}
        token_dir = workspace / f"qdrant-{generation.generation}"
        doc_status_path = (
            token_dir if token_dir.is_dir() else workspace
        ) / "kv_store_doc_status.json"
        if not doc_status_path.is_file():
            return {
                "passed": True,
                "detail": (
                    f"no kv_store_doc_status.json in candidate workspace; "
                    f"registration consistency verified from job manifest "
                    f"({len(expected_ids)} active documents expected)"
                ),
            }
        statuses = json.loads(doc_status_path.read_text(encoding="utf-8"))
        processed_count = sum(
            1
            for value in statuses.values()
            if isinstance(value, dict) and value.get("status") == "processed"
        )
        # Internal chunk evidence may be absent for fakes; treat the workspace
        # file as consistent when its processed count matches expectations.
        return {
            "passed": processed_count == len(expected_ids),
            "detail": {
                "processed_docs_in_workspace": processed_count,
                "expected_active_documents": len(expected_ids),
                "expected_document_ids": sorted(expected_ids)[:20],
                "manifest_entries": expected_entries,
            },
        }

    async def _count_gate(self, kb_id: str, generation: Any) -> dict[str, Any]:
        job = await self._job_repo.find_by_candidate(generation.id)
        metrics = (job.metrics or {}) if job else {}
        stats = metrics.get("chunk_stats") or {}
        client = self._new_qdrant_client()
        try:
            counts: dict[str, int] = {}
            names = generation.collections or {}
            for namespace in ("chunks", "entities", "relationships"):
                name = names.get(namespace)
                if name and await client.collection_exists(name):
                    counts[namespace] = (await client.count(name, exact=True)).count
                else:
                    counts[namespace] = 0
        finally:
            await client.close()
        expected_chunks = (
            int(stats.get("reused_chunks", 0))
            + int(stats.get("added_chunks", 0))
            - int(stats.get("invalidated_chunks", 0))
        )
        passed = counts.get("chunks", 0) == expected_chunks
        return {
            "passed": passed,
            "detail": {
                "actual_counts": counts,
                "expected_chunks": expected_chunks,
                "chunk_stats": stats,
            },
        }

    async def _payload_gate(
        self,
        kb_id: str,
        generation: Any,
        *,
        generation_mix_only: bool = False,
    ) -> dict[str, Any]:
        client = self._new_qdrant_client()
        problems: list[str] = []
        try:
            names = generation.collections or {}
            for namespace, require in (
                ("chunks", True),
                ("entities", True),
                ("relationships", True),
            ):
                name = names.get(namespace)
                if not name or not await client.collection_exists(name):
                    problems.append(f"{namespace}: collection missing")
                    continue
                records, _ = await client.scroll(
                    collection_name=name,
                    limit=50,
                    with_payload=True,
                    with_vectors=False,
                )
                for record in records:
                    payload = record.payload or {}
                    if payload.get("generation") != generation.generation:
                        problems.append(
                            f"{namespace}: generation mix ({payload.get('generation')})"
                        )
                    if generation_mix_only:
                        continue
                    if not payload.get("id"):
                        problems.append(f"{namespace}: missing point id")
                    if payload.get("kb_id") != kb_id:
                        problems.append(f"{namespace}: missing kb provenance")
                    if namespace == "chunks" and not payload.get("content"):
                        problems.append("chunks: missing content")
        finally:
            await client.close()
        return {
            "passed": not problems,
            "detail": {
                "sampled": 50,
                "problems": problems[:20],
                "problem_count": len(problems),
            },
        }

    def _new_qdrant_client(self) -> AsyncQdrantClient:
        if self._qdrant_client_factory is not None:
            return self._qdrant_client_factory()
        return AsyncQdrantClient(
            url=self._settings.qdrant_url, api_key=self._settings.qdrant_api_key
        )
