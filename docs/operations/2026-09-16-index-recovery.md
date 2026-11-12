# 2026-09-16 Active index recovery

## Verified causes

- The API used port 16333. The stopped `ira-phase3-qdrant-test` container bound that port and retained 453 Active-generation chunk points in its writable layer, without a storage mount.
- `start_phase9b_qdrant.ps1` only searched its generated container name. It created `ira-qdrant-16333` with a different empty volume instead of locating the existing deployment. The original points were not lost.
- Reindex inherited the Active collection and skipped document ingestion. With an empty source collection it returned `candidate_built` with zero added/reused chunks.
- Building is followed by explicit validation and promotion. No automatic validation/promotion was implemented by the document-task adapter. Earlier progress messages incorrectly described the completed build call as ongoing embedding.
- `/readyz` only checked runtime existence. It did not establish Active index integrity.

## Changes

- Startup recognizes existing port bindings, rejects ambiguous container identity, requires a persistent mount on existing storage, checks Docker exit codes, and supports an explicit `QDRANT_CONTAINER_NAME`.
- Local staging selects `ira-qdrant-16333-recovered`, backed by `ira-qdrant-16333-recovered-data`.
- Workbench startup checks Active chunk counts against Qdrant before reporting success.
- Reindex builds vectors from frozen children into a fresh candidate and rejects a point-count mismatch.
- Completed candidates report `awaiting_validation`. Incomplete building candidates cannot report idempotent success.
- The empty repair job `6fca078b31024262a6bbcbf13a8fa271` was cancelled through its API. The original Active pointer was retained.

## Recovery and verification

Offline backup: `.run/qdrant-recovery-20260916/storage` (about 11 GB). Original containers and volumes are retained, stopped when they conflict with the restored service. Backup and runtime data must not be committed.

Migration copies the stopped original container's storage to the new persistent volume. Completion requires live count verification, restart persistence, and real query verification.

Final combined verification: 57 tests passed in 34.37 seconds (`test_phase15b_unified_document_lifecycle.py`, `test_phase9.py`, `test_active_index_startup.py`). PowerShell syntax and scoped `git diff --check` passed. No retrieval thresholds or validation gates changed.

Live acceptance completed:

- Recovered volume has 453 Active chunk points; still 453 after `docker restart`.
- API restarted with changed code; workbench reports `ACTIVE_INDEX_OK ... points=453` and `WORKBENCH_READY`.
- Startup question returned an answer with two citations (t1739cn.pdf p.31; 2196-ANSI-Manual-Chinese.pdf p.17).
- Disassembly question through the frontend proxy returned a 183-character answer with three citations.
- Out-of-scope weather question returned an empty answer and zero citations.
- Original Active generation remains `b856c823e4894ed4bea7c1e41a0b7db8` / `g9b05e83c30b45eb7da1c5224`.

State: SUCCESS. New reindex behavior is regression-tested offline; no unnecessary paid full rebuild or new-generation promotion was performed after recovering the original index. `/readyz` remains a process-runtime check; the stronger index check is in workbench startup.
