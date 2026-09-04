# Phase 14B-0 — Evidence-to-Chunk Trace Audit

**Status:** `TRACE_INCOMPLETE`
**Final decision:** `TRACE_INCOMPLETE`

## 1. Executive Summary

All 21 historical missing evidence records are present in the saved PyMuPDF parser audit, frozen child registry, embedding snapshot, and BM25 index. The canonical A2 artifact records final TopK only; it does not persist canonical raw retrieval, fusion Top20, or complete reranker output. Consequently, the first retrieval-stage loss cannot be attributed without guessing. This audit therefore finds no supported parsing, chunk-generation, or index loss and treats the retrieval-stage root cause as trace-incomplete.

## 2. Experiment Identity

- Audit commit: `d8f5ca59bf6b2ff5c503508b798285f3a829b227`
- Generation: `dev-v2-20260902`
- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`
- Child chunk registry fingerprint: `b1a6334f6e725591e5d796f318e76d3eb77c46fa8c8cc3f6342dc850ad77d1e0`
- BM25 fingerprint: `6a1fd370918362422d7539f60265cf3992e06f1f9ddd5c69d9e8545c3d7f1c13`
- Embedding snapshot fingerprint: `744553a7f8c9ae7cf74ba7576b3839d2a73bd5871a4607f9a5f178c1e697b670`
- Qdrant collection: `not_applicable:nano_local_vdb_chunks`
- Identity drift: `none`

Qdrant is not applicable to this frozen generation: the saved LightRAG chunk vector store is the local Nano `vdb_chunks.json` snapshot. No Qdrant connection was read or queried.

## 3. Evidence Funnel

| Stage | Known hit / 21 | Unavailable / 21 |
|---|---:|---:|
| parsed | 21 | 0 |
| chunk | 21 | 0 |
| embedding | 21 | 0 |
| bm25 | 21 | 0 |
| retrieval | 0 | 21 |
| fusion | 0 | 21 |
| rerank | 0 | 21 |
| final_top10 | 0 | 0 |
| final_top5 | 0 | 0 |

The final Top10/Top5 values are independently read from the canonical formal A2 artifact. The intermediate A2 values are unavailable rather than zero because their candidate traces were never saved in that artifact.

## 4. Root Cause Distribution

- `PARSING_LOSS`: 0
- `CHUNK_GENERATION_LOSS`: 0
- `INDEX_MISSING`: 0
- `CANDIDATE_RECALL_FAILURE`: 0
- `FUSION_LOSS`: 0
- `RERANKER_LOSS`: 0
- `TOPK_SELECTION_LOSS`: 0
- `UNRESOLVED`: 21

## 5. Question Level Analysis

### S014

- Gold evidence: 1; final Top10 hits: 0; final Top5 hits: 0
- Parsed / Chunk / Embedding / BM25: 1/1/1/1
- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.
- First failure point: `canonical_a2_retrieval_trace_unavailable`; root cause: `UNRESOLVED`.

| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |
|---|---:|---|---|---|---|---|---|
| `cchunk-pymupdf-v1-93807a18f7b7345f-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |

### S015

- Gold evidence: 3; final Top10 hits: 0; final Top5 hits: 0
- Parsed / Chunk / Embedding / BM25: 3/3/3/3
- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.
- First failure point: `canonical_a2_retrieval_trace_unavailable`; root cause: `UNRESOLVED`.

| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |
|---|---:|---|---|---|---|---|---|
| `cchunk-pymupdf-v1-34cc49bd2766d02e-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-5989850607a8046c-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-78a156ed97cebd53-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |

### S006

- Gold evidence: 3; final Top10 hits: 0; final Top5 hits: 0
- Parsed / Chunk / Embedding / BM25: 3/3/3/3
- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.
- First failure point: `canonical_a2_retrieval_trace_unavailable`; root cause: `UNRESOLVED`.

| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |
|---|---:|---|---|---|---|---|---|
| `cchunk-pymupdf-v1-5388c52812f37351-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-a03be0b31badfb6b-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-d8638f275d20c6d6-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |

### S003

- Gold evidence: 4; final Top10 hits: 0; final Top5 hits: 0
- Parsed / Chunk / Embedding / BM25: 4/4/4/4
- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.
- First failure point: `canonical_a2_retrieval_trace_unavailable`; root cause: `UNRESOLVED`.

| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |
|---|---:|---|---|---|---|---|---|
| `cchunk-pymupdf-v1-6590f00e21e280d0-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-c97eb4631d5d2c9c-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-87557f88f4709fcc-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-663e640852497df6-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |

### S016

- Gold evidence: 4; final Top10 hits: 0; final Top5 hits: 0
- Parsed / Chunk / Embedding / BM25: 4/4/4/4
- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.
- First failure point: `canonical_a2_retrieval_trace_unavailable`; root cause: `UNRESOLVED`.

| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |
|---|---:|---|---|---|---|---|---|
| `cchunk-pymupdf-v1-99121c418e138c64-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-317e33cc54ca5b18-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-f997c995a333b4ae-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-16686d3e3ddcc21b-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |

### S011

- Gold evidence: 6; final Top10 hits: 0; final Top5 hits: 0
- Parsed / Chunk / Embedding / BM25: 6/6/6/6
- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.
- First failure point: `canonical_a2_retrieval_trace_unavailable`; root cause: `UNRESOLVED`.

| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |
|---|---:|---|---|---|---|---|---|
| `cchunk-pymupdf-v1-acca8dbfb1b95f8f-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-bf2be6315d2f187b-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-ac2c48838803419d-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-cc1f6fd20cdb46f6-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-91e5666cf6078fb9-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |
| `cchunk-pymupdf-v1-93807a18f7b7345f-000` | 1 / FULL | FULL | True / True | unavailable | unavailable | unavailable | Top10=False, Top5=False |

## 6. Final Decision

`TRACE_INCOMPLETE`. A parser/chunk/index root cause is not supported by the frozen artifacts. The next safe diagnostic is to capture a canonical A2 retrieval trace under the existing trace contract; no retrieval, chunking, or ranking change is justified by this audit.

## Trace Limitations

- Phase 13D-2 includes complete trace fields, but its arms are Multi-query A3.1 rather than frozen A2 and cannot be substituted for A2 attribution.
- Phase 13E-0 captured A2-like stages but is explicitly noncanonical because its live replay drifted from the frozen A2 final metrics. It is retained as historical context only and is not used for this classification.
