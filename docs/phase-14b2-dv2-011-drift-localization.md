# Phase 14B-2 — D-V2-011 Trace Drift Localization Audit

**Classification:** `ARTIFACT_SCHEMA_MISMATCH`

## Query

- Match: `True`

## Canonical vs replay final ranking

- Canonical Top5: `['cchunk-pymupdf-v1-3c48ca477586c617-000', 'cchunk-pymupdf-v1-08ad51d60ec4d01b-000', 'cchunk-pymupdf-v1-0261999df5570bed-000', 'cchunk-pymupdf-v1-4e14a3b265877fb1-000', 'cchunk-pymupdf-v1-53f148860125f4f4-000']`
- Replay Top5: `['cchunk-pymupdf-v1-3c48ca477586c617-000', 'cchunk-pymupdf-v1-0261999df5570bed-000', 'cchunk-pymupdf-v1-4e14a3b265877fb1-000', 'cchunk-pymupdf-v1-08ad51d60ec4d01b-000', 'cchunk-pymupdf-v1-5178c456afbf1e5a-000']`
- Canonical Top10: `['cchunk-pymupdf-v1-3c48ca477586c617-000', 'cchunk-pymupdf-v1-08ad51d60ec4d01b-000', 'cchunk-pymupdf-v1-0261999df5570bed-000', 'cchunk-pymupdf-v1-4e14a3b265877fb1-000', 'cchunk-pymupdf-v1-53f148860125f4f4-000', 'cchunk-pymupdf-v1-92120ce2e7ee0096-000', 'cchunk-pymupdf-v1-5178c456afbf1e5a-000', 'cchunk-pymupdf-v1-8cb55fe1213f5d02-000', 'cchunk-pymupdf-v1-f86158424224442d-000', 'cchunk-pymupdf-v1-40b4f739da3cec6e-000']`
- Replay Top10: `['cchunk-pymupdf-v1-3c48ca477586c617-000', 'cchunk-pymupdf-v1-0261999df5570bed-000', 'cchunk-pymupdf-v1-4e14a3b265877fb1-000', 'cchunk-pymupdf-v1-08ad51d60ec4d01b-000', 'cchunk-pymupdf-v1-5178c456afbf1e5a-000', 'cchunk-pymupdf-v1-92120ce2e7ee0096-000', 'cchunk-pymupdf-v1-fe95f94d24968921-000', 'cchunk-pymupdf-v1-53f148860125f4f4-000', 'cchunk-pymupdf-v1-8cb55fe1213f5d02-000', 'cchunk-pymupdf-v1-c252bcbee91ab8cc-000']`
- Added: `['cchunk-pymupdf-v1-fe95f94d24968921-000', 'cchunk-pymupdf-v1-c252bcbee91ab8cc-000']`
- Removed: `['cchunk-pymupdf-v1-f86158424224442d-000', 'cchunk-pymupdf-v1-40b4f739da3cec6e-000']`
- Rank changed: `[{'candidate_id': 'cchunk-pymupdf-v1-08ad51d60ec4d01b-000', 'canonical_rank': 2, 'replay_rank': 4}, {'candidate_id': 'cchunk-pymupdf-v1-0261999df5570bed-000', 'canonical_rank': 3, 'replay_rank': 2}, {'candidate_id': 'cchunk-pymupdf-v1-4e14a3b265877fb1-000', 'canonical_rank': 4, 'replay_rank': 3}, {'candidate_id': 'cchunk-pymupdf-v1-53f148860125f4f4-000', 'canonical_rank': 5, 'replay_rank': 8}, {'candidate_id': 'cchunk-pymupdf-v1-5178c456afbf1e5a-000', 'canonical_rank': 7, 'replay_rank': 5}, {'candidate_id': 'cchunk-pymupdf-v1-8cb55fe1213f5d02-000', 'canonical_rank': 8, 'replay_rank': 9}]`

## Stage localization

- Raw retrieval: unavailable: canonical artifact stores no raw candidate list
- Fusion: `True` for all canonical final candidates observed in replay.
- Rerank input: unavailable: no complete input pool or candidate fingerprint
- Rerank output: canonical score differences are `unavailable: canonical A2 final rows do not retain rerank_score`.
- First observable divergence: `final_ranking`
- First actual divergence: `unavailable`

## Decision

The capture is not suitable for root-cause attribution because the canonical artifact lacks the raw candidate pool, full fusion pool, rerank input fingerprint, and rerank scores. The observable final disagreement follows matching fusion metadata for every canonical final candidate, but that is insufficient to prove a reranker drift. This is a read-only schema-comparability finding; no retrieval optimization is authorized.
