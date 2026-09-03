# Phase 13E-0R — A2 Baseline Reproducibility Repair

**Final status:** `BASELINE_REPRODUCIBILITY_BLOCKED`  
**Primary cause:** `NONDETERMINISTIC_RUNTIME`

## 1. Identity audit

- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`
- Generation: `dev-v2-20260902`
- Identity match: `True`
- Config match: `True`
- Validation/Holdout accessed: `false`
- Retrieval config: candidate Top20, final Top10, RRF k=60
- Reranker: qwen3-rerank, timeout 2s

All static dataset/Generation/index identities and retrieval configuration match. The canonical run recorded 23 successful reranker calls plus 1 timeout fallback; the capture did not reproduce that external runtime outcome.

## 2. Per-question drift

Question Hit@5 changed for: `S003`
Top10 ranking mismatch count: `23/24`

S003 is the question responsible for the Question Hit@5 change (`true → false`). Differences are concentrated in final rerank ordering; no dataset or retrieval-configuration drift was found.

## 3. Metrics

| Metric | Canonical | Capture |
|---|---:|---:|
| Recall@5 | 0.818 | 0.826 |
| Recall@10 | 0.831 | 0.834 |
| MRR@5 | 0.894 | 0.883 |
| MRR@10 | 0.894 | 0.890 |
| Question Hit@5 | 1.000 | 0.958 |
| Question Hit@10 | 1.000 | 1.000 |
| Complete@5 | 0.750 | 0.750 |
| Complete@10 | 0.750 | 0.750 |

## 4. Repair

Added `phase13e0r-a2-identity-v1`: the canonical A2 artifact is authoritative, with dataset/Generation/config/evaluator identity and SHA-256. A live capture may not replace it; if external reranker output differs, use artifact replay and report the runtime as non-reproducible. No retrieval logic was modified.

## 5. Gate

`BASELINE_REPRODUCIBILITY_BLOCKED`. The live A2 runtime was not restored to the canonical result in this phase, so Parser A/B and new Retrieval Optimization remain blocked. The cause is classified as `NONDETERMINISTIC_RUNTIME`.
