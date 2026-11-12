# Phase 14C-0 — Canonical A2 Artifact Schema v2

**Status:** `ARTIFACT_SCHEMA_READY`

## Decision

The historical A2 artifact remains the immutable v1 authority. It is readable for historical metric reporting, but cannot prove a pipeline-stage divergence because it lacks the trace-complete v2 fields. Schema v2 is therefore defined for the next controlled canonical capture; it does not alter historical metrics or silently upgrade the v1 JSON.

## Historical compatibility

- Historical artifact: `evaluation\retrieval_foundation\formal_development_effectiveness_2026-09-03.json`
- SHA-256 before/after read-only audit: `9d4839a4c5fe8717877c7d92c206149a137997e20f1f3c957c5f4ffdc08c3bb3` / `9d4839a4c5fe8717877c7d92c206149a137997e20f1f3c957c5f4ffdc08c3bb3`
- Immutable: `True`
- Legacy readable: `True`
- Trace complete: `False`

## Identity gate

- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`
- Generation: `dev-v2-20260902`
- Historical authority hash matches its identity contract: `True`

## v2 required per-question trace

1. Query and query hash.
2. Raw LightRAG/BM25 retrieval candidates, local ranks, and raw scores.
3. Complete RRF fusion pool, ranks, scores, and contributor lineage.
4. Ordered rerank input plus deterministic candidate fingerprint.
5. Complete rerank output, scores, and output ranks.
6. Final Top5 and Top10 prefixes.
7. Runtime metadata including provider/model, latency, request status, and fallback flag.

The v2 validator rejects identity drift, missing rerank fingerprints, altered candidate identity, and final rankings which are not prefixes of the saved rerank output. Its offline replay delegates to the existing `recompute_trace_metrics` implementation, preserving Recall, MRR, Question Hit, and Complete semantics.

## Why v1 is not retroactively converted

The v1 artifact does not contain:

- `raw_retrieval_candidates`
- `fusion_candidates`
- `rerank_input.candidate_fingerprint`
- `rerank_output`
- `rerank_scores`
- `runtime_metadata`

Filling these fields from a later live capture would misrepresent a new external rerank execution as the historical canonical execution. The contract therefore blocks promotion instead of guessing or modifying the v1 artifact.

## Compatibility and replay

Legacy v1 remains readable and unchanged. A validated v2 artifact can independently recompute Recall@5/@10, MRR@5/@10, Question Hit@5/@10, and Complete@5/@10 entirely offline from its saved final rankings and frozen expected evidence.

## Scope boundary

This phase introduces only artifact observability and integrity contracts. It makes no retrieval, chunking, embedding, reranker, or evaluator-metric change.
