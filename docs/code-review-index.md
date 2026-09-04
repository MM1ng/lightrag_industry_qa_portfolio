# Core Code Review Index

This index is the selective architecture-review manifest for the current
`dev/retrieval-foundation-qa-downstream` runtime. The companion
[`review_package/`](review_package/README.md) contains static copies of the
listed files for review; it is not a deployable distribution or a repository
backup.

## 1. Runtime Core

| File | Role | Core path |
| --- | --- | --- |
| `src/industrial_rag/api.py` | FastAPI application, query API, lifecycle wiring | Yes |
| `src/industrial_rag/config.py` | Provider, storage, model, and runtime settings | Yes |
| `src/industrial_rag/runtime.py` | Synchronous API-to-LightRAG runtime bridge | Yes |
| `src/industrial_rag/lightrag_service.py` | Query orchestration, grounding gate, generation, and evidence response | Yes |
| `src/industrial_rag/document_parser.py` | PyMuPDF document parsing and source metadata | Yes |
| `src/industrial_rag/mineru_client.py` | Optional remote MinerU parser adapter; disabled by default | Yes, optional |
| `src/industrial_rag/structured_chunker.py` | Parent–Child chunk construction | Yes |
| `src/industrial_rag/parent_chunk_store.py`, `runtime_chunk_hydration.py` | Parent/child persistence and context hydration | Yes |
| `src/industrial_rag/services/parse_service.py`, `ingestion_pipeline.py`, `index_service.py` | Parse, ingestion, and index lifecycle | Yes |
| `src/industrial_rag/services/lexical_retrieval.py`, `retrieval_fusion.py`, `rrf_fusion.py` | BM25, hybrid candidate fusion, and RRF | Yes |
| `src/industrial_rag/services/reranker_runtime.py`, `reranker_runtime_adapter.py` | Provider-neutral rerank execution and DashScope adapters | Yes |
| `src/industrial_rag/retrieval_trace.py`, `services/retrieval_trace_service.py` | Saved retrieval lineage and protected diagnostic lookup | Yes |
| `src/industrial_rag/evidence_policy.py`, `citation_selection.py`, `answer_grounding.py` | Evidence filtering, citation selection, and claim grounding | Yes |
| `src/industrial_rag/citation_formatter.py`, `claim_citation_pruning.py`, `structured_citation_output.py` | Citation identity projection and output validation | Yes |
| `src/industrial_rag/services/query_application_service.py` | KB/generation-scoped query application service | Yes |
| `src/industrial_rag/services/generation_artifacts.py`, `generation_fingerprint_service.py` | Frozen Generation identity and artifact lookup | Yes |
| `src/industrial_rag/vector_collections.py`, `services/qdrant_collection_service.py` | Nano/Qdrant backend and generation-scoped collections | Yes |
| `src/industrial_rag/db/`, `repositories/` (selected files) | Runtime persistence for KB, documents, generations, updates, and traces | Yes |
| `src/industrial_rag/conversation/query_rewriter.py` | Bounded-history standalone query rewrite | Yes |

The snapshot also includes direct support modules imported by this chain:
`auth.py`, `errors.py`, `safety_policy.py`, `kb_runtime_settings.py`,
`observability.py`, `operational_metrics.py`, `evidence_*`,
`structured_generation_*`, `conditional_completion.py`, and
`post_retrieval_recovery.py`.

## 2. Evaluation Core

| File | Role | Included |
| --- | --- | --- |
| `src/industrial_rag/evaluation.py` | General retrieval/citation evaluation primitives | Yes |
| `src/industrial_rag/services/retrieval_evaluation.py` | Frozen Development Recall/MRR/Hit/Complete semantics | Yes |
| `src/industrial_rag/services/retrieval_ab_evaluation.py` | Formal retrieval evaluation runner contract | Yes |
| `src/industrial_rag/services/evaluation_trace_contract.py` | Per-stage evidence trace and offline metric replay | Yes |
| `src/industrial_rag/services/canonical_evaluation_artifact_v2.py` | v2 artifact identity, trace schema, validation, and replay | Yes |
| `src/industrial_rag/services/expanded_development_dataset.py` | Development-set contract and frozen labels | Yes |
| `src/industrial_rag/services/golden_set_policy.py` | Gold-label policy and split guard | Yes |
| `src/industrial_rag/services/qa_downstream_evaluation.py` | Retrieval-to-citation downstream evaluator | Yes |

One-off Phase 13/14 scripts and historical JSON artifacts are intentionally
excluded. They document experiments, not the reusable evaluation framework.

## 3. Configuration

| File | Purpose |
| --- | --- |
| `.env.example` | Required and optional environment variables; contains no secrets |
| `pyproject.toml` | Python/runtime and development dependency contract |
| `environment.lightrag.yml` | LightRAG environment configuration |
| `config/lightrag_contract.json` | Explicit LightRAG integration contract |
| `src/industrial_rag/config.py` | Runtime parsing and validation of environment configuration |

Key model/provider inputs are `DASHSCOPE_API_KEY`, `LLM_BASE_URL`,
`LLM_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, and the reranker settings in
the frozen evaluation/runtime configuration. The package carries only the
template, never an actual `.env` file or credentials.

## 4. Focused Tests

| Test area | Included tests |
| --- | --- |
| Parser and chunks | `test_document_parser.py`, `test_structured_chunker.py`, `test_parent_chunk_store.py`, `test_parent_expansion.py` |
| Hybrid retrieval | `test_lightrag_service.py`, `test_lexical_retrieval.py`, `test_rrf_fusion.py`, `test_retrieval_evaluation.py` |
| Reranker | `test_rerank.py`, `test_reranker_runtime.py` |
| Grounding/citations | `test_citation_formatter.py`, `test_evidence_policy.py`, `test_qa_downstream_evaluation_contracts.py` |
| Evaluation contracts | `test_evaluation.py`, `test_evaluation_trace_contract.py`, `test_canonical_evaluation_artifact_v2.py`, `test_expanded_development_dataset_contract.py` |

## Exclusions

The package excludes `scripts/run_phase*_*.py`, all `evaluation/` result
artifacts, PDFs, JSONL datasets, vector/index stores, `data/`, cache
directories, `.venv*`, `node_modules`, `__pycache__`, lockfiles, and frontend
dependency output. It also excludes unrelated worktree changes such as
`frontend/package-lock.json`.
