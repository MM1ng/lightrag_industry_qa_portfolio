# Phase 13E-0 — A2 Missing Evidence Root-Cause Trace

**Final status:** `BLOCKED`  
**Next recommendation:** `MORE_TRACE_REQUIRED`

## Identity

- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`; Generation: `dev-v2-20260902`; split: `Development`
- Validation/Holdout accessed: `false`
- Capture metrics match frozen A2 canonical metrics: `False`

## Missing-only evidence funnel (diagnostic capture)

| Stage | Retained |
|---|---:|
| Missing gold total | 21 |
| Retrieval hit | 1/21 |
| Fusion retained | 1/21 |
| Reranker retained | unavailable |
| Final Top10 | 1/21 |
| Final Top5 | 1/21 |

## Evidence-level primary causes

| Cause | Count | Rate |
|---|---:|---:|
| CANDIDATE_RECALL | 20 | 0.952 |
| UNRESOLVED | 1 | 0.048 |

## Question-level attribution

| Question | Gold total | Missing | Retrieval hit | Dominant cause |
|---|---:|---:|---:|---|
| S003 | 5 | 4 | 0 | CANDIDATE_RECALL |
| S006 | 5 | 3 | 0 | CANDIDATE_RECALL |
| S011 | 8 | 6 | 0 | CANDIDATE_RECALL |
| S014 | 2 | 1 | 0 | CANDIDATE_RECALL |
| S015 | 5 | 3 | 0 | CANDIDATE_RECALL |
| S016 | 5 | 4 | 1 | CANDIDATE_RECALL |

## Interpretation

Per-evidence details, including LightRAG/BM25 local rank and score, RRF rank/score, rerank fields, and final ranks, are stored in the JSON artifact. The reranker Top20 stage is unavailable in the current runtime trace; no rerank mismatch or TopK selection cause is asserted without that field.

- RerankerRuntime persists only final Top10; rerank rank/score for candidates outside final Top10 is unavailable and classified UNRESOLVED.
- Capture metrics do not match frozen A2 canonical metrics; this funnel is diagnostic-only and cannot support root-cause decisions.

Because the trace-capture metrics do not match the frozen A2 canonical metrics, this run is blocked and the cause counts are not decision-grade. No optimization recommendation is authorized.
