# Phase15-B Unified Document Lifecycle Pipeline — Final Acceptance Report

**Acceptance date:** 2026-09-04  
**Scope:** Phase15-B Steps 1–5, final acceptance only.  
**Commit range:** `2ec75af` through `ea699e0`.

## 1. Executive Summary

Phase15-B aligns document lifecycle operations with the durable `UpdateJob`
pipeline. The five supported operations — `add`, `replace`, `delete`,
`reparse`, and `reindex` — build an isolated candidate generation and retain
the serving generation until canonical validation succeeds and the explicit
Promote operation changes the active pointer.

`reparse` and `reindex` are now persisted `UpdateJob` operations. Legacy
`LifecycleTask` document handlers act as compatibility adapters: they create
and execute an `UpdateJob`, but do not activate or promote a generation.
This acceptance step adds no product features and does not change production
code.

## 2. Before/After Architecture

| Concern | Before Phase15-B | After Phase15-B |
| --- | --- | --- |
| Reparse/reindex contract | Not represented as durable `UpdateJob` operations | Persisted `UpdateOperation.reparse` and `UpdateOperation.reindex` with repository create/query/recovery support |
| Document lifecycle entry | Legacy `LifecycleTask` paths could reach parse/index behavior directly | Lifecycle handlers converge to an `UpdateJob` and candidate build |
| Candidate isolation | Reparse/reindex were not covered by the common candidate contract | Both create a distinct `VectorIndexGeneration`; Active remains unchanged during build and validation |
| Publication authority | Legacy document paths could be confused with indexing/publishing work | Document publication is gated by Validation and `promote_generation`; `IndexService` requires an explicit backend-migration target |
| Concurrency safety | Existing Phase9 lease protections | Reparse/reindex are covered by the same fenced candidate and Promote path |

## 3. Lifecycle Flow

All document operations use the following safety boundary:

```text
add | replace | delete | reparse | reindex
                    |
                    v
              UpdateJob (durable intent)
                    |
                    v
       IncrementalUpdateService.execute_job
                    |
                    v
   Candidate VectorIndexGeneration (isolated)
                    |
                    v
       GenerationValidationService / Validation Gate
                    |
                    v
      promote_generation + KBLeaseService fencing
                    |
                    v
          KnowledgeBase.active_vector_generation_id
```

`execute_job` builds a candidate only. It neither validates nor promotes. A
candidate cannot be promoted while it is still `building`; a `ready` candidate
must also have current canonical validation evidence. The active-generation
pointer changes through the fenced Promote transition.

Operation-specific scope remains deliberate:

- `reparse` requires `document_id` and rebuilds candidate parsing/chunk
  artifacts for that document.
- `reindex` uses the current KnowledgeBase active-document snapshot and does
  not change parser, chunking, or embedding configuration.

## 4. Changed Components

| Component | Phase15-B responsibility |
| --- | --- |
| `UpdateOperation` / migration | Added backward-compatible `reparse` and `reindex` values and a reparse document-id constraint |
| `UpdateJobRepository` | Persists, queries, and recovers the new operation values |
| `DocumentService` and lifecycle handlers | Preserve compatibility while creating/executing UpdateJobs for reparse/reindex |
| `IncrementalUpdateService` | Extends the existing service with an execution entry that builds candidates; it was not rewritten |
| `IndexService` | Reserves direct indexing for explicit vector-backend migration rather than document lifecycle publication |
| Phase15-B tests | Cover operation persistence, handler convergence, candidate isolation, validation, Promote, and fencing |

## 5. Security Guarantees

- **No unvalidated publish:** a candidate in `building` state is rejected by
  Promote. A `ready` candidate must pass `ValidationGateService.require_eligible`.
- **Failure isolation:** a failed validation marks the candidate and its job
  failed without changing the active-generation pointer.
- **Evidence binding:** Promote rechecks canonical validation evidence against
  frozen generation artifacts, document registry, Qdrant content, strategy,
  and content epoch.
- **Atomic publication:** `KBLeaseService.switch_active_generation` guards the
  pointer compare-and-set, generation state update, and generation epoch with
  the current lease and fencing token.
- **Stale-writer rejection:** an expired reindex lease cannot restore an older
  Active Generation after a newer promote.
- **Compatibility without authority:** `LifecycleTask` remains available for
  creation, status, and recovery compatibility but has no document publication
  authority.

## 6. Test Evidence

All commands below were run in the Phase15-B worktree on 2026-09-04.

| Command / suite | Result | Evidence covered |
| --- | --- | --- |
| `pytest tests/test_phase15b_unified_document_lifecycle.py -v` | 26 passed | UpdateJob contract, legacy handler convergence, reparse/reindex candidate construction, validation gating, Promote, and stale-lease fencing |
| `pytest tests/test_phase9.py -v` | 24 passed | Add/replace/delete candidate lifecycle, validation failure isolation, Promote, rollback, restart recovery, snapshot integrity, and concurrency |
| `pytest tests/test_phase9b_validation_gate.py -v` | 8 passed | Canonical validation evidence, artifact/Qdrant tamper detection, and required validation |
| `pytest tests/test_phase9b_job_recovery.py -v` | 4 passed | Atomic claims, stale-worker rejection, recovery, and KB lease binding |
| `pytest tests/test_phase9b_multi_instance.py -v` | 10 passed | Multi-instance runtime behavior, candidate isolation, and Promote/rollback propagation |
| `ruff check src tests` | passed | Static lint checks for source and tests |

The requested combined Phase9 command was also initiated. The desktop command
output channel has a 30-second cutoff, so its final summary was obtained by
running the same four requested files independently; their results total
46 passed tests.

## 7. Known Limitations

- Lifecycle execution remains synchronous for `LifecycleTask` compatibility;
  Phase15-B intentionally does not introduce an async worker.
- Reindex is a candidate-index rebuild for the current KB snapshot only. It
  does not perform parser, chunking, or embedding upgrades.
- `LifecycleTask` storage and compatibility APIs remain. They are adapters and
  recovery mechanisms, not a second document publication state machine.
- The acceptance suites use offline test doubles for Qdrant and canonical
  runner interactions. A release rollout still needs an operator-run canary
  using the production validation endpoint, real vector backend, and an
  observed rollback drill.
- Retrieval, Evaluation, parser algorithms, embedding strategy, and frontend
  behavior are intentionally outside Phase15-B scope.

## 8. Recommendation for Phase15-C

Proceed to Phase15-C only after a controlled deployment rehearsal confirms
the same validation evidence and fenced Promote behavior against production
dependencies. Keep the Phase15-B invariants unchanged: document changes must
remain `UpdateJob`-backed, candidates must remain isolated until validation,
and Active Generation must change only through the Promote path. Any future
worker, parser/configuration upgrade, or observability enhancement should be
specified and accepted as a separate phase rather than folded into this
completed lifecycle alignment.

