# Phase 13B — Multi-query Candidate Recall Ablation

**Final status:** `PASS_TO_EVIDENCE_DIVERSITY`
**Scope:** `development_only_offline_ablation`

## Core metrics

| Metric | A2 | A3 |
|---|---:|---:|
| Recall@5 | 0.818 | 0.776 |
| Recall@10 | 0.831 | 0.836 |
| MRR@5 | 0.894 | 0.883 |
| MRR@10 | 0.894 | 0.890 |
| Question Hit@5 | 1.000 | 0.958 |
| Complete Evidence Coverage@5 | 0.750 | 0.708 |
| Complete Evidence Coverage@10 | 0.750 | 0.750 |
| Multi-evidence Complete@5 | 0.250 | 0.250 |
| Multi-evidence Complete@10 | 0.250 | 0.250 |

Regression count: `0` at @10; `1` question-hit regression at @5
Average query count: `4.00`
Average union candidates: `63.46`
Average latency: `8545.4 ms`

## Phase 13A six multi-evidence misses

Recovered missing gold evidence: `1/21`

| Question | Missing gold → A3 evidence status |
|---|---|
| S014 | cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS |
| S015 | cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=variant_2; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=original |
| S006 | cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=original; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS |
| S003 | cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=variant_2; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS |
| S016 | cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=original; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS |
| S011 | cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=variant_1; cchunk-pymup…: recovered / pre-rerank=5 / final=10 / variant=original; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS; cchunk-pymup…: MISS / pre-rerank=MISS / final=MISS / variant=MISS |

## Integrity

- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`
- Generation: `dev-v2-20260902`
- Reranker calls/success/fallback: `24/24/0`
- Validation/Holdout accessed: `False`
- A2 rerun: `False`; saved A2 artifact reused

## Decision

A3 uses the original query plus variants, performs candidate union/dedup and one RRF before exactly one qwen3-rerank call. No production integration or Evidence Diversity work was started.
