# Phase 13D-3 — Controlled Retrieval Trace Re-evaluation

**Final status:** `PASS_TO_NEXT_PHASE`  
**Primary bottleneck:** `NO_MEANINGFUL_MULTI_QUERY_GAIN`  
**Next recommendation:** `MULTI_QUERY_STOP`

## Identity and fixed configuration

- Branch/commit: `dev/retrieval-foundation-qa-downstream` / `b63f7a7344ad760924b822c9697ba7771fe111c1`
- Dataset: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`, split `Development`; Generation `dev-v2-20260902`
- Validation/Holdout accessed: `false`
- A3.1: original + existing 3 variants, weighted RRF (original=1.5, variant=1.0), candidate Top20, one qwen3-rerank call, final Top10.

## A2 vs A3.1

| Metric | A2 | A3.1 |
|---|---:|---:|
| Recall@5 | 0.818 | 0.809 |
| Recall@10 | 0.831 | 0.831 |
| MRR@5 | 0.894 | 0.883 |
| MRR@10 | 0.894 | 0.889 |
| Question Hit@5 | 1.000 | 0.958 |
| Question Hit@10 | 1.000 | 1.000 |
| Complete@5 | 0.750 | 0.750 |
| Complete@10 | 0.750 | 0.750 |
| Multi-evidence Complete@5 | 0.250 | 0.250 |
| Multi-evidence Complete@10 | 0.250 | 0.250 |

## Missing-only evidence funnel (A3.1)

| Stage | Retained |
|---|---:|
| Raw retrieval hit | 7/21 |
| Fusion Top20 | 1/21 |
| Rerank Top20 | unavailable |
| Final Top10 | 0/21 |
| Final Top5 | 0/21 |

The six-question missing-only set contains 21 gold evidence items. A2 final metrics are comparable, but its historical artifact cannot support raw/fusion/rerank funnel counts. No stage was guessed.

## Attribution

A3.1 raw retrieval recovers 7/21, but fusion retains only 1/21 and final Top10 retains 0/21. Complete multi-evidence coverage remains unchanged versus A2 (`0.143` at both @5 and @10). The evidence does not show a meaningful multi-query gain; the dominant observed loss is candidate/fusion-stage recall, but this phase does not optimize it.

Regression count versus frozen A2: `0`.

## Limitations

- A2 historical artifact has no raw retrieval/fusion/rerank Top20 trace; those A2 funnel stages are unavailable, not inferred.
- A3.1 reranker runtime persists final Top10 scores; rerank Top20 rank for non-final candidates is unavailable.

No retrieval or reranker optimization was started.
