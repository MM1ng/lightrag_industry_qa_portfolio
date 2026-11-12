# Phase15-A Document Lifecycle Architecture Alignment Review

**Scope:** read-only architecture review and implementation plan. No business code is changed and no commit is created.

## Decision

Phase15 must adopt one document-change control plane. `upload`, `replace`, `delete`, `reparse`, and `reindex` must each persist exactly one `UpdateJob` and follow:

```text
Request -> UpdateJob -> IncrementalUpdateService worker -> Candidate Generation
        -> Validation -> Ready -> fenced, atomic Promote
```

The active generation is the only serving truth. No document operation may mutate active retrieval state, active-document projection, or runtime cache before a candidate is validated and promoted.

## Current model assessment

| Model | Responsibility today | Relationship | Finding |
| --- | --- | --- | --- |
| `KnowledgeBase` | KB settings, workspace/count summaries, active-generation pointer and epoch. | Owns documents/tasks/generations; references active generation. | Correct aggregate root, but two pipelines write its serving state. |
| `Document` | Source-file metadata, document version, parse/chunk status and counts. | Belongs to KB; referenced by `LifecycleTask` and `UpdateJob`. | `is_active` and `status` conflate source-version retention with membership in one published generation. |
| `UpdateJob` | Add/replace/delete command audit, base/candidate linkage, trace, retry and lease state. | References KB, changed document, and base/candidate generation. | Correct production command aggregate, but enum excludes `reparse`/`reindex`; the API runs its build synchronously. |
| `VectorIndexGeneration` | Immutable workspace, collection, manifest/config hashes, lifecycle and retention. | Belongs to KB; referenced by active pointer and jobs. | Strong serving artifact; `created_by_task_id` exposes legacy ownership. |
| `LifecycleTask` | Legacy parse/index/rebuild/delete queue. | References KB and optional document. | A second state machine with its own executor and direct publication capability. |

## Current backend flows

| Operation | Router | Service/repository chain | Generation result |
| --- | --- | --- | --- |
| Upload | `routers/documents.py::upload_document` | `IncrementalUpdateService.add_document` -> `DocumentRepository.create` -> `UpdateJobRepository.create` -> `_execute_persisted_job` | Candidate is built immediately; separate generation API validates then promotes. |
| Replace | `routers/documents.py::replace_document` | `IncrementalUpdateService.replace_document` -> new `Document` version -> `UpdateJobRepository.create` -> `_execute_persisted_job` | Same candidate/validation/promotion path; old document is removed in candidate. |
| Delete | `routers/documents.py::delete_document` | `IncrementalUpdateService.delete_document` -> `UpdateJobRepository.create` -> `_execute_persisted_job` | Same candidate/validation/promotion path; source row is projected deleted only at promotion. |
| Reparse | `routers/documents.py::reparse_document` | `DocumentService.request_reparse` -> `TaskRepository.create(reparse)` -> `LifecycleTaskExecutor` -> `handle_reparse` -> `ParseService` -> `IngestionPipeline` -> `TaskRepository.create(rebuild)` -> `IndexService` | No `UpdateJob`; parse changes the mutable parsed `current` directory, then full rebuild directly activates. |
| Reindex | `routers/documents.py::reindex_document` | `DocumentService.request_reindex` -> `TaskRepository.create(rebuild)` -> `LifecycleTaskExecutor` -> `handle_rebuild` -> `IndexService` | No `UpdateJob`; `IndexService` calls `VectorIndexGenerationRepository.activate` directly. |

## Pipeline-consistency verdict

Upload, replace, and delete reach the intended candidate path but build on the request thread rather than a continuously claimed worker. Reparse and reindex do **not** reach the intended path. They use the legacy lifecycle pipeline:

```text
DocumentService -> LifecycleTask -> LifecycleTaskExecutor -> ParseService/IndexService
                                                        -> direct generation activation
```

The bypass is implemented in:

- `src/industrial_rag/routers/documents.py` — reparse/reindex select `DocumentService`, unlike upload/replace/delete.
- `src/industrial_rag/services/document_service.py` — creates `LifecycleTask`, and reindex is queued as `TaskType.rebuild`.
- `src/industrial_rag/services/handler_impls.py` — reparse queues a rebuild; rebuild delegates to `IndexService`.
- `src/industrial_rag/services/index_service.py` — calls `VectorIndexGenerationRepository.activate` and updates KB/document serving state.
- `src/industrial_rag/services/lifecycle_task_executor.py` — uses only process-local per-KB locks, unlike durable fenced job leases.
- `src/industrial_rag/services/parse_service.py` — atomically replaces the document's mutable parsed `current` directory before candidate validation.

Risks: failed or low-quality reparse/reindex can replace serving retrieval artifacts; canonical validation and validation fingerprints are bypassed; multi-instance fencing and recovery semantics diverge; rollback cannot identify an immutable parse revision; operations have split audit/status APIs; and concurrent legacy task/job work can race over document or KB projections.

## Target architecture

### Domain boundaries

1. `KnowledgeBase` owns settings and the active-generation pointer only; counts are derived projection data.
2. A logical document owns immutable source versions. A source version is retained independently of its published membership.
3. Each parse produces an immutable parse-artifact revision pinned to source hash and parser/chunker configuration.
4. A generation manifest records the exact source-version and parse-revision identities it serves. The manifest—not `Document.is_active`—is authoritative for historical and active membership.
5. `UpdateJob` is the sole operation command/audit model. Extend `UpdateOperation` to `add`, `replace`, `delete`, `reparse`, and `reindex`, with immutable operation input and an idempotency key.
6. `LifecycleTask` becomes an execution adapter during migration and has no active-generation publication authority.

### Unified operation behavior

| Operation | Candidate delta | Serving change |
| --- | --- | --- |
| Upload | Persist new source version, parse it, add parse revision to base manifest. | Visible only after promote. |
| Replace | Persist new version of logical document, parse it, substitute manifest membership. | Old version remains serving until promote and retained for rollback. |
| Delete | Omit target from candidate manifest without deleting source. | Stops serving only after promote; physical cleanup waits for GC. |
| Reparse | Generate new parse revision from same source under pinned parser/chunker settings. | New extraction/chunks visible only after promote. |
| Reindex | Rebuild retrieval artifacts from base manifest and pinned index/runtime configuration. | New index visible only after promote. |

### Invariants

- Router requests persist commands and return `202` with `job_id`; they never build/activate in-process.
- The worker claims/retries/checkpoints every job through `KBLeaseService` fencing.
- Candidate workspaces/collections are isolated. Active pointers, runtime cache, and current-document projection change only at promote.
- Every candidate requires current validation evidence bound to manifest, content epoch, policy, and index configuration; no force/bypass promote exists.
- Promotion is the only path that can switch `KnowledgeBase.active_vector_generation_id` and update serving projections. It is atomic and idempotent.
- Rollback selects a retained validated generation; it never re-parses or re-indexes.

## Implementation plan

### 1. Establish the unified persistence contract

Modify `db/models.py`, document/update-job repositories, and add an Alembic migration. Add operation values, immutable operation input, idempotency, source-version/parse-revision identity, and generation-manifest bindings. Test a duplicate idempotency key returns one job for all five operations. Verify upgrade/downgrade preserves existing documents, generations, validation evidence, and jobs.

### 2. Create one command facade and redirect all document routers

Add `services/document_operation_service.py`; have all five mutation routes in `routers/documents.py` call it. The facade validates input, stages source bytes safely, persists the source/version and `UpdateJob` atomically, and returns a pending job summary. Remove synchronous `_execute_persisted_job` ownership from request handlers. Add API tests requiring `202`, one `job_id`, and no active-pointer change for each operation.

### 3. Version parse artifacts and build candidates only from persisted inputs

Change `parse_service.py`, `generation_artifacts.py`, and `incremental_update_service.py` so reparse writes a revisioned artifact, rather than changing `parsed/.../current` before validation. Candidate construction resolves unchanged artifacts from the base-generation manifest and only the operation delta from the persisted job. Test that a failed reparse leaves active query results and active parse artifact unchanged; test promote and rollback resolve frozen revisions.

### 4. Make the durable worker execute build and validation

Extend `update_job_worker.py` and startup wiring in `api.py` so a worker claims pending/recovery jobs, creates candidates, checkpoints build/validation, and marks them ready or failed through fenced writes. `GenerationValidationService` remains the gate; promotion remains explicit admin action. Test crash recovery, stale fencing rejection, and validation-required promotion for all operation types.

### 5. Remove legacy direct publication authority

Refactor `IndexService` into a candidate builder that cannot call `VectorIndexGenerationRepository.activate` or update active KB/document state. Redirect/retire reparse/reindex handlers in `handler_impls.py`; constrain `LifecycleTaskExecutor` to transitional scheduling only. Add a regression test that monkeypatches `activate` to fail during every document operation and proves the only successful activation is centralized promotion.

### 6. Reconcile historical state and release safely

Add a read-only lifecycle audit and a Phase15 runbook. It must report legacy active generations without jobs, documents absent from active manifests, orphan parse artifacts, and stale lifecycle tasks; it must not auto-delete or auto-promote. Run migration rehearsal, read-only audit, baseline seeding under operator approval, a canary KB cycle for all five operations, validation/promotion evidence capture, and rollback rehearsal.

## Verification matrix

| Gate | Required evidence |
| --- | --- |
| Isolation | Active query results and active pointer stay unchanged for build/parse/index/validation failures. |
| Consistency | Every five-operation request has exactly one `UpdateJob`, one candidate, and generation-manifest traceability. |
| Validation | Promote returns conflict without a fresh successful validation run. |
| Concurrency | Durable KB lease/fencing prevents stale or parallel writers from changing candidate/active state. |
| Recovery | Restart resumes from persisted command and reconstructs only a candidate. |
| Rollback | A retained validated generation restores prior source/parse/index membership without reprocessing. |
| No bypass | No production document path directly invokes generation activation or updates active pointer outside promotion. |

## Review conclusion

The current Phase 9/9B path already contains the correct production primitives—isolated candidates, immutable generation artifacts, validation evidence, fenced leases, atomic promotion, and rollback. Phase15 should preserve those primitives and make reparse/reindex first-class `UpdateJob` operations. The legacy lifecycle pipeline is the sole architectural misalignment and should lose publication authority in a staged migration.
