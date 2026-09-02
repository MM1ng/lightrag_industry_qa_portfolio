# Task 3 report: generation-scoped ChunkRegistry and canonical hydration

## Delivered

- Added `ChunkRegistry`, an in-memory generation snapshot registry keyed only by canonical `child_chunk_id` (`chunk_id` in the frozen child snapshot). It retains the full frozen row and adapts the necessary source, page, section, parent, and adjacency metadata for the existing evidence-completion boundary.
- `GenerationArtifactResolver` now materializes `ChunkRegistry` from the exact records whose manifest/snapshot bytes it validates. Its cache key contains the resolved workspace, generation id, and manifest hash.
- `QueryApplicationService` passes the resolved registry to the runtime manager. The runtime manager binds it before initialization, and generation changes continue to evict/close prior runtime entries through the existing generation-aware runtime cache key.
- `LightRAGService` hydrates both initial and bounded supplemental LightRAG hits before extraction or `select_evidence`. Exact `child_chunk_id` metadata from the frozen snapshot replaces untrusted hit text, source file, page, section, and parent metadata. Unknown IDs raise an explicit `UnresolvedChunkIdError`; no similarity, filename/page, `current/`, or legacy context-registry fallback is used.
- Removed the canonical query-time read of `context_registry/chunks.jsonl`. Existing evidence/citation, graph, trace, selection, and reranker behavior was left untouched.

## Test-first evidence

1. Added the canonical LightRAG hydration and unresolved-ID tests, then ran them before implementation. They failed because `LightRAGService` had no `chunk_registry` parameter and `ChunkRegistry` did not exist.
2. Added the runtime-manager binding/switch test and ran it with the registry argument removed; it failed with the expected unsupported `chunk_registry` argument error.
3. Implemented the smallest registry, resolver, binding, and hydration path needed to make the tests pass.

## Verification

- `tests/test_generation_retrieval_artifacts.py`, `tests/test_runtime_manager_generations.py`, and `tests/test_lightrag_service.py`: 46 passed.
- Relevant generation query/rollback cases from `tests/test_phase9b_multi_instance.py`: 3 passed.
- Citation/evidence and canonical runtime regression selection: 110 passed, 1 intentionally deselected (the separately verified rollback case).
- `ruff check` on every changed production and test file: passed.
- `python -m compileall -q src`: passed.

## Scope guardrails

- No RRF, reranker, LightRAG/BM25 merge, production BM25 ordering, graph, public result, citation/evidence selection, feature-flag, or trace-schema changes.
- The only intentional runtime behavior change is fail-closed canonical hydration from the active generation snapshot.

## Review-fix round 1

- Added a separately hashed, manifest-bound `retrieval/parent_chunks.jsonl` artifact. The resolver supplies its frozen parent records to `ChunkRegistry`, which now adapts both child and parent records for the unchanged evidence-completion API. Index and incremental builds publish parent records alongside child snapshots; incremental reuse reads only the prior generation's validated parent artifact.
- Hydration now accepts only an explicit `child_chunk_id`. A provenance source header or legacy `chunk_id` cannot be interpreted as a replacement ID. Evidence-identifiable hits with no canonical ID and IDs absent from the snapshot raise explicit unresolved errors before selection or generation.
- Supplemental retrieval now retains the document id emitted by exact snapshot hydration; the filename-based document-id lookup was removed, preventing same-name document collisions.
- Runtime construction fails closed if a registry is supplied to a service without `bind_chunk_registry`; a non-hydrating runtime cannot enter the cache.
- Added review regressions for frozen parent completion, missing canonical IDs, absent runtime bind support, same-filename document identities, and the required G1(A) -> mutable current=B -> G2(B) -> G1 exact hydration/repeated switch path.

### Round-1 verification

- Focused artifact/runtime/LightRAG/hydration and Phase 9 selection: 78 passed.
- Full `test_phase9b_multi_instance.py`: 10 passed.
- Actual G1 snapshot rollback after mutable parsed artifacts are removed: 1 passed.
- Ruff on every changed production and test file: passed.
