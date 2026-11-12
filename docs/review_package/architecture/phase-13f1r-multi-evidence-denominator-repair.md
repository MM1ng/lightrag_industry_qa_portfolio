# Phase 13F-1R — Multi-evidence Denominator Consistency Repair

**Final status:** `EVALUATION_CONTRACT_RESTORED`

## Root cause of 8 → 7

The frozen Development V2 gold contract contains eight multi-evidence cases:

`S014, S015, S006, S003, S016, S011, D-V2-001, D-V2-012`

`D-V2-001` was incorrectly excluded by the Phase 13F-1 helper because it used
the filter `evidence_pattern == "multi_evidence"`. Its formal evidence pattern
is `adjacent_chunk_evidence`, but it has two expected child chunks and therefore
belongs in the multi-evidence denominator. This was an evaluator filter bug,
not a retrieval or reranker result.

The repaired contract identifies multi-evidence cases from the frozen gold
contract: a question is included when its unique `expected_child_chunk_ids`
count is greater than one. Retrieval hits, reranker success, and final
completeness cannot remove a case from the denominator.

## Offline replay results

No model calls and no retrieval were performed. The saved Phase 13F-1 artifact
was replayed with the repaired evaluator.

| Metric | qwen3-rerank | qwen3.7-text-rerank |
|---|---:|---:|
| Recall@5 | 0.826 | 0.818 |
| Recall@10 | 0.834 | 0.826 |
| MRR@5 | 0.883 | 0.868 |
| MRR@10 | 0.890 | 0.868 |
| Question Hit@5 | 0.958 | 1.000 |
| Question Hit@10 | 1.000 | 1.000 |
| Complete@5 | 0.750 | 0.750 |
| Complete@10 | 0.750 | 0.750 |
| HARD Complete@5 | 0.875 | 0.875 |

| Model | Multi-evidence Complete@5 | Multi-evidence Complete@10 |
|---|---:|---:|
| qwen3-rerank | 2/8 = 0.250 | 2/8 = 0.250 |
| qwen3.7-text-rerank | 2/8 = 0.250 | 2/8 = 0.250 |

All ordinary core metrics are unchanged from the saved Phase 13F-1 artifact;
only the denominator/reporting semantics were repaired.

## Decision

The qwen3.7 promotion decision does not change. It remains
`KEEP_QWEN3_RERANK`: no additional multi-evidence questions were completed,
while Recall and MRR were lower for qwen3.7. No production model, retrieval
algorithm, ranking, dataset, or gold labels were modified.

Structured replay output:
`evaluation/retrieval_foundation/phase13f1r_denominator_repair_2026-09-03.json`.
