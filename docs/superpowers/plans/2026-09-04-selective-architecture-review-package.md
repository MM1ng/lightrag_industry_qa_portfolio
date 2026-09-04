# Selective Architecture Review Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a compact, reviewable snapshot of the current runtime and evaluation architecture without copying artifacts, experiments, or environment files.

**Architecture:** `docs/code-review-index.md` is the authoritative manifest. `docs/review_package/` is a static, selective copy of the current runtime modules, core evaluation contracts, their focused tests, and concise architecture/status documentation; it contains no executable changes to the application.

**Tech Stack:** Markdown and source/test file copies only.

**Spec:** User task “Prepare Selective Architecture Review Package”.

## Global Constraints

- Do not change business logic, code behavior, or production configuration.
- Include only current runtime/evaluation code and the focused tests that review it.
- Exclude experiment scripts, artifacts, PDFs, caches, environments, dependencies, and `frontend/package-lock.json`.
- Preserve the user’s unrelated `frontend/package-lock.json` and `.venv_broken_worktree/` changes.
- Commit only review-package files and push the requested branch.

---

### Task 1: Define the review manifest

**Files:**
- Create: `docs/code-review-index.md`

**Interfaces:**
- Produces the definitive file inventory for the review package and a clear exclusion policy.

- [ ] **Step 1: Trace FastAPI, ingestion, retrieval, rerank, grounding/citation, and evaluation imports.**
- [ ] **Step 2: Select only modules called by those paths plus identity/artifact replay contracts.**
- [ ] **Step 3: Document configuration and focused test coverage in tables.**

### Task 2: Assemble static review package

**Files:**
- Create: `docs/review_package/README.md`
- Create: `docs/review_package/current-status.md`
- Create: `docs/review_package/runtime_core/**`
- Create: `docs/review_package/evaluation_core/**`
- Create: `docs/review_package/tests/**`
- Create: `docs/review_package/architecture/**`

**Interfaces:**
- Consumes the manifest from Task 1.
- Produces a portable review-only directory with source and test snapshots, no data/artifacts/dependencies.

- [ ] **Step 1: Copy manifest-selected runtime, evaluation, and test files preserving paths.**
- [ ] **Step 2: Write the README architecture flow and file-map.**
- [ ] **Step 3: Write current state, known risks, and explicitly deferred product work.**

### Task 3: Verify, commit, and push

**Files:**
- Modify: only Task 1 and 2 documentation/snapshots.

- [ ] **Step 1: Scan package files for forbidden suffixes, directories, and large files.**
- [ ] **Step 2: Run a documentation/package manifest check and `git status`; confirm unrelated local changes remain unstaged.**
- [ ] **Step 3: Commit only package files with `docs: prepare selective architecture review package`.**
- [ ] **Step 4: Push `dev/retrieval-foundation-qa-downstream` to its configured remote.**
