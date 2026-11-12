# Phase 13D-1 Trace Consistency Audit

Status: **PASS_TO_NEXT_PHASE**  
Root cause: **REPORT_BUG**  
Recommendation: **FIX_EVALUATION_PIPELINE**  
Audit commit: `fdc6cbccf7e56a23c574383c5a4d524383ef4942`

## Data identity

- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`; questions: 24; split: Development
- Generation: `dev-v2-20260902`
- Validation/Holdout accessed: `false`
- Inputs: Phase 13B missing-set, Phase 13C-1 JSON, Phase 13D-0 JSON

## Difference reproduced

| Metric | Phase 13C-1 report | Phase 13D-0 audit | Strict recompute | Match |
|---|---:|---:|---:|---|
| Fusion Top20 | 10/21 | 1/21 | 1/21 | yes |
| Final Top10 | 9/21 | 0/21 | unavailable | unavailable |
| Final Top5 | 6/21 | 0/21 | unavailable | unavailable |

The discrepancy is a reporting denominator/selection bug: C1's `10` is the all-gold count (`10`) across the six questions, mislabeled with the 21-item missing-evidence denominator. Restricting to the Phase 13B missing set yields **1/21**, exactly matching D0.

## Evidence ID alignment

Exact child-ID alignment: **21/21**. No child/parent, citation/retrieval, or normalization mismatch was observed in the checked keys.

## Trace schema gap

Missing or non-equivalent persisted fields:

- `query_variants`
- `retrieval_candidates`
- `fusion_candidates`
- `rerank_candidates`
- `final_top_k`
- `evidence_lineage`
- `retrieval_local_rank`
- `query_source`
- `rerank_rank`

The C1 artifact contains summary `fusion_top20`/`final_top10` and boolean `raw_retrieved`, but not full candidate lineage, local rank/query source, or rerank Top20. Those values remain `unavailable`; this audit did not rerun anything.

## Root cause and next step

Primary root cause: **REPORT_BUG**. Secondary limitation: **TRACE_SCHEMA_GAP**.  
Next recommendation: **FIX_EVALUATION_PIPELINE**. Correct the metric/report denominator and preserve full stage lineage in a future experiment artifact. No Retrieval Optimization is justified by this inconsistency audit.
