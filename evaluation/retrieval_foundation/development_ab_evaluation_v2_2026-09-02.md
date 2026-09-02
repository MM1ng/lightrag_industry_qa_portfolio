# Retrieval Foundation Development A/B Evaluation

**Status:** `INCONCLUSIVE`  
**Downstream QA allowed:** `False`  
**Scope:** `development_only`  
**Question count:** `6` (sample-size limitation: `True`)

## Aggregate metrics

| Variant | Recall@5 | Recall@10 | MRR@5 | MRR@10 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| A0_lightrag | 0.238 | 0.258 | 0.597 | 0.597 | 0.0 | 0.0 |
| A1_lightrag_bm25_rrf | 0.271 | 0.271 | 0.833 | 0.833 | 0.6 | 4.8 |
| A2_lightrag_bm25_rrf_reranker | 0.271 | 0.271 | 0.833 | 0.833 | 0.6 | 4.8 |

## Reranker

{
  "calls": 6,
  "success_count": 0,
  "provider": "external",
  "model": "unavailable",
  "timeout_seconds": 2.0,
  "external_result_determinism": "not_guaranteed",
  "fallback_count": 6,
  "fallback_rate": 1.0
}

## Delta classification

| Classification | Count |
|---|---:|
| NO_MATERIAL_CHANGE | 4 |
| RRF_IMPROVEMENT | 2 |

## Trace integrity

- Invalid chunk IDs: `0`

## Question IDs

S014, S015, S006, S003, S016, S011

Raw per-question details are stored in the adjacent JSON report. Results from six questions are pipeline smoke evidence, not a stable effectiveness claim (Sample-size limitation: n=6).
