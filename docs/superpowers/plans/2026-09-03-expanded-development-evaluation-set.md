# Expanded Development Evaluation Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 Frozen Generation `dev-v2-20260902` 建立并冻结 20–30 题的 Development-only retrieval evaluation dataset，完成 evidence mapping、coverage、duplicate audit 与 dataset gate。

**Architecture:** 复用现有 `FrozenGeneration`/generation artifact loader 与 `dev_label_audit_v2.json` 的历史映射，只新增一个离线 dataset contract/audit 模块和固定产物。问题与证据先作为静态 JSONL 冻结，validator 只读 generation snapshot，完全不依赖 retrieval ranking 或 A/B runner。

**Tech Stack:** Python 3.11, JSON/JSONL, hashlib, pytest, ruff；不引入新 retrieval 组件或外部服务。

**Spec:** `docs/superpowers/specs/2026-09-03-expanded-development-evaluation-set-design.md`

## Global Constraints

- 唯一 corpus 是 `dev-v2-20260902` 的两份真实工业手册。
- 旧 6 题及 question ID/golden labels/evidence mapping 不修改。
- 不运行 A0、A1、A2，不查看新题 retrieval ranking。
- 不访问 Validation/Holdout，不修改 Frozen Generation、retrieval 参数或 Full QA。
- 总题数 20–30，且 split 只能是 Development。

### Task 1: Lock the dataset contract with failing tests

**Files:**
- Create: `tests/test_expanded_development_dataset_contract.py`
- Create: `src/industrial_rag/services/expanded_development_dataset.py`

**Interfaces:**
- Produces `load_generation_snapshot(path)`, `validate_dataset(cases, snapshot)`, `canonical_dataset_fingerprint(cases)`, `audit_dataset(cases, snapshot, legacy_ids)`, and `build_manifest(...)`.
- Consumes existing Frozen Generation retrieval JSONL and generation metadata only.

- [ ] **Step 1: Write failing tests** for required fields, unique IDs, `split == "development"`, non-empty evidence, and exact old question IDs.
- [ ] **Step 2: Run `\.venv\Scripts\pytest.exe -q tests/test_expanded_development_dataset_contract.py`** and verify failure is caused by the missing module/contract behavior.
- [ ] **Step 3: Add minimal immutable dataclasses/loader** for child and parent records, preserving source/page/content metadata.
- [ ] **Step 4: Run the focused test again** and verify the schema tests pass.

### Task 2: Add generation membership and mapping validation

**Files:**
- Modify: `tests/test_expanded_development_dataset_contract.py`
- Modify: `src/industrial_rag/services/expanded_development_dataset.py`

**Interfaces:**
- `validate_dataset` rejects missing child IDs, wrong source document IDs, invalid child→parent mappings, parent IDs not in the generation, and evidence text/page mismatches.

- [ ] **Step 1: Add failing tests** for each invalid membership/mapping case and for a valid multi-evidence case.
- [ ] **Step 2: Run only these tests** and confirm each fails for the expected validation reason.
- [ ] **Step 3: Implement deterministic child→parent and evidence metadata checks** against `dev_generation_v2/retrieval/child_chunks.jsonl` and `parent_chunks.jsonl`.
- [ ] **Step 4: Run focused contract tests** and verify all pass.

### Task 3: Add fingerprint, duplicate, concentration and coverage audit

**Files:**
- Modify: `tests/test_expanded_development_dataset_contract.py`
- Modify: `src/industrial_rag/services/expanded_development_dataset.py`

**Interfaces:**
- `canonical_dataset_fingerprint` is repeatable across JSON serialization order.
- `audit_dataset` returns duplicate pairs, template concentration, evidence reuse counts, source/type/difficulty/pattern coverage, and gate failures.

- [ ] **Step 1: Add failing tests** for fingerprint repeatability, exact/semantic duplicate detection, evidence over-concentration, and coverage counts.
- [ ] **Step 2: Verify RED** with the focused pytest command.
- [ ] **Step 3: Implement normalized text tokens/Jaccard audit and stable canonical JSON hashing** with no external model or retrieval call.
- [ ] **Step 4: Run focused tests** and verify audit output is deterministic.

### Task 4: Freeze the expanded dataset and reports

**Files:**
- Create: `evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl`
- Create: `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json`
- Create: `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json`
- Create: `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_coverage.md`
- Create: `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_audit.md`
- Create: `scripts/build_expanded_development_dataset.py`
- Modify: `tests/test_expanded_development_dataset_contract.py`

**Interfaces:**
- The builder accepts explicit dataset JSONL and generation path, writes only the five named artifacts, and fails closed if an A/B/Validation/Holdout guard marker is present.

- [ ] **Step 1: Add a failing CLI contract test** for output files, manifest fingerprint, 24-question count target, legacy traceability, and final status.
- [ ] **Step 2: Verify RED** without the builder/artifacts.
- [ ] **Step 3: Independently annotate 18 new questions from snapshot evidence** across both PDFs, using stable new IDs (`D-V2-001` …) and recording all necessary evidence.
- [ ] **Step 4: Run the builder** to generate mapping, manifest and reports; do not invoke any retrieval command.
- [ ] **Step 5: Run the builder a second time** and confirm identical dataset fingerprint and report counts.

### Task 5: Add dataset gate and repository verification

**Files:**
- Modify: `tests/test_expanded_development_dataset_contract.py`
- Create: `docs/retrieval-foundation-expanded-development-report.md`

- [ ] **Step 1: Add failing tests** that the gate is `BLOCKED_DATASET_QUALITY` below 20 questions or with any invariant failure, and `READY_FOR_EFFECTIVENESS_EVAL` only when all required conditions hold.
- [ ] **Step 2: Implement the gate and final report** with explicit A0/A1/A2 status, Validation/Holdout access status, generation mutation status, coverage, duplicates and completeness.
- [ ] **Step 3: Run focused dataset tests, the dataset builder, and inspect output JSON/Markdown manually.**
- [ ] **Step 4: Run `\.venv\Scripts\ruff.exe check .`** and record the fresh result.
- [ ] **Step 5: Prove no formal retrieval evaluation ran** by checking the generated report/manifest guard fields and not invoking any A/B runner.

## Explicitly out of scope

Do not modify `src/industrial_rag/lightrag_service.py`, BM25/RRF/reranker code, retrieval parameters, Frozen Generation artifacts, Validation/Holdout data, or Full QA evaluation.

