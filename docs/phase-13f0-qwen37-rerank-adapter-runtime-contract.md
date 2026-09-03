# Phase 13F-0 — qwen3.7-text-rerank Adapter & Runtime Contract

**Status:** `QWEN37_RERANK_READY_FOR_AB`

## Scope

This phase added explicit adapter/runtime support only. No formal A/B, retrieval
tuning, production-default change, dataset change, or canonical A2 replacement
was performed. Existing `qwen3-rerank` remains the backward-compatible default
for callers that do not pass a model.

## API and adapter audit

The existing provider already uses DashScope's Text Rerank endpoint and the
nested request shape:

```json
{
  "model": "<exact model>",
  "input": {"query": "...", "documents": ["..."]},
  "parameters": {"top_n": 20, "return_documents": false}
}
```

The qwen3.7 model is now an explicitly allowlisted model and is passed through
the same provider-neutral interface. Model identity is included in provider
cache keys and result records. The production provider continues to use its
existing endpoint selection, authentication headers, score parser
(`index`/`relevance_score`), input limits, retry behavior, and fallback policy.

The official API documentation describes qwen3.7-text-rerank at the DashScope
Text Rerank endpoint; this is compatible with the existing adapter's schema.

## Formal runtime contract

`RerankerRuntime(allow_fallback=False)` now raises `RerankRuntimeBlocked` for
provider-unavailable, timeout, invalid response, or provider failure. The
formal Development retrieval evaluator enables this strict mode. Production
callers retain the default `allow_fallback=True` behavior.

## Artifact and replay contract

Provider cache artifacts now record:

- model/provider identity;
- query hash and ordered input candidate IDs;
- `candidate_fingerprint`;
- input/output order and candidate IDs;
- scores, latency, request status, fallback flag, and error type.

The smoke artifact is:
`evaluation/retrieval_foundation/phase13f0_qwen37_smoke_2026-09-03.json`.
Its replay cache is:
`evaluation/retrieval_foundation/qwen37_rerank_cache.jsonl`.

## Smoke test

Four Development-only samples were used: S014, S003, S015, and S011. No
formal effectiveness metric was computed.

| Check | Result |
|---|---|
| Model | `qwen3.7-text-rerank` |
| Provider | `aliyun_model_studio` |
| API calls | 4 |
| Successful responses | 4 |
| Silent fallback | none |
| Candidate count | 20 in / 20 out for each sample |
| Output ranking | parsed successfully |
| Candidate identity | preserved |
| Artifact replay | available through provider cache |

## Verification

Focused tests: `42 passed`.

Ruff was run with the project virtual environment. No new dependency was
installed and no virtual environment was rebuilt.

## Decision

`qwen3.7-text-rerank` is ready for a separately identified A/B evaluation. It
must not be interpreted as the existing canonical qwen3-rerank A2 baseline,
and it is not the default production model.
