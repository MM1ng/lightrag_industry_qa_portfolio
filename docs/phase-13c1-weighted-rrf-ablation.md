# Phase 13C-1 — Weighted Query-level RRF Ablation

**Final status:** `FUSION_NOT_PRIMARY_BOTTLENECK`

## A3 vs A3.1 metrics

| Metric | A3 baseline | A3.1 original=1.5 | A3.1 original=2.0 |
|---|---:|---:|---:|
| Recall@5 | 0.776 | 0.809 | 0.809 |
| Recall@10 | 0.836 | 0.831 | 0.831 |
| MRR@5 | 0.883 | 0.883 | 0.883 |
| MRR@10 | 0.890 | 0.889 | 0.889 |
| Question Hit@5 | 0.958 | 0.958 | 0.958 |
| Question Hit@10 | 1.000 | 1.000 | 1.000 |
| Complete@5 | 0.708 | 0.750 | 0.750 |
| Complete@10 | 0.750 | 0.750 | 0.750 |
| Multi-evidence Complete@5 | 0.250 | 0.250 | 0.250 |
| Multi-evidence Complete@10 | 0.250 | 0.250 | 0.250 |

## Six Phase 13A multi-evidence misses

| Arm | Gold evidence recovered into fusion Top20 | Final Top5 | Final Top10 |
|---|---:|---:|---:|
| A3.1_original_1_5 | 10 | 6 | 9 |
| A3.1_original_2_0 | 10 | 6 | 9 |

## Notes

- Each arm used the same Phase 13B query variants; query-generation prompt was not changed.
- Each arm performed one weighted query-level fusion and exactly 24 qwen3-rerank calls; no per-variant reranking.
- A2 was not rerun; the frozen A2 artifact was reused.
- Validation/Holdout was not accessed.

## Regression and interpretation

- Top10 regression count versus frozen A2: `0` for both arms.
- Question Hit@5 remains `0.958`, so the Phase 13B Top5 regression did not disappear.
- Weighted fusion raised the six-question missing-gold count entering fusion Top20 from `1/21` in A3 to `10/21`, but final Multi-evidence Complete remained unchanged at `2/8` for both @5 and @10.

**Decision:** `FUSION_NOT_PRIMARY_BOTTLENECK`. Fusion loss is real at candidate inclusion, but weighted query-level RRF alone does not produce a complete-evidence or Top5 improvement. This phase does not modify production retrieval.
