# Expanded Development Evaluation Set Report

最终状态：`READY_FOR_EFFECTIVENESS_EVAL`

## Dataset summary

- 总题数：24
- 新增题数：18
- 保留历史题：6（S014、S015、S006、S003、S016、S011）
- split：Development only
- Generation：`dev-v2-20260902`
- Dataset fingerprint：`deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`

## Source coverage

| PDF | document ID | questions |
|---|---|---:|
| 2196-ANSI-Manual-Chinese.pdf | doc-4ffb6df91a9a | 15 |
| t1739cn.pdf | doc-6a9ea3ff1f42 | 9 |

## Question type distribution

| Type | Count |
|---|---:|
| parameter | 4 |
| procedure | 3 |
| installation_debugging | 2 |
| fault_handling | 3 |
| safety_warning_limit | 3 |
| maintenance | 5 |
| component_structure | 2 |
| condition_prerequisite | 2 |

## Difficulty distribution

- EASY：5
- MEDIUM：11
- HARD：8

## Evidence distribution

- Single evidence：16
- Multi evidence：8
- Table/structured：3
- Adjacent chunk：1
- Evidence mapping completeness：24/24
- Maximum evidence reuse：2

## Duplicate audit

- Semantic/exact duplicate question pairs：0
- Template-like duplicate findings：0
- Evidence over-concentration：通过（最大复用次数 2）
- Question ID uniqueness：通过
- Existing six-question overlap audit：通过；六条历史题均原样保留并可追溯至原 question IDs

## Contract and guard status

- Child IDs exist in Frozen Generation：通过
- Child → parent mapping：通过
- Source document identity：通过
- Evidence text/page metadata：通过
- Dataset fingerprint reproducible：通过
- A0/A1/A2：未运行本阶段正式评估
- Validation/Holdout：未访问
- Frozen Generation：未修改
- Retrieval parameters：未修改
- Full QA downstream：未执行

## Frozen artifacts

- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_coverage.md`
- `evaluation/retrieval_foundation/retrieval_foundation_dev_v2_audit.md`

