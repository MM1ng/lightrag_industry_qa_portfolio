# Phase15-B 统一文档生命周期流水线——最终验收报告

**验收日期：** 2026-09-04
**验收范围：** Phase15-B 第 1–5 步的最终验收。
**提交范围：** `2ec75af` 至 `ea699e0`。

## 1. 执行摘要

Phase15-B 将文档生命周期操作统一到持久化的 `UpdateJob` 流水线。五种受支持操作——`add`、`replace`、`delete`、`reparse` 和 `reindex`——都会构建隔离的 Candidate Generation；在 canonical validation 成功且显式执行 Promote 前，线上服务中的 Active Generation 保持不变。

`reparse` 和 `reindex` 现已是持久化的 `UpdateJob` 操作。遗留的 `LifecycleTask` 文档处理器仅作为兼容性适配器：它们创建并执行 `UpdateJob`，但不具备激活或 Promote Generation 的权限。本次最终验收未新增产品能力，也未修改生产代码。

## 2. 改造前后架构

| 关注点 | Phase15-B 前 | Phase15-B 后 |
| --- | --- | --- |
| reparse/reindex 契约 | 未作为持久化 `UpdateJob` 操作表示 | 增加 `UpdateOperation.reparse` 和 `UpdateOperation.reindex`，支持 repository 创建、查询与恢复 |
| 文档生命周期入口 | 遗留 `LifecycleTask` 路径可能直接进入解析或索引行为 | 生命周期处理器统一收敛到 `UpdateJob` 与 Candidate 构建 |
| Candidate 隔离 | reparse/reindex 未纳入通用 Candidate 契约 | 两者均创建独立 `VectorIndexGeneration`；构建和验证期间 Active 不变 |
| 发布权限 | 遗留文档路径可能与索引/发布职责混淆 | 文档发布受 Validation 和 `promote_generation` 约束；`IndexService` 必须显式指定后端迁移目标 |
| 并发安全 | 已有 Phase9 Lease 保护 | reparse/reindex 复用同一套带 fencing 的 Candidate 与 Promote 路径 |

## 3. 生命周期流程

所有文档操作遵循以下安全边界：

```text
add | replace | delete | reparse | reindex
                    |
                    v
              UpdateJob（持久化意图）
                    |
                    v
       IncrementalUpdateService.execute_job
                    |
                    v
   Candidate VectorIndexGeneration（隔离）
                    |
                    v
       GenerationValidationService / Validation Gate
                    |
                    v
      promote_generation + KBLeaseService fencing
                    |
                    v
          KnowledgeBase.active_vector_generation_id
```

`execute_job` 仅构建 Candidate，不执行验证或发布。处于 `building` 的 Candidate 无法 Promote；处于 `ready` 的 Candidate 仍必须具备当前有效的 canonical validation evidence。Active Generation 指针只能通过带 fencing 的 Promote 转换改变。

操作范围保持明确：

- `reparse` 必须携带 `document_id`，用于为单个文档重建 Candidate 解析/分块产物。
- `reindex` 仅使用当前 KnowledgeBase 的活动文档快照构建 Candidate 索引，不改变 parser、chunking 或 embedding 配置。

## 4. 已变更组件

| 组件 | Phase15-B 职责 |
| --- | --- |
| `UpdateOperation` / migration | 以向后兼容方式增加 `reparse`、`reindex`，并增加 reparse 必须提供 document_id 的约束 |
| `UpdateJobRepository` | 持久化、查询并恢复新增操作类型 |
| `DocumentService` 与生命周期处理器 | 在保留兼容性的同时，为 reparse/reindex 创建并执行 UpdateJob |
| `IncrementalUpdateService` | 在不重写服务的前提下扩展 Candidate 构建入口 |
| `IndexService` | 将直接索引限定为显式向量后端迁移，不再承担文档生命周期发布 |
| Phase15-B 测试 | 覆盖操作持久化、处理器收敛、Candidate 隔离、验证、Promote 与 fencing |

## 5. 安全保证

- **禁止未验证发布：** 处于 `building` 状态的 Candidate 会被 Promote 拒绝；`ready` Candidate 必须通过 `ValidationGateService.require_eligible`。
- **失败隔离：** 验证失败会将 Candidate 与其 Job 标记为失败，不改变 Active Generation 指针。
- **证据绑定：** Promote 会重新校验 canonical validation evidence 是否仍与冻结的 Generation 产物、文档注册表、Qdrant 内容、策略和内容 epoch 一致。
- **原子发布：** `KBLeaseService.switch_active_generation` 使用当前 Lease 与 fencing token 保护指针 compare-and-set、Generation 状态变更和 generation epoch。
- **拒绝陈旧写入：** 已过期的 reindex Lease 无法在新 Promote 后恢复旧的 Active Generation。
- **保留兼容而不保留权限：** `LifecycleTask` 仍可用于创建、查询状态和恢复兼容，但不拥有文档发布权限。

## 6. 测试证据

以下命令均于 2026-09-04 在 Phase15-B worktree 内执行。

| 命令 / 测试集 | 结果 | 覆盖证据 |
| --- | --- | --- |
| `pytest tests/test_phase15b_unified_document_lifecycle.py -v` | 26 passed | UpdateJob 契约、遗留处理器收敛、reparse/reindex Candidate 构建、验证门禁、Promote 与陈旧 Lease fencing |
| `pytest tests/test_phase9.py -v` | 24 passed | add/replace/delete Candidate 生命周期、验证失败隔离、Promote、回滚、重启恢复、快照完整性和并发 |
| `pytest tests/test_phase9b_validation_gate.py -v` | 8 passed | canonical validation evidence、artifact/Qdrant 篡改检测及必需验证 |
| `pytest tests/test_phase9b_job_recovery.py -v` | 4 passed | 原子 claim、陈旧 worker 拒绝、恢复与 KB Lease 绑定 |
| `pytest tests/test_phase9b_multi_instance.py -v` | 10 passed | 多实例运行时行为、Candidate 隔离及 Promote/rollback 传播 |
| `ruff check src tests` | passed | 源码和测试的静态检查 |

请求的组合 Phase9 命令也已启动。桌面执行通道存在 30 秒输出截止，因此最终结果通过将同样的四个指定文件分别运行获得；合计 46 项测试通过。

## 7. 已知限制

- 为兼容 `LifecycleTask`，生命周期执行仍为同步模式；Phase15-B 按约束未引入 Async Worker。
- reindex 仅针对当前 KB 快照重建 Candidate 索引；不执行 parser、chunking 或 embedding 升级。
- `LifecycleTask` 表和兼容 API 仍被保留。它们仅用于适配与恢复，不再构成第二套文档发布状态机。
- 验收测试对 Qdrant 和 canonical runner 使用离线测试替身。正式发布前仍需要使用真实向量后端、生产 validation endpoint 和可观测回滚演练执行 operator-run canary。
- Retrieval、Evaluation、Parser 算法、Embedding 策略和 Frontend 均明确不在 Phase15-B 范围内。

## 8. 对 Phase15-C 的建议

建议在受控部署演练确认生产依赖环境中的 validation evidence 与带 fencing 的 Promote 行为后，再进入 Phase15-C。必须保持 Phase15-B 的不变量：所有文档变更均由 `UpdateJob` 持久化；Candidate 在验证前保持隔离；Active Generation 仅可通过 Promote 路径变更。任何新的 worker、parser/configuration 升级或可观测性增强，都应作为独立阶段定义和验收，不应混入已完成的生命周期对齐范围。
