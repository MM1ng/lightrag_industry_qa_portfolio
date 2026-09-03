# Expanded Development Evaluation Set Design

**Goal:** 建立一个只依赖 `dev-v2-20260902`、包含现有 6 题且总量 20–30 题的固定 Development retrieval evaluation dataset，并在不运行 A0/A1/A2 的前提下完成 evidence、coverage、duplicate 与数据契约审计。

## Scope and invariants

- 唯一 corpus 是 `evaluation/retrieval_foundation/dev_generation_v2` 对应的两份真实工业手册。
- 旧题 S014、S015、S006、S003、S016、S011 原样保留，question ID、问题和已有 golden label 不修改；仅通过显式 legacy trace/mapping 纳入新 schema。
- 新题从 Frozen Generation 的 child/parent 快照人工独立抽取，先完成问题与 evidence 标注，再冻结 dataset；不读取任何新题的 retrieval ranking。
- 不运行 A0/A1/A2，不访问 Validation/Holdout，不改 generation、retrieval 参数或 Full QA 状态。

## Data model

主数据文件为 JSONL，每行一个 question，包含 `question_id`、`question`、`split`、`source_document_id`、`question_type`、`difficulty`、`evidence_pattern`、`expected_child_chunk_ids`、`expected_parent_chunk_ids` 和 `evidence`。`evidence` 为完整 evidence item 数组，每项保存 child/parent ID、文本、页码、section/location 及是否必要。parent IDs 必须由 Frozen Generation 的 child→parent 关系推导并校验。

Evidence mapping 单独输出为 JSON，按 question 保存 source identity、child→parent pairs、文本 fingerprint 和 location，便于人工审阅和后续 evaluator 使用。旧题通过已有 `dev_label_audit_v2.json` 中的 EQUIVALENT 映射追溯到 V2 chunk，不改历史原始 label 文件。

## Dataset construction and audit

先建立独立的 18 条新增候选，按两份 PDF、题型、难度和 evidence pattern 配额覆盖；候选均从 evidence-first 记录生成，禁止由 retrieval 命中情况筛选。生成器/validator 只读取冻结 child/parent JSONL 和 generation metadata，不接触 A/B runner。

审计包括：规范化问题的 exact/semantic duplicate（使用本地 token/Jaccard 规则，不调用外部模型）、evidence fingerprint 重复集中度、模板相似度、与旧 6 题重叠、ID 唯一性、source/document/page、child 存在性、parent 映射完整性和 split。低质量候选从最终 dataset 排除并在 Markdown 中记录原因；不得为了达到数量保留重复题。

Coverage report 统计 source、question type、difficulty、single/multi evidence、table/structured evidence、adjacent-chunk evidence，并给出质量 gate 判定。总量目标为 24 题，最低 gate 为 20 题；两份 PDF 各至少 8 题，题型与难度均不得单一集中。

## Freeze and reproducibility

冻结产物为：

- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_coverage.md`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_audit.md`

Dataset fingerprint 对 JSONL 的 canonical JSON（UTF-8、排序稳定、无空白差异）做 SHA-256；manifest 同时记录 generation ID、child/parent/lexical fingerprints、source documents、question IDs、counts 和 process guard flags。任何 A0/A1/A2、Validation/Holdout、generation mutation 或 retrieval parameter mutation 都会令本阶段 gate 失败，而不是被隐式忽略。

## Testing

先以 failing tests 锁定 schema、generation membership、parent mapping、fingerprint repeatability、split 与 guard behavior，再实现 validator/audit。focused pytest 覆盖新增契约；最后运行 `ruff check .`。本阶段不运行 retrieval evaluator、A/B runner 或 Full QA。

