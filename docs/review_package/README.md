# Selective Architecture Review Package

## Purpose

This is a compact, static review snapshot of the Industrial LightRAG QA
system. It is intended for architecture review: understanding the live
ingestion/query path, evaluation contracts, current constraints, and technical
debt without downloading PDFs, vector indexes, model credentials, experiment
artifacts, or a complete repository clone.

The source-of-truth manifest is [`../code-review-index.md`](../code-review-index.md).
Snapshots retain their repository-relative layout beneath this directory.

## Current architecture

```text
PDF
  -> PyMuPDF parser (optional MinerU adapter)
  -> Parent / Child chunks with page and document identity
  -> Generation-scoped indexes (LightRAG + lexical BM25; NanoVectorDB or Qdrant)
  -> Hybrid retrieval
  -> Reciprocal Rank Fusion
  -> optional provider reranker runtime
  -> evidence policy / parent context hydration
  -> LLM generation
  -> claim grounding and citation projection
  -> answer, citations, and protected retrieval trace
```

The HTTP entry point is `runtime_core/industrial_rag/api.py`. It creates the
runtime and lifecycle services; `lightrag_service.py` owns the answer path;
the `services/` snapshot contains parser/indexing, BM25/RRF/rerank, generation
identity, and query application services.

## Package layout

| Directory | Contents |
| --- | --- |
| `architecture/` | Selected architecture, lifecycle, parent-child, and retrieval/evaluation design documents |
| `config/` | Non-secret configuration templates and provider/integration contracts |
| `runtime_core/` | Current production/runtime Python modules, preserving `industrial_rag/` paths |
| `evaluation_core/` | Reusable evaluators, trace contract, artifact schema, and Development-set contract |
| `tests/` | Focused parser, retrieval, reranker, grounding, and evaluation tests |
| `current-status.md` | Completed capabilities, known risks, and work not yet productized |

## Review boundary

This package deliberately does **not** contain any PDF, parsed output, golden
dataset, vector database, embedding cache, Qdrant snapshot, model key,
`.env`, virtual environment, frontend dependency directory, lockfile, or
historical experiment artifact. It is not runnable in isolation and does not
replace the repository.
