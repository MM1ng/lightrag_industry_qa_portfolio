# Phase 13F-1 — qwen3-rerank vs qwen3.7-text-rerank Paired A/B

**Final status:** `KEEP_QWEN3_RERANK`

## Experiment identity

- Branch: `dev/retrieval-foundation-qa-downstream`
- Dataset: 24-question Development V2; fingerprint `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`
- Generation: `dev-v2-20260902`
- Fixed retrieval: LightRAG + BM25 + RRF, candidate Top20, RRF k=60
- Fixed rerank: top_n=20, timeout=60s, strict no-fallback
- Validation/Holdout: not accessed
- Candidate bundle: generated once before either arm; all 24 per-question fingerprints and orders matched

## Core metrics

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
| Multi-evidence Complete@5 | 0.143 (1/7) | 0.143 (1/7) |
| Multi-evidence Complete@10 | 0.143 (1/7) | 0.143 (1/7) |
| Regression count (Complete@5) | — | 0 |

The frozen Development V2 artifact contains 7 multi-evidence questions; the
six Phase 13A focus questions are included in that set.

## Runtime

| Metric | qwen3-rerank | qwen3.7-text-rerank |
|---|---:|---:|
| Success / total | 24 / 24 | 24 / 24 |
| Timeout | 0 | 0 |
| Invalid response | 0 | 0 |
| Fallback | 0 | 0 |
| Mean latency | 477.2 ms | 491.6 ms |
| P50 latency | 461.0 ms | 476.5 ms |
| P95 latency | 515.0 ms | 593.0 ms |

## Paired question analysis

- Output ranking was different for all 24 questions.
- Improved Question Hit@5: 1 question (`S003`).
- Regressed Question Hit@5: 0 questions.
- Newly completed multi-evidence questions: 0.
- Regressed multi-evidence questions: 0.
- Complete@5 regression: 0.
- The new model improved some individual gold ranks, but also displaced other
  gold evidence; the net effect was lower evidence Recall/MRR and no gain in
  complete multi-evidence coverage.

### Six Phase 13A multi-evidence focus questions

| Question | qwen3 gold ranks | qwen3.7 gold ranks | Complete change |
|---|---|---|---|
| S003 | `6` plus missing | `2` plus missing | no |
| S006 | `1, 2` plus missing | `1, 2` plus missing | no |
| S011 | `1` plus missing | `1` plus missing | no |
| S014 | `1` plus missing | `1` plus missing | no |
| S015 | `1, 5` plus missing | `1, 9` plus missing | no |
| S016 | `1, 3` plus missing | `1, 13` plus missing | no |

Full per-question output IDs, scores, rank deltas, fingerprints, and runtime
records are in the structured artifact:
`evaluation/retrieval_foundation/phase13f1_paired_rerank_ab_2026-09-03.json`.

## Promotion gate

The gate is not passed: although Question Hit@5 did not decrease and improved
by one question, Recall@5, Recall@10, MRR@5, and MRR@10 decreased; Complete@5
did not improve; and zero additional multi-evidence questions were completed.
The old `qwen3-rerank` remains the selected model. No production default was
changed.
