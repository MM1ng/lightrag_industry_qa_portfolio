# QA Downstream Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish an auditable 24-question QA downstream baseline using the frozen A2 retrieval path and the project's real Retrieval → Evidence Selection → Answer → Citation pipeline.

**Architecture:** Perform a fail-closed identity preflight against the frozen Development dataset and Generation V2. Reuse the production query application, retrieval trace, parent-child context expansion, structured answer/citation, grounding, and refusal behavior; add only a thin evaluation adapter for per-question artifacts, metric aggregation, stratification, and failure attribution.

**Tech Stack:** Python, existing FastAPI/service layer, pytest, ruff, JSON/JSONL, Markdown audit report.

**Spec:** User request in the current task message.

## Global Constraints

- Use only Development V2, 24 questions, fingerprint `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`.
- Use only Generation `dev-v2-20260902` and frozen A2: LightRAG + BM25 + RRF + `qwen3-rerank`.
- Do not modify retrieval parameters, Generation, dataset, labels, or evidence mapping.
- Do not access Validation/Holdout, run retrieval tuning, or automatically repair QA failures.
- On any identity mismatch, stop with `BLOCKED_EXPERIMENT_INTEGRITY`.
- Full QA is valid only through the existing formal QA path; no evaluation-only answer pipeline.

### Task 1: Map and lock the formal QA path

**Files:**
- Inspect: `src/industrial_rag/services/query_application_service.py`
- Inspect: `src/industrial_rag/structured_citation_output.py`
- Inspect: `src/industrial_rag/answer_grounding.py`
- Inspect: `src/industrial_rag/phase10_evaluation.py`
- Inspect: `evaluation/phase10/`

- [ ] Confirm the callable production entrypoint, trace fields, answer status values, citation schema, and existing metric semantics.
- [ ] Confirm no Validation/Holdout dataset or mutable `current/` Generation is imported by the runner.
- [ ] Record the selected interfaces in the runner's experiment metadata.

### Task 2: Add fail-closed QA evaluation adapter and tests

**Files:**
- Create: `scripts/run_formal_qa_downstream_evaluation.py`
- Create or modify: `src/industrial_rag/services/qa_downstream_evaluation.py`
- Test: `tests/test_qa_downstream_evaluation_contracts.py`

**Interfaces:**
- Consumes: frozen dataset/mapping, A2 retrieval output, production QA service response and trace.
- Produces: `evaluate_qa_case(...)`, `aggregate_qa_metrics(...)`, `attribute_failure(...)`, and a JSON-serializable per-question chain.

- [ ] Write contract tests for identity checks, citation precision/recall, refusal classification, multi-evidence accounting, and primary/secondary failure causes.
- [ ] Implement the smallest adapter that preserves production response and trace fields verbatim.
- [ ] Fail closed on dataset, Generation, split, evidence, or trace identity mismatch.
- [ ] Add JSON and Markdown output writers with aggregate, stratified, per-question, multi-evidence, taxonomy, and root-cause sections.

### Task 3: Run the single formal 24-question evaluation

**Files:**
- Generate: `evaluation/retrieval_foundation/qa_downstream_development_2026-09-03.json`
- Generate: `evaluation/retrieval_foundation/qa_downstream_development_2026-09-03.md`

- [ ] Run preflight and verify every integrity check is true before any QA call.
- [ ] Execute all 24 questions through the real A2 QA path exactly once, retaining retrieval ranks, selected context, answer claims, citations, refusal status, and traces.
- [ ] Do not run A0/A1, tune retrieval, access Validation/Holdout, or run Full QA outside this controlled Development evaluation.
- [ ] If a correctness bug is found, stop, add a regression test, minimally fix it, and restart all 24 questions from the beginning.

### Task 4: Verify and report the baseline

**Files:**
- Inspect: generated JSON and Markdown outputs

- [ ] Assert 24 complete per-question records, valid trace identity, computable metrics, and allowed final status.
- [ ] Run focused QA/evaluator tests and `ruff check .`.
- [ ] Document historical metrics only as directional when dataset or semantics differ.
- [ ] Answer the eight required decision questions, including whether Retrieval optimization should stop and which downstream modules should be prioritized next.

**Status gate:** Emit `QA_BASELINE_ESTABLISHED` only when all 24 cases completed with reliable metrics and trace-based attribution; otherwise emit `BLOCKED_EXPERIMENT_INTEGRITY` or `BLOCKED_QA_RUNTIME` as specified.
