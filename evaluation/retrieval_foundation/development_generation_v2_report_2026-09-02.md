# Frozen Development Generation V2

## 最终状态：`READY_FOR_AB`

本阶段仅完成 Frozen Development Generation V2 和 Development 标签兼容性审计，**未执行 A0/A1/A2**，因此不报告 Recall/MRR。

## 1. 真实 source corpus

| 文件 | 大小 | SHA256 | 历史 document ID | 可读 |
|---|---:|---|---|---|
| `2196-ANSI-Manual-Chinese.pdf` | 1,561,387 | `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` | `doc-4ffb6df91a9a` | 是 |
| `t1739cn.pdf` | 4,532,306 | `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae` | `doc-6a9ea3ff1f42` | 是 |

仅使用 `D:/基于Lightrag的工业手册问答系统/lightrag_industry_qa_portfolio/data/manuals`；未修改 PDF，未访问 Validation/Holdout。

## 2. Frozen Generation

- Generation ID：`dev-v2-20260902`
- 构建时间：`2026-09-02T09:12:03.413312+00:00`
- 构建代码 commit：`b3b732157e599349cac90e3d08e35e3d070e8a04`
- corpus fingerprint：`ed7bf1da6aab63a7afcf88c21480c6e987d231b9de65de2dd0977ce4b4e60e68`
- child fingerprint：`b1a6334f6e725591e5d796f318e76d3eb77c46fa8c8cc3f6342dc850ad77d1e0`
- parent fingerprint：`4a576db34d65ae270efdc5ca3694c8a32f5a7f1bb5e3416f1ee27d93ab9b5bb1`
- lexical index fingerprint：`6a1fd370918362422d7539f60265cf3992e06f1f9ddd5c69d9e8545c3d7f1c13`
- lexical index bytes SHA256：`5f87e21f73085e3949d10685d61e4b8ed931ab945c2933de2f1baebc23862d58`
- retrieval config fingerprint：`f83052a79e36befbf0c88c9639ab0ea9f95a62a54acf0a07cd477cd55310aecd`
- parent chunks：447
- child chunks：453

快照位于独立目录 `evaluation/retrieval_foundation/dev_generation_v2/retrieval/`，包含 `parent_chunks.jsonl`、`child_chunks.jsonl`、`chunk_manifest.json`、`lexical_index.json`。child→parent 引用全部有效，chunk identity 唯一。

## 3. LightRAG 与隔离性

- LightRAG workspace：已 populated；包含非空 graph、text store、entity/relation store 和向量文件，可重新加载。
- LightRAG text store 中可定位全部 453 个冻结 child ID，确认 workspace 与同一 chunk universe 建立关联。
- 独立 SQLite：`evaluation/retrieval_foundation/dev_generation_v2/generation.db`
- `FrozenGeneration.load` reload：通过，二次加载 fingerprint 一致。
- active `.run/industrial_rag.db`：未写入；V2 不依赖 mutable `current/` alias。
- A0/A1/A2 将共享此 Generation、chunk universe、Development dataset、evidence labels 和 identity rules。

## 4. Development split 与标签审计

Development question 数量为 6，实际 ID：`S014, S015, S006, S003, S016, S011`。loader 对非 Development split 硬失败；本阶段未读取 Validation/Holdout。

标签审计结果见 [dev_label_audit_v2.md](dev_label_audit_v2.md) 和 [dev_label_audit_v2.json](dev_label_audit_v2.json)：6 题全部为 `equivalent`，置信度 0.85，依据是同 document/page 且 evidence text identical。映射不依据 retrieval rank，未修改 golden labels。

## 5. 环境与验证

- Python：3.11.9
- executable：当前 worktree `.venv\\Scripts\\python.exe`
- base prefix：`C:/Users/mming/AppData/Local/Programs/Python/Python311`
- pip：26.2.1
- worktree：`D:/基于Lightrag的工业手册问答系统/lightrag_industry_qa_portfolio-retrieval-foundation-upgrade`
- branch：`codex/retrieval-foundation-upgrade`
- focused contract tests：`7 passed`
- generation artifact focused tests：`2 passed`（新增 slots ParentChunk 回归测试）
- ruff：`All checks passed`

本阶段业务改动仅为：支持 slots dataclass ParentChunk 的冻结序列化，以及两个直接脚本的 `src` 导入入口；`frontend/package-lock.json` 是任务前已有 modification，`.venv_broken_worktree/` 是环境恢复产生的 untracked 目录，均未触碰。

## 6. 门禁结论

Generation contracts、LightRAG populated/reload、lexical index 校验、Development split guard 和 6 条标签映射均通过，因此状态为 `READY_FOR_AB`。本阶段仍禁止运行 A0/A1/A2；下一阶段才可执行现有 A/B runner。
