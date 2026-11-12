# Phase15 Document Lifecycle Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every document operation through a durable UpdateJob, isolated candidate generation, validation gate, and atomic promotion.

**Architecture:** `UpdateJob` becomes the sole document-change command and audit aggregate. `VectorIndexGeneration` remains the immutable serving artifact, while source versions and parse revisions are candidate inputs. Promotion is the only active-generation writer.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async/Alembic, Qdrant, LightRAG, pytest, Ruff.

**Spec:** `docs/phase-15a-document-lifecycle-architecture-review.md`

## Global Constraints

- Preserve active generation availability until candidate validation and promote succeed.
- Do not add a validation bypass or force promotion.
- Use durable `KBLeaseService` fencing for every candidate and promotion writer.
- Retain source versions and rollback-qualified generations; physical removal stays in explicit GC.
- Maintain API compatibility during the migration and do not commit code automatically.

---

### Task 1: Extend the persisted operation contract

**Files:**
- Modify: `src/industrial_rag/db/models.py`
- Modify: `src/industrial_rag/repositories/document_repository.py`
- Modify: `src/industrial_rag/repositories/update_job_repository.py`
- Create: `migrations/<revision>_phase15_document_lifecycle.py`
- Test: `tests/test_phase15_document_lifecycle.py`

**Interfaces:**
- Produces `UpdateOperation.add|replace|delete|reparse|reindex` and a persisted operation input/idempotency contract.

- [ ] Write a failing test that submits the same reparse idempotency key twice and asserts one job, operation `reparse`, and identical operation input.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py::test_reparse_idempotency -v` and confirm it fails because `reparse` has no UpdateJob contract.
- [ ] Add enum values, immutable JSON operation input, KB-scoped idempotency, source-version/parse-revision identities, and generation-manifest bindings; backfill old jobs without deleting records.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py -v; alembic upgrade head; alembic downgrade -1; alembic upgrade head` and require all pass.

### Task 2: Centralize router command acceptance

**Files:**
- Create: `src/industrial_rag/services/document_operation_service.py`
- Modify: `src/industrial_rag/routers/documents.py`
- Modify: `src/industrial_rag/services/incremental_update_service.py`
- Test: `tests/test_phase15_document_lifecycle.py`

**Interfaces:**
- Consumes `request_operation(kb_id, operation, operation_input, idempotency_key, actor, request_id, trace_id)`.
- Produces a `202` response containing one pending `job_id`; no request handler owns candidate build work.

- [ ] Write parameterized API tests for upload, replace, delete, reparse, and reindex; each must return pending `job_id` and leave active generation unchanged.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py::test_all_routes_accept_one_pending_update_job -v` and confirm current reparse/reindex return legacy `task_id` while other operations build synchronously.
- [ ] Implement the facade, stage file data before persistence safely, and delegate all five document routes to it.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py -v` and require all five routes to pass the same contract.

### Task 3: Make parsing a revisioned candidate input

**Files:**
- Modify: `src/industrial_rag/services/parse_service.py`
- Modify: `src/industrial_rag/services/generation_artifacts.py`
- Modify: `src/industrial_rag/services/incremental_update_service.py`
- Test: `tests/test_phase15_document_lifecycle.py`

**Interfaces:**
- Produces a parse revision keyed by source hash plus parser/chunker configuration and bound into candidate manifests.

- [ ] Write a failing reparse test asserting active parsed artifacts and active query answers stay unchanged before candidate promotion.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py::test_reparse_isolated_until_promote -v` and confirm mutable `parsed/current` causes failure.
- [ ] Write parse results under an immutable revision path; construct each candidate from base-manifest revisions plus its persisted operation delta.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py -k "reparse or rollback or manifest" -v` and require promote/rollback to resolve frozen artifacts.

### Task 4: Execute all operations via the fenced UpdateJob worker

**Files:**
- Modify: `src/industrial_rag/services/update_job_worker.py`
- Modify: `src/industrial_rag/services/incremental_update_service.py`
- Modify: `src/industrial_rag/services/generation_validation_service.py`
- Modify: `src/industrial_rag/api.py`
- Test: `tests/test_phase15_operation_recovery.py`

**Interfaces:**
- Produces `building -> validating -> ready|failed`; promotion stays an explicit admin operation.

- [ ] Write a failing test for reindex recovery: build is resumed from the persisted job, active is unchanged, and promotion fails until validation succeeds.
- [ ] Run `pytest tests/test_phase15_operation_recovery.py::test_reindex_requires_validation_after_worker_recovery -v` and confirm legacy rebuild directly activates.
- [ ] Make the worker claim, checkpoint, build, validate, and finalize all operations with `KBLeaseService` fencing; recovery must reconstruct only a candidate from persisted input.
- [ ] Run `pytest tests/test_phase15_operation_recovery.py tests/test_phase9b_job_recovery.py tests/test_phase9b_leases.py -v` and require all pass.

### Task 5: Remove the direct-activation bypass

**Files:**
- Modify: `src/industrial_rag/services/index_service.py`
- Modify: `src/industrial_rag/services/handler_impls.py`
- Modify: `src/industrial_rag/services/lifecycle_task_executor.py`
- Test: `tests/test_phase15_legacy_task_boundary.py`

**Interfaces:**
- `IndexService.build_candidate(job_id, base_generation_id)` produces artifacts and metrics only; it never calls `activate` or writes active KB state.

- [ ] Write a failing test that invokes legacy reparse/rebuild and asserts it cannot activate a generation without validation/promotion.
- [ ] Run `pytest tests/test_phase15_legacy_task_boundary.py -v` and confirm direct `VectorIndexGenerationRepository.activate` is reachable.
- [ ] Restrict `IndexService` to candidate construction and bridge or retire document-related legacy handlers; retain unrelated KB deletion/backend migration tasks outside this scope.
- [ ] Run `pytest tests/test_phase15_legacy_task_boundary.py tests/test_phase9.py tests/test_phase9b_validation_gate.py tests/test_phase9b_multi_instance.py -v; ruff check src tests` and require all pass.

### Task 6: Reconcile retained history and release by evidence

**Files:**
- Create: `scripts/audit_document_lifecycle_alignment.py`
- Create: `docs/phase-15-document-lifecycle-runbook.md`
- Test: `tests/test_phase15_document_lifecycle.py`

**Interfaces:**
- Produces a read-only report for legacy generations without jobs, document/manifest mismatches, orphan parsed artifacts, and stale lifecycle tasks.

- [ ] Write a fixture test expecting `LEGACY_GENERATION_NO_UPDATE_JOB` and `auto_remediation == false`.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py::test_lifecycle_audit_is_read_only -v` and confirm no audit exists.
- [ ] Implement the report and runbook: migration rehearsal, read-only audit, operator-approved baseline seeding, canary KB, validation/promote evidence, rollback rehearsal.
- [ ] Run `pytest tests/test_phase15_document_lifecycle.py tests/test_phase15_operation_recovery.py tests/test_phase15_legacy_task_boundary.py -v; ruff check src tests; python scripts/audit_document_lifecycle_alignment.py --help` and require all pass.

## Plan self-review

- Coverage: Tasks 1–2 unify commands; Task 3 isolates parse inputs; Task 4 supplies durable build/validation/recovery; Task 5 removes direct activation; Task 6 provides safe rollout evidence.
- No implementation occurs in this review and no commit is created.
