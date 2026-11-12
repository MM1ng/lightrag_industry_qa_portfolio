# Phase 13B Multi-query Candidate Recall Ablation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run an offline A3 multi-query candidate-recall ablation against the frozen 24-question Development Set while preserving A2 and performing exactly one final rerank per question.

**Architecture:** Add a small offline-only module that accepts deterministic query variants, invokes the existing LightRAG and BM25 candidate paths per variant, unions and deduplicates candidates, applies the existing RRF, then invokes the existing qwen3-rerank once on the fused pool. The runner reuses the saved A2 artifact for baseline metrics and computes A3 metrics plus per-question missing-gold recovery and rank provenance.

**Tech Stack:** Python, asyncio, existing `FrozenGeneration`, `BM25Index`, `DashScopeRuntimeAdapter`, `RerankerRuntime`, existing ranking evaluator, pytest, ruff.

**Spec:** User-approved Phase 13B request in the current task.

## Global Constraints

- A2 behavior and all retrieval parameters remain frozen.
- Only the frozen 24-question Development Set and `dev-v2-20260902` may be used.
- No Validation / Holdout access, no production QA integration, no LangChain/MCP/LangGraph, and no PinRAG clone.
- A3 keeps the original query, adds 2–3 industrial-document query variants, performs one union/dedup/RRF before one reranker call.
- Do not stage `frontend/package-lock.json` or `.venv_broken_worktree/`.

---

### Task 1: Define and test the offline multi-query candidate pipeline

**Files:**
- Create: `src/industrial_rag/services/multi_query_ablation.py`
- Create: `tests/test_multi_query_ablation.py`

**Interfaces:**
- Consumes: frozen child IDs, variant query strings, per-query dense retrieval callable, BM25 index, and the existing RRF/reranker primitives.
- Produces: `QueryVariant`, `A3CandidateRun`, and `run_a3_candidates(...)` returning union/fusion provenance, final ranked rows, per-query latency, and exactly one reranker invocation.

- [ ] **Step 1: Write the failing tests**

  Test that the original query is retained, variant retrievals are called, duplicate child IDs are removed before fusion, missing-gold provenance records the variant and first pre-rerank rank, and the injected reranker is called once per question.

- [ ] **Step 2: Run the focused test to verify the expected failure**

  Run: `pytest -q tests/test_multi_query_ablation.py`

  Expected: FAIL because `multi_query_ablation` does not yet exist.

- [ ] **Step 3: Implement the minimal offline pipeline**

  Implement a pure orchestration module. For each query string, call dense retrieval and BM25 with the frozen candidate limit; union rows by canonical `child_chunk_id`; retain `retrieved_by_queries`, original source ranks, and first appearance; run existing `reciprocal_rank_fusion` over the union source rows; call `RerankerRuntime.rerank` exactly once over the fused candidate pool; return ranked A3 rows and provenance. Do not alter `LightRAGService` or settings.

- [ ] **Step 4: Run the focused test to verify it passes**

  Run: `pytest -q tests/test_multi_query_ablation.py`

  Expected: all tests PASS, including the single-rerank assertion.

### Task 2: Build the offline A3 experiment runner and metrics

**Files:**
- Create: `scripts/run_phase13b_multi_query_ablation.py`
- Create: `tests/test_phase13b_multi_query_runner.py`

**Interfaces:**
- Consumes: the frozen dataset, generation snapshot, saved Phase 13A/A2 artifact, existing LightRAG backend, existing BM25 index, and existing DashScope reranker adapter.
- Produces: `evaluation/retrieval_foundation/phase13b_multi_query_ablation_2026-09-03.json` and the Markdown report.

- [ ] **Step 1: Write failing contract tests**

  Test that preflight rejects a non-Development split or mismatched dataset fingerprint, the six Phase 13A miss IDs are selected from saved A2 evidence, and aggregate metrics include all requested A2/A3 fields without changing A2 data.

- [ ] **Step 2: Run the focused tests to verify failure**

  Run: `pytest -q tests/test_phase13b_multi_query_runner.py`

  Expected: FAIL because the runner module and report contract do not exist.

- [ ] **Step 3: Implement minimal runner and deterministic variant generation**

  Load and validate the exact frozen dataset/generation fingerprints. Generate at most three industrial retrieval angles per question through the existing configured generation provider, validate the returned variant list, and preserve the original query. Run A3 only offline; use the current LightRAG backend for each query, the frozen BM25 index, `rrf_k=60`, candidate pool 20, final top 10, and one `qwen3-rerank` call. Persist query text, variant identity, candidate count, first rank, and source provenance.

- [ ] **Step 4: Implement A2-vs-A3 metrics and six-miss analysis**

  Reuse `evaluate_rankings` semantics for Recall, MRR, Question Hit, and Complete Evidence Coverage. Add Multi-evidence Complete using the frozen expected child IDs. For each of the six Phase 13A @10-incomplete questions, record every missing gold ID, whether A3 recovered it, the query variant that first recovered it, pre-rerank first rank, final @5/@10 membership, and whether A3 caused a question-level regression.

- [ ] **Step 5: Run the focused tests to verify pass**

  Run: `pytest -q tests/test_multi_query_ablation.py tests/test_phase13b_multi_query_runner.py`

  Expected: all tests PASS without any network/model invocation from the unit tests.

### Task 3: Execute A3 once and write the audit report

**Files:**
- Create: `docs/phase-13b-multi-query-ablation.md`
- Create: `evaluation/retrieval_foundation/phase13b_multi_query_ablation_2026-09-03.json`

**Interfaces:**
- Consumes: the tested offline runner and frozen artifacts.
- Produces: machine-readable A2/A3 results and an auditable Markdown decision with one final status.

- [ ] **Step 1: Run the offline A3 experiment**

  Run the runner with explicit frozen dataset, manifest, mapping, generation, and saved A2 artifact paths. Confirm no Validation/Holdout path is opened and no production QA entrypoint is invoked.

- [ ] **Step 2: Verify report contents**

  Check 24 questions, A2/A3 requested metrics, average query count, average candidate count, latency, regression count, and the six-question recovery table. Apply the decision rules exactly: `MULTI_QUERY_PROMISING`, `PASS_TO_EVIDENCE_DIVERSITY`, or `MULTI_QUERY_INEFFECTIVE`.

- [ ] **Step 3: Run verification commands**

  Run: `pytest -q tests/test_multi_query_ablation.py tests/test_phase13b_multi_query_runner.py`

  Run: `ruff check .`

  Run: `git diff --check` and verify only intended files are staged.

- [ ] **Step 4: Commit and push only intended files**

  Stage only the offline module, runner, tests, JSON result, Markdown report, and this plan if not already committed. Do not stage `frontend/package-lock.json` or `.venv_broken_worktree/`.

