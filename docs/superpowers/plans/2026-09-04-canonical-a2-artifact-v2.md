# Canonical A2 Artifact Schema v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a trace-complete, immutable canonical A2 evaluation artifact contract that can validate identity and replay the existing retrieval metrics offline.

**Architecture:** A new service module owns v2 artifact construction, validation, legacy-v1 inspection, and replay. It uses the existing evaluation trace metric implementation unchanged, while a read-only audit script documents why the historical v1 artifact remains authoritative but cannot be promoted to a trace-complete v2 artifact.

**Tech Stack:** Python 3.11, pytest, Ruff, existing `industrial_rag.services.evaluation_trace_contract`.

**Spec:** `docs/phase-14b2-dv2-011-drift-localization.md`

## Global Constraints

- Do not change retrieval, chunking, embeddings, reranker behavior, or evaluator metric semantics.
- Do not overwrite or modify the historical canonical A2 JSON artifact.
- Do not call retrieval, reranking, embedding, or external providers.
- Use `.venv\\Scripts\\python.exe` for Python, pytest, and Ruff commands.
- Exclude `frontend/package-lock.json` and `.venv_broken_worktree/` from commits.

---

### Task 1: Add contract tests before implementation

**Files:**
- Create: `tests/test_canonical_evaluation_artifact_v2.py`
- Create: `src/industrial_rag/services/canonical_evaluation_artifact_v2.py`

**Interfaces:**
- Produces `build_canonical_artifact_v2`, `validate_canonical_artifact_v2`, `replay_canonical_artifact_v2`, and `inspect_legacy_canonical_artifact`.

- [ ] **Step 1: Write failing tests for a trace-complete artifact, strict identity validation, offline replay, and legacy-v1 immutability.**

- [ ] **Step 2: Run the focused test file and confirm the import fails before implementation.**

- [ ] **Step 3: Implement the minimal v2 schema contract.**

- [ ] **Step 4: Run the focused tests and confirm they pass.**

### Task 2: Add a read-only schema audit and report

**Files:**
- Create: `scripts/run_phase14c0_canonical_artifact_v2_audit.py`
- Create: `docs/phase-14c0-canonical-artifact-v2.md`

**Interfaces:**
- Consumes the historical canonical A2 v1 JSON and the identity contract.
- Produces a read-only JSON audit and Markdown report; never emits a replacement canonical result.

- [ ] **Step 1: Write failing tests for read-only legacy inspection and report identity assertions.**

- [ ] **Step 2: Run the focused tests and confirm the missing audit interface fails.**

- [ ] **Step 3: Implement the minimal audit script and generate the report.**

- [ ] **Step 4: Run focused tests, then Ruff.**

### Task 3: Verify and commit

**Files:**
- Modify: only files created in Tasks 1 and 2.

- [ ] **Step 1: Re-read the phase requirements and compare them with the implementation.**

- [ ] **Step 2: Run the complete focused contract and audit test set with the project virtual environment.**

- [ ] **Step 3: Run `.venv\\Scripts\\python.exe -m ruff check .` and inspect `git diff`/`git status`.**

- [ ] **Step 4: Commit only Phase 14C-0 files.**
