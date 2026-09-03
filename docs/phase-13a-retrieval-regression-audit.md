# Phase 13A — Retrieval Regression Audit

**最终状态：`INCONCLUSIVE`**  
**审计范围：仅冻结的 Development V2，未访问 Validation / Final / Holdout；未运行新的 A0/A1/A2。**

## 1. 实验身份

| 项目 | 当前值 |
|---|---|
| Branch | `dev/retrieval-foundation-qa-downstream` |
| HEAD | `5b97fbd feat: establish QA downstream evaluation baseline` |
| Dataset | `retrieval_foundation_dev_v2.jsonl` |
| Questions | 24（旧 6 题 ID 全部保留） |
| Dataset fingerprint | `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060` |
| Generation | `dev-v2-20260902` |
| Corpus fingerprint | `ed7bf1da6aab63a7afcf88c21480c6e987d231b9de65de2dd0977ce4b4e60e68` |
| A2 | LightRAG + BM25 + RRF + `qwen3-rerank` |
| Saved artifact reused | `evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json` |

Identity checks passed: 24/24 question IDs、dataset fingerprint、Generation ID、corpus/child-manifest identity 均与保存结果一致；split 仍为 Development only。

## 2. A2：同一结果、同一 evaluator 的 @5 / @10

| Metric | @5 | @10 | Delta |
|---|---:|---:|---:|
| Recall | 0.818 | 0.831 | +0.014 |
| MRR | 0.894 | 0.894 | +0.000 |
| Question Hit | 1.000 | 1.000 | +0.000 |
| Complete Evidence Coverage | 0.750 | 0.750 | +0.000 |
| Multi-evidence Complete | 0.143 (1/7) | 0.143 (1/7) | +0.000 |

说明：保存的正式 retrieval evaluator 将 multi-evidence 分层计为 7 题；冻结 JSONL 中共有 8 条多证据标注，其中 2 条新增题完整命中，6 条历史多证据题未完整命中。该分层口径差异已保留，不能把 1/7 与 2/8 混写成同一统计量。

### @5 未完整、但 @10 完整

无（0 题）。因此没有证据表明把最终 cutoff 从 5 扩展到 10 能修复当前 multi-evidence completeness。

### @10 仍未完整覆盖的问题及必要证据 rank

以下使用冻结 JSONL 的全部多证据标注和已保存 A2 排名；`MISS` 表示未进入保存的 Top10 候选结果。

| Question | 必要 evidence 的 A2 rank |
|---|---|
| S014 | `17f477…:1`; `93807…:MISS` |
| S015 | `34cc49…:MISS`; `598985…:MISS`; `5ea5c4…:1`; `78a156…:MISS`; `f997c9…:7` |
| S006 | `5388c5…:MISS`; `a03be0…:MISS`; `d8638f…:MISS`; `e2d711…:1`; `fcc2e6…:2` |
| S003 | `6590f0…:MISS`; `663e64…:MISS`; `87557f…:MISS`; `8ac7c8…:5`; `c97eb4…:MISS` |
| S016 | `16686d…:MISS`; `317e33…:MISS`; `58f11d…:1`; `99121c…:MISS`; `f997c9…:MISS` |
| S011 | `2db1a5…:6`; `91e566…:MISS`; `93807a…:MISS`; `a94c71…:1`; `ac2c48…:MISS`; `acca8d…:MISS`; `bf2be6…:MISS`; `cc1f6f…:MISS` |

另外两条冻结多证据题已在 @5 完整覆盖：D-V2-001（ranks 2, 1）和 D-V2-012（ranks 3, 1）。

## 3. Old vs New：可比性审计

旧仓库 `MM1ng/lightrag_industry_qa` 的远端可访问（HEAD `63dbcd75f779cff6bb68a5ef903f3a8d3958e378`），但没有直接运行其业务代码，也没有修改旧仓库。

| 维度 | Old `lightrag_industry_qa` | Current portfolio / A2 | 同口径结论 |
|---|---|---|---|
| Dataset / gold | Phase 10 历史集（文档显示 64 题，development 36；另有 12 题 holdout artifacts） | 固定 24 题、fingerprint 固定、child-level gold | 不同，不能严格 delta |
| Generation / corpus | 历史 Development KB；未发现 `dev-v2-20260902` identity | Frozen Generation V2，453 children / 447 parents | 不同 |
| candidate / final TopK | `chunk_top_k=20`，业务 QA `top_k=12` | audit artifact `candidate_top_n=20`，`final_top_k=10` | 不同 |
| Dense / LightRAG | LightRAG naive/mix 路径，无当前 A2 的完整配置 | LightRAG candidate retrieval | 不能直接比较排名 |
| BM25 tokenizer | 无 generation-scoped BM25 pipeline | CJK runs + contiguous CJK subterms；保留 manual identifiers | 能确认能力变化，不能归因退化 |
| RRF | 无 RRF | `rrf_k=60`，按 rank 融合并去重 | 新增能力 |
| dedup | 历史 LightRAG/selection 逻辑；无当前 RRF dedup 语义 | 每个 source 首次 child ID 生效，融合后 canonical child ID | 语义不同 |
| parent-child | 有历史 parent-child 代码/实验，但非同一 Frozen Generation mapping | 当前 child→parent mapping 与 frozen registry 校验通过 | mapping 不同，不能直接比较 |
| reranker pool / top_n | `rerank_enabled=false`；无 provider score | qwen3-rerank，输入 candidate pool 20，最终 top 10 | Old 没有对应 baseline |
| evidence / gold matching | 历史 `expected_evidence[].chunk_id` / mapping | frozen `expected_child_chunk_ids` exact child matching | 口径不同 |

结论：旧仓库没有满足“同 24 题 + 同 gold + 同 Generation + 同 evaluator + 同 TopK”的可运行基线。其历史 `MRR 0.5269 / Chunk Recall@20 0.6970` 等数字只能作为 `historical directional reference`，不能宣称当前 A2 相对旧仓库发生真实 regression。

## 4. 根因排序

### 1. B — 新 Development Set / metric 口径更严格

这是目前最有证据的解释。当前集包含 8 条多证据标注，并要求全部必要 child evidence 命中；旧报告使用不同数据规模、Generation、TopK 和历史 evidence semantics。单证据题当前 A2 complete coverage 为 13/13，而主要缺口集中在历史多证据题，说明总体低分不能直接解释为全局检索退化。

### 2. C — candidate recall 本身不足（主要限于多证据完整性）

多证据题有大量必要 child 在保存的 A2 Top10 中直接 `MISS`；Multi-evidence Complete @10 仍为 0.143，且 @5→@10 没有改善。这是当前确实存在的 retrieval-side risk，但尚不足以证明相对于旧仓库的 regression。

### 3. D — final TopK evidence competition / diversity

有局部证据：部分题的一个必要 child 位于 rank 6/7，而另一个必要 child 位于 rank 1/2；因此 @5 cutoff 会造成局部损失。但整体 Recall 仅从 0.818 增至 0.831，Complete Coverage 和 Multi-evidence Complete 均不变，不能把问题主要归因于 Top5 vs Top10 cutoff。

### 4. A — 主要是 Top5 vs Top10 cutoff

不支持作为主要根因：不存在“@5 不完整、@10 完整”的题，且 MRR、Question Hit、Complete Coverage 均无变化。

### 5. F — 暂无足够证据证明真实 regression

这是跨仓库结论的最终限定。旧仓库没有同口径可比运行结果；因此 E（新仓库真实 retrieval regression）不能成立，只能保持未证实状态。

## 5. 决策

- **真实 retrieval regression：未证实。** 当前审计结果为 `INCONCLUSIVE`，原因是旧仓库无法在同一 24 题、Generation、gold、evaluator、TopK 下运行。
- **主要问题：** 新集的严格 multi-evidence completeness 暴露了 candidate recall / evidence diversity 风险；不是一个已被证实的全局退化。
- **下一阶段唯一推荐：`EVIDENCE_DIVERSITY`。** 理由是 @10 相比 @5 没有修复完整覆盖，当前应先针对候选内多证据保留/竞争做诊断；本阶段不引入 Multi-query，不开始 Phase 13B。
- **Validation/Holdout：** 未访问、未运行。
- **A0/A1/A2：** 本阶段未重新运行；仅复用了冻结的正式 A2 结果 artifact。

