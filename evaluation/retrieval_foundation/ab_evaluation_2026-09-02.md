# Retrieval Foundation Development-only A/B Evaluation

## Decision

**INCONCLUSIVE — STOPPED_DATA_GAP**

No A0/A1/A2 scores are reported. The evaluation was stopped because the
required fixed Generation and evidence corpus are not available in this
worktree.

## Audit findings

- `evaluation/retrieval_foundation/dev_cases.jsonl` contains 6 labeled cases,
  covering model, part-number, parameter, semantic, fault, and procedure
  questions.
- The local `.run/industrial_rag.db` contains the expected lifecycle tables,
  but `knowledge_bases` has 0 rows; therefore there is no active or candidate
  Generation to pin for the comparison.
- No generation-scoped `retrieval/child_chunks.jsonl` snapshot was found for a
  runtime Generation. The parser experiment snapshots are not treated as a
  substitute because they are not the same LightRAG/evidence corpus.
- The current `retrieval_evaluation.py` calculates ranking metrics from
  already-produced ranked IDs only. It does not yet run A0/A1/A2, measure
  p50/p95 latency, count reranker fallbacks, or produce per-question deltas.

## Why execution stopped

Without one immutable Generation snapshot and its corresponding LightRAG
workspace, BM25 index, and evidence corpus, comparing the variants would mix
different corpora or require fabricated rankings. That would violate the
fixed-corpus and no-fabrication requirements.

No Validation or Holdout data was accessed.

## Commands and results

```text
Get-ChildItem evaluation -Recurse -File
Get-ChildItem -Path . -Recurse -Filter child_chunks.jsonl
sqlite3 .run/industrial_rag.db: select ... from knowledge_bases
```

Evidence: six development cases exist; no runtime Generation/KB rows exist.

```text
.\.venv\Scripts\pytest.exe -q tests/test_hybrid_query_integration.py tests/test_retrieval_evaluation.py tests/test_lightrag_service.py tests/test_rrf_fusion.py tests/test_reranker_runtime.py tests/test_retrieval_trace_v2.py tests/test_runtime_manager_generations.py
45 passed

.\.venv\Scripts\ruff.exe check .
All checks passed
```

## Required input before rerun

Provide or build one Development-only Generation with its frozen
`child_chunks.jsonl`, `chunk_manifest.json`, `lexical_index.json`, LightRAG
workspace, and evidence corpus. Then add an execution adapter that runs all
three variants against exactly that snapshot and records latency and reranker
fallback events.
