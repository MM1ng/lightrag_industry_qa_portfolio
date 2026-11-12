# Phase 13D-0 — Evidence Loss Audit

**Scope:** frozen Development V2 only; no model/retrieval rerun.
**A3.1 arm:** `A3.1_original_1_5` (Phase 13C-1 best-result tie)
**Final status:** `INCONCLUSIVE`

## Evidence funnel

| Stage | Count / rate |
|---|---:|
| Gold evidence | 21 |
| Retrieval hit | 7/21 (33.3%) |
| Fusion Top20 | 1/21 (4.8%) |
| Rerank Top20 | unavailable (not persisted) |
| Final Top10 | 0/21 (0.0%) |
| Final Top5 | 0/21 (0.0%) |

Loss rates: lost before fusion `66.7%`; lost after rerank `unavailable`; lost at cutoff `unavailable`.

## Phase 13C-1 configuration

`A3.1_original_1_5`: original query weight 1.5, variants weight 1.0, candidate Top20, RRF k=60, one `qwen3-rerank` call per question with final limit 10.

## Six-question missing-gold path

`retrieval local rank`、`query source`、`rerank Top20/rank` 均为 `unavailable`；报告不从 final rank 反推这些阶段。

| Question | Gold evidence | Retrieval | Fusion rank/score | Rerank rank | Final rank | Status | Cause |
|---|---|---:|---:|---:|---:|---|---|
| S014 | `cchunk-pymupdf-v1-93807a18f7b7345f-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S015 | `cchunk-pymupdf-v1-34cc49bd2766d02e-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S015 | `cchunk-pymupdf-v1-5989850607a8046c-000` | hit | MISS / — | unavailable | MISS | `lost_in_fusion` | `A` |
| S015 | `cchunk-pymupdf-v1-78a156ed97cebd53-000` | hit | MISS / — | unavailable | MISS | `lost_in_fusion` | `A` |
| S006 | `cchunk-pymupdf-v1-5388c52812f37351-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S006 | `cchunk-pymupdf-v1-a03be0b31badfb6b-000` | hit | MISS / — | unavailable | MISS | `lost_in_fusion` | `A` |
| S006 | `cchunk-pymupdf-v1-d8638f275d20c6d6-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S003 | `cchunk-pymupdf-v1-6590f00e21e280d0-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S003 | `cchunk-pymupdf-v1-c97eb4631d5d2c9c-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S003 | `cchunk-pymupdf-v1-87557f88f4709fcc-000` | hit | MISS / — | unavailable | MISS | `lost_in_fusion` | `A` |
| S003 | `cchunk-pymupdf-v1-663e640852497df6-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S016 | `cchunk-pymupdf-v1-99121c418e138c64-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S016 | `cchunk-pymupdf-v1-317e33cc54ca5b18-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S016 | `cchunk-pymupdf-v1-f997c995a333b4ae-000` | hit | MISS / — | unavailable | MISS | `lost_in_fusion` | `A` |
| S016 | `cchunk-pymupdf-v1-16686d3e3ddcc21b-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S011 | `cchunk-pymupdf-v1-acca8dbfb1b95f8f-000` | hit | MISS / — | unavailable | MISS | `lost_in_fusion` | `A` |
| S011 | `cchunk-pymupdf-v1-bf2be6315d2f187b-000` | hit | 18 / 0.04919117550446458 | unavailable | MISS | `lost_after_rerank_or_topk_selection` | `UNAVAILABLE_B_OR_C` |
| S011 | `cchunk-pymupdf-v1-ac2c48838803419d-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S011 | `cchunk-pymupdf-v1-cc1f6fd20cdb46f6-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S011 | `cchunk-pymupdf-v1-91e5666cf6078fb9-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |
| S011 | `cchunk-pymupdf-v1-93807a18f7b7345f-000` | MISS | MISS / — | unavailable | MISS | `lost_before_fusion` | `UNCLASSIFIED_PRE_FUSION` |

## Root-cause proportions

| Category | Count / proportion |
|---|---:|
| A | 6/21 (28.6%) |
| B | 0/21 (0.0%) |
| C | 0/21 (0.0%) |
| D | 0/21 (0.0%) |
| UNCLASSIFIED_PRE_FUSION | 14/21 (66.7%) |
| UNAVAILABLE_B_OR_C | 1/21 (4.8%) |

## Data gaps and decision

- Phase 13C-1 stored `raw_retrieved` only as a boolean; local retrieval rank and query source are unavailable.
- Phase 13C-1 reranked with `limit=10`; rerank Top20 and rerank rank for items outside final Top10 are unavailable.
- D (mapping issue) was not observed in the persisted identity checks; no mapping mismatch was inferred.
- Of 21 A3-missing gold evidence, 14 were not observed in persisted raw retrieval, 6 were observed but lost before fusion Top20, and 1 was in fusion Top20 but cannot be separated between B and C.
- The Phase 13C-1 summary `10/21` counted all gold evidence across the six questions; this audit uses only the 21 evidence items that Phase 13B explicitly marked as missing, for an apples-to-apples loss audit.

**Next recommendation:** `RERANKER_MISMATCH` is not provable without rerank Top20 trace. The auditable next direction is `NO_CHANGE` for this incomplete audit artifact; add richer trace only in a future explicitly approved audit, without changing retrieval in this phase.
