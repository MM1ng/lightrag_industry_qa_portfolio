# Retrieval Foundation Upgrade — Next Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已存在的 Sparse/BM25、RRF、Reranker 和 Trace 基础能力接入实际查询链路，并用离线评测验证工业型号、零件号和参数查询收益。

**Architecture:** 保持 LightRAG 原生 `mix` 行为作为一个 retrieval source；并发执行 LightRAG 与 generation-scoped BM25，使用 provenance-preserving RRF 形成候选集，再执行可超时、可回退的 rerank。Evidence selection、Parent/Adjacent expansion、Citation 和 Generation contract 保持不变。

**Tech Stack:** Python, asyncio, FastAPI, pytest, existing LightRAG backend, generation-scoped JSON lexical artifact。不得引入 LangChain、LangGraph、Pi Agent、Neo4j 或新的全文基础设施。

**Spec:** 当前 Retrieval Foundation V2 设计、P0 generation consistency invariant，以及用户提供的“标准 RAG 教程”启发。

## Global Constraints

- 不修改 LightRAG graph extraction 或原生 graph 行为。
- 不修改 public API contract；请求仍只接受现有 query/history 字段。
- 所有 retrieval source 必须使用同一 generation-scoped ChildChunk snapshot。
- Reranker provider 不可用、失败或超时不得导致 QA 请求失败。
- Parent-Child retrieval、Citation、Evidence、Generation、Feedback、Graph UI 必须保持兼容。
- 不删除现有 feature flags；新增开关必须默认关闭或保持当前默认行为。

---

### Task 1: Lock the integration contract with failing tests

**Files:**
- Modify: `tests/test_lightrag_service.py`
- Create: `tests/test_hybrid_query_integration.py`
- Modify: `tests/test_retrieval_trace_v2.py`

**Interfaces:**
- Consumes: `QueryOptions`, `QueryResult`, `HybridRetriever`, `RerankerRuntime`, `RetrievalExecutionTrace`。
- Produces: tests specifying source provenance, duplicate chunk collapse, fallback ordering, and selected/rejected trace fields。

- [ ] **Step 1: Write failing tests** for a query that returns the same chunk from LightRAG and BM25, asserting one fused candidate with both contributions.
- [ ] **Step 2: Write failing tests** asserting reranker timeout/provider failure returns RRF order and records fallback metadata.
- [ ] **Step 3: Write failing tests** asserting final evidence trace contains `dense/lightRAG`, `sparse`, `rrf`, `rerank`, `selected`, and `rejected_reason` fields.
- [ ] **Step 4: Run targeted tests** with `.\.venv\Scripts\pytest.exe -q tests/test_hybrid_query_integration.py tests/test_retrieval_trace_v2.py` and verify the new integration tests fail for missing wiring.
- [ ] **Step 5: Commit** `test: define hybrid retrieval integration contract`.

### Task 2: Add generation-scoped lexical runtime loading

**Files:**
- Modify: `src/industrial_rag/services/lexical_retrieval.py`
- Modify: `src/industrial_rag/services/generation_artifacts.py`
- Modify: `src/industrial_rag/runtime.py`
- Create: `tests/test_generation_lexical_runtime.py`

**Interfaces:**
- Consumes: validated `generation/retrieval/lexical_index.json`, expected `child_manifest_hash`。
- Produces: a cached per-generation lexical index whose search rows hydrate to the same `ChunkRegistry` identities.

- [ ] **Step 1: Add failing tests** for loading a valid index, rejecting manifest mismatch, and returning exact hits for `2196-R`, `ANSI B15.1`, `ISO VG 68`, and `0.005`.
- [ ] **Step 2: Implement the smallest loader/cache** behind the existing `GenerationArtifactResolver`; never read mutable `parsed/.../current` at query time.
- [ ] **Step 3: Add duplicate and same-document tests** proving one `chunk_id` identity is retained while document/page metadata remains available.
- [ ] **Step 4: Run `pytest -q tests/test_generation_lexical_runtime.py tests/test_generation_retrieval_artifacts.py`**.
- [ ] **Step 5: Commit** `feat: load generation lexical indexes at runtime`.

### Task 3: Wire concurrent LightRAG + Sparse retrieval and RRF

**Files:**
- Modify: `src/industrial_rag/lightrag_service.py`
- Modify: `src/industrial_rag/services/retrieval_fusion.py`
- Modify: `src/industrial_rag/config.py`
- Create: `tests/test_hybrid_query_integration.py` (extend)

**Interfaces:**
- Consumes: normalized query, `ChunkRegistry`, generation lexical index, existing `reciprocal_rank_fusion`。
- Produces: fused candidate list with `source`, `original_rank`, `original_score`, and `rrf_score`.

- [ ] **Step 1: Add failing test** asserting both retrieval coroutines run through `asyncio.gather`, while lexical search is executed via `asyncio.to_thread`.
- [ ] **Step 2: Add a feature-flagged integration path** preserving current LightRAG-only behavior when disabled.
- [ ] **Step 3: Replace only the initial candidate input to evidence selection**; keep parent expansion and supplemental retrieval semantics unchanged.
- [ ] **Step 4: Add config validation for RRF `k`, sparse top-k, and feature defaults without changing public request fields.
- [ ] **Step 5: Run focused integration and existing LightRAG tests**.
- [ ] **Step 6: Commit** `feat: integrate sparse retrieval with rrf`.

### Task 4: Wire fail-safe reranker after fusion

**Files:**
- Modify: `src/industrial_rag/lightrag_service.py`
- Modify: `src/industrial_rag/services/reranker_runtime.py`
- Modify: `src/industrial_rag/retrieval_trace.py`
- Create: `tests/test_reranker_integration.py`

**Interfaces:**
- Consumes: RRF candidate TopN and normalized query。
- Produces: final TopK ordering plus `rerank_enabled`, provider, latency, candidate/final counts, and fallback reason。

- [ ] **Step 1: Write failing tests** for successful rerank, timeout, provider exception, empty provider result, and disabled mode.
- [ ] **Step 2: Implement runtime invocation with one bounded timeout** and deterministic RRF fallback.
- [ ] **Step 3: Ensure reranker receives child text/metadata only and cannot change chunk identity.
- [ ] **Step 4: Feed final ordering into existing Evidence Selection without changing citation IDs.
- [ ] **Step 5: Run `pytest -q tests/test_reranker_integration.py tests/test_reranker_runtime.py tests/test_lightrag_service.py`.
- [ ] **Step 6: Commit** `feat: apply fail-safe reranking to fused candidates`.

### Task 5: Complete explainable retrieval trace

**Files:**
- Modify: `src/industrial_rag/retrieval_trace.py`
- Modify: `src/industrial_rag/lightrag_service.py`
- Modify: `src/industrial_rag/services/answer_feedback_service.py`
- Modify: `tests/test_retrieval_trace_v2.py`

**Interfaces:**
- Consumes: per-source ranks/scores, RRF contributions, rerank output, evidence decision。
- Produces: a stable trace explaining why each final chunk entered or failed to enter the prompt.

- [ ] **Step 1: Add failing contract tests** for selected and rejected candidates, including duplicate collapse and rejection reasons.
- [ ] **Step 2: Populate trace fields at each stage without overwriting earlier provenance.
- [ ] **Step 3: Keep API response backward compatible; expose extended details through existing trace/diagnostic structures.
- [ ] **Step 4: Run trace, feedback, and API contract tests.
- [ ] **Step 5: Commit** `feat: record end-to-end retrieval provenance`.

### Task 6: Add development-only offline retrieval evaluation

**Files:**
- Create: `src/industrial_rag/services/retrieval_evaluation.py`
- Create: `tests/test_retrieval_evaluation.py`
- Create: `evaluation/retrieval_foundation/dev_cases.jsonl`
- Modify: `README.md` or the existing evaluation documentation

**Interfaces:**
- Consumes: labeled development queries with `question_type` and relevant `chunk_id` values.
- Produces: Recall@5/10 and MRR@5/10 for LightRAG baseline, LightRAG+Sparse+RRF, and the reranked candidate.

- [ ] **Step 1: Define JSONL schema** covering semantic, model, part-number, parameter, fault, and procedure queries.
- [ ] **Step 2: Write failing metric tests** for duplicate results, missing relevant chunks, and rank cutoffs.
- [ ] **Step 3: Implement deterministic evaluator** with no validation/holdout tuning.
- [ ] **Step 4: Add a CLI or documented Python entry point** that runs only against development data.
- [ ] **Step 5: Record a baseline report before tuning any weights or thresholds.
- [ ] **Step 6: Commit** `feat: add development retrieval evaluation`.

### Task 7: Full verification and rollout gate

**Files:**
- Modify: `docs/` with a short rollout/runbook if needed
- No business-code changes unless verification exposes a defect

- [ ] **Step 1: Run focused suites for lexical, RRF, reranker, trace, generation artifacts, and API contracts.
- [ ] **Step 2: Run `ruff check .` and the full pytest suite; document the pre-existing Ragas dependency blocker separately if it remains.
- [ ] **Step 3: Verify feature-disabled behavior produces the same LightRAG result ordering and public response shape.
- [ ] **Step 4: Verify generation rollback loads only generation-scoped snapshot/index/registry artifacts.
- [ ] **Step 5: Perform manual UI smoke test with an uploaded development manual.
- [ ] **Step 6: Request code review before merge; do not merge into the dirty main workspace automatically.

## Risks and rollback

- **Risk:** Sparse or reranker integration changes answer evidence ordering. **Mitigation:** feature flags, baseline comparison, and deterministic RRF fallback.
- **Risk:** Snapshot/index mismatch. **Mitigation:** resolver validation; disable sparse and fail generation readiness rather than merging mismatched chunks.
- **Rollback:** disable the new retrieval flag to restore LightRAG-only behavior; retain frozen generation artifacts and public contracts.
