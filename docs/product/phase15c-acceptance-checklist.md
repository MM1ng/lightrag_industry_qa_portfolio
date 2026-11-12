# Phase15-C Acceptance Checklist

| 项目 | 内容 |
| --- | --- |
| Product / Phase | Industrial Knowledge Assistant Platform / Phase15-C Knowledge Update Runtime |
| Version / Date | 1.0 / 2026-09-06 |
| Requirements | [Phase15-C PRD v1.1](phase15c-knowledge-update-runtime-prd.md) |
| Code baseline | `dev/retrieval-foundation-qa-downstream` / `0686940bd41989e47805a38f17eeab2c8aa6aade` |
| Status | Not Executed；本次只编写文档，未开始 C1 实现，未运行测试、解析、模型、Embedding、检索或迁移 |

## 1. 使用与判定规则

本清单将 PRD 验收条件转换为后续执行记录。所有复选框初始未勾选；文档存在、代码核查或历史 Phase15-B 测试通过，均不代表 Phase15-C 验收通过。

每项实际验收需记录：实现 commit、运行环境、输入/负载、执行人及日期、测试命令或演练步骤、期望/实际结果、日志/报告位置、结论与缺陷编号。状态采用 Not Executed / Passed / Failed / Blocked；只有证据完整且实际符合预期才勾选。PRD 所列必做项不得通过标记 N/A 绕过，P0 失败必须停止进入下一阶段。

## 2. 代码基线核查结论

- `IncrementalUpdateService._parse_document_pymupdf` 将 parent/child chunks 写入共享 `parsed/documents/{doc_id}/current`；`_candidate_snapshot_pairs` 及 `load_child_chunks` / `load_parent_chunks` 读取该位置。当前尚无 per-attempt parsed staging 契约，隔离最终 Candidate 和向量集合不能消除此风险。
- `ParseService` 另有临时 parse 目录切换到 current 的兼容路径。需要核对每条实际可达写入和读取链路，不能把临时目录名当作新 attempt 输入隔离的证据。
- `IndexService.index_knowledge_base` 要求显式 target_backend；`handle_migrate_to_qdrant` 传入 Qdrant 目标；`handle_rollback_to_nano` 按 Nano 指纹等既有条件检查后激活。PRD 的文档发布 sole-authority 规则不得全局禁止这些路径。
- 本次仅核查这些代码事实，不判定当前实现已经满足下述新增验收，也不扩大或重写维护安全模型。精确代码定位见 PRD §1.3。

## 3. 需求验收总表

详细给定条件与期望以 PRD §17 为准。以下所有项目均未执行。

| 完成 | ID | 验收摘要 | 必需证据 |
| --- | --- | --- | --- |
| [ ] | AC-01 | 五类操作在构建替身解除阻塞前返回 202 + job_id | 提交时间、响应及构建阻塞记录 |
| [ ] | AC-02 | Worker 暂停时任务持久 PENDING、Active 不变 | Job 行、无构建副作用、Active ID/epoch |
| [ ] | AC-03 | API 重启和 Worker 启动后可从持久输入领取 | 重启记录、源引用、attempt |
| [ ] | AC-04 | 双 Worker 单一有效 claim，业务服务不二次领取 | 两方领取结果、owner/token 与调用计数 |
| [ ] | AC-05 | 同 KB 准入和 Legacy 提交无双 writer | 并发提交结果、同 Job 幂等/新意图 busy |
| [ ] | AC-06 | KB A 忙不阻塞可执行 KB B | 轮询及 B 的领取时间 |
| [ ] | AC-07 | 多个崩溃点及启动后到期均可恢复 | 故障点、过期时间、周期扫描与恢复结果 |
| [ ] | AC-08 | stale worker success/failure/checkpoint/元数据提交均拒绝 | 旧/新 owner、拒绝结果、状态未覆盖 |
| [ ] | AC-09 | 重试/恢复独立 Candidate workspace/vector namespace | attempt 与物理产物映射、旧候选不可发布证据 |
| [ ] | AC-10 | 取消先申请、后安全确认，无强杀或自动发布 | 请求/确认时间、安全 checkpoint、Active |
| [ ] | AC-11 | 构建 SUCCEEDED 仍待验证，不切换 Active | 双状态、冻结结果、Active ID/epoch |
| [ ] | AC-12 | 显式验证失败不重写构建成功或 Active | 验证结果、双状态、拒绝构建 retry |
| [ ] | AC-13 | 五类文档新内容仅通过既有验证及 Promote 发布 | Gate/fencing 正反向用例；作用域见 §5 |
| [ ] | AC-14 | 后台构建期间查询仍读旧 Active | 查询 Generation 关联及候选隔离 |
| [ ] | AC-15 | 既有 Phase15-B/Phase9 相关回归通过 | 本阶段新生成的完整回归报告 |
| [ ] | AC-16 | 阶段、最后失败阶段、attempt、双状态可查询 | 阶段 checkpoint 与 API 响应对照 |
| [ ] | AC-17 | 长阶段期间独立心跳和取消可提交，无长 DB 事务 | 多周期心跳、事务边界与取消落库时间 |
| [ ] | AC-18 | 错误分类、持久退避、次数耗尽终态正确 | 重启前后 next_run_at、attempt/max_attempts |
| [ ] | AC-19 | 并发 retry/网络重放只重排队一次 | 请求标识、同一失败 attempt、计数与排队结果 |
| [ ] | AC-20 | 取消/成功/发布不同先后顺序结果确定 | 各竞态执行序列及无取消后发布证据 |
| [ ] | AC-21 | 125 条静态数据真实分页、筛选及 total | 50/50/25 页结果、状态过滤、KB 边界 |
| [ ] | AC-22 | 提交幂等与文件/DB 持久化失败契约 | 同键同/异输入响应、无错误接受记录 |
| [ ] | AC-23 | 历史状态按证据回填、12×6 矩阵正确 | 全部生命周期样本及合法/非法组合检查 |
| [ ] | AC-24 | 管理员权限、KB 归属及错误脱敏正确 | 正反向授权结果、无凭据/路径/堆栈泄漏 |
| [ ] | AC-25 | 停止领取、排空/隔离及回退无隐式 Active 回滚 | 停机顺序、保留 Job/Candidate、无双执行 |
| [ ] | AC-26 | 部分 Candidate/文件存在不误判成功 | 部分产物、缺失完成证据、对账/重建结果 |
| [ ] | AC-27 | stale base 不静默 rebase 或覆盖新发布 | 基准/当前 Active、冲突及拒绝结果 |
| [ ] | AC-28 | 完成后释放 Lease/响应失败不重放构建 | 完成提交事实、残留 Lease 处置 |
| [ ] | AC-29 | 旧解析 Worker 失权后不能污染新 attempt 解析输入 | §4 的双 attempt 故障注入及产物/引用摘要 |
| [ ] | AC-30 | 半成品、未登记或迟到解析结果不被消费 | §4 的崩溃/取消用例、消费者实际输入引用 |
| [ ] | AC-31 | 文档无发布旁路，显式迁移/维护正反向行为保留 | §5 的分别隔离验证结果 |

## 4. C1 P0 Parsed Artifact Staging and Isolation

对应 PRD FR-14a、AC-29/30。此为 Runtime isolation 验收，不要求也不允许借此升级 Parser/Chunk/Embedding 算法。

### 4.1 契约检查

- [ ] 每次尝试的 parsed staging 绑定 KB、job_id、attempt、document/source version；A/B 物理目录不同，路径无需采用本清单指定格式。
- [ ] 父块、子块以及同次解析消费的其他产物均受隔离；不能只隔离向量集合或最终 Candidate。
- [ ] staging 完整性和输入/配置指纹核验完成后，才通过短事务登记不可变 parsed artifact 引用。
- [ ] Candidate snapshot、Embedding 与索引读取显式绑定本 attempt 引用；不在消费时回退或重新查找共享 current。
- [ ] 当前有效 owner 才能登记引用和提交 Document 解析元数据；失权旧 Worker 无法通过异常/成功分支覆盖。
- [ ] 进行中的后台 attempt 不直接写共享 `parsed/documents/{doc_id}/current`；保留兼容读取不等于允许旧 swap。
- [ ] 维护/兼容消费者不会读取未完成 staging；如确需兼容投影，其完整产物来源与发布权限已明确，不能以无条件 current 替换实现。
- [ ] Parser/Chunk 配置、算法及模型保持原有契约，变化限定在产物位置、引用绑定和执行权限。

### 4.2 AC-29 失权解析写入演练

1. 为同一源版本创建 attempt A；在解析产物写入前、写入中、旧 current swap 前分别设置可控停顿点。
2. 使 A 的有效 ownership 失效，由受控恢复取得 B；B 使用独立 staging，并生成可区分的父/子块标记或内容摘要。
3. 记录 B 文件摘要、parsed 引用、候选输入摘要和 Document 元数据，再允许 A 继续完成。
4. 观察 A 只能写旧隔离产物；B 文件/引用/元数据不得改变，B 消费的父/子块不得混入 A 内容。
5. 检查共享 current 未被后台 attempt 改写；仅证明 Active 不变不算通过，因为新 Candidate 输入也必须受保护。
6. 对 add/replace 复用解析链路执行对应参数化检查。后续实际实现不再存在旧 swap 时，验证该路径确已不可达，并保留读取绑定断言。

### 4.3 AC-30 不完整及迟到产物演练

- [ ] staging 写一半崩溃：不登记完整引用，后续消费者不读取半成品，恢复使用新 staging。
- [ ] 文件完成但 checkpoint 提交前崩溃：不凭“文件存在”自动认为本 attempt 已有效完成；核对凭据或隔离重建。
- [ ] 取消或失权后解析返回：拒绝登记新引用/元数据；不能转为 SUCCEEDED 或替换 current。
- [ ] 现有 Active 的冻结解析/索引快照及保留的兼容数据未被污染。
- [ ] 产物清理仍沿既有受控 GC，不清除 Active、回滚或其他有效 attempt 的依赖。

**Stop gate**：若旧 A 能修改 B 的解析文件/引用/元数据，或 B 仍隐式消费共享 current，则 C1 isolation 不通过；不得进入 C2，不能用“Parser 不在本期”豁免。

## 5. Document Promote Authority and Maintenance Compatibility

对应 PRD §3、FR-17、AC-13/31。Promote sole authority 仅适用于五类文档操作的新内容发布，不是全仓库所有 Generation activate 的禁令。显式后端迁移/维护保留已有入口与前置校验，不因此获得代替文档发布的权限。

### 5.1 五类文档路径负向验收

- [ ] add/replace/delete/reparse/reindex 均只构建候选，不自动 Validation/Promote。
- [ ] 文档 Legacy handler 不调用直接 activate，不把文档更新包装成迁移任务绕过 Gate。
- [ ] 未验证、取消、失权、superseded Candidate 不能经文档 Promote 上线。
- [ ] 文档候选只有显式验证通过并满足现有 Gate/fencing 才可发布；旧 Active 的观察应排除同时运行的显式维护操作，避免误判。

### 5.2 显式迁移和维护正反向验收

- [ ] 现有 `migrate_to_qdrant` 在原有有效输入及依赖条件满足时仍能通过显式 target_backend 路径工作，未被全局 activate 拦截或强制转成 document UpdateJob。
- [ ] `IndexService.index_knowledge_base` 缺少显式迁移目标时仍拒绝，不能重新成为文档生命周期通用索引入口。
- [ ] 现有 `rollback_to_nano` 在 Nano 产物及原有指纹条件满足时仍能执行原行为；缺失或陈旧 Nano 输入仍被原校验拒绝。
- [ ] 原有显式 Generation rollback 行为另行保持，不与后台构建取消混淆。
- [ ] 不全局删除/屏蔽 `VectorIndexGenerationRepository.activate`，不误删维护 handler，也不要求所有维护任务进入文档双状态矩阵。
- [ ] 兼容回归记录实际原有前置条件，不假称维护路径已有完整 document canonical Gate/fencing，也不在本期自行升级该模型。

现有参考测试文件：`tests/test_vector_backend_api.py`、`tests/test_qdrant_e2e_migration.py`、`tests/test_phase15b_unified_document_lifecycle.py`。应核对实际用例覆盖，未覆盖的条件在后续实施中补足；当前未运行它们。

**Stop gate**：五类文档出现旁路，或显式维护正向行为被 blanket sole-authority 规则误伤，或原有维护负向校验丢失，均不能通过 AC-31。不能以“维护不在范围内”为理由接受本期造成的兼容回归。

## 6. 阶段 Gate 与生产验收

| 阶段 | 必须通过 | 停止条件 |
| --- | --- | --- |
| C1 Runtime Contract & Ownership | PRD §22.1；单 claim、双状态、持久输入、短事务；§4 parsed staging/引用契约及 AC-29/30 相应断言；§5 维护边界及 AC-31 相应回归 | 任一 P0 不满足；parsed/current 共享写入风险未消除；维护职责误伤 |
| C2 Background Worker Runtime | PRD §22.2；202/暂停领取/双 Worker/KB 公平性/阶段与心跳/查询分页/构建止于 Candidate | 同步等待、双 claim、心跳受阻、Legacy 双执行或读 Candidate |
| C3 Recovery & Cooperative Control | PRD §22.3；崩溃、退避、取消竞态、失权提交拒绝；真实调度下复验 AC-29/30 | 旧解析输入污染、强杀取消、无界重试、错误对账或无有效隔离 |
| C4 Production Acceptance | 全部 AC；既有回归、真实向量与验证端点 canary、维护兼容、性能与回退演练 | 仅有离线替身，P0/P1 未解决，负载/证据缺失或回退改变 Active |

生产前另外记录：

- [ ] 实际数据库、持久卷、Worker 访问方式、CPU/内存、文档规模和并发/限流。
- [ ] 真实 Gate/Promote/Rollback、候选隔离及检索代际行为证据。
- [ ] submission P95 < 1s（排除上传传输），后台负载 query P95 增幅 ≤10% 的测量条件与结果；均为 **Target / Not Yet Measured**，当前未达成测量结论。
- [ ] heartbeat/TTL/sweep/backoff/阶段超时及执行容量参数已按负载固定，取消不承诺不安全强停。
- [ ] 停止领取、协作排空/逻辑隔离、保留 Job/Candidate、不开启同 Job 双执行的回退演练。
- [ ] 全部 PRD DoD 条件完成，并有产品/架构/测试及部署责任人确认。

## 7. 验收记录模板

| 项目 | 待填写 |
| --- | --- |
| AC ID / 实现 commit | 未执行 |
| 环境、数据及依赖 | 未执行 |
| 执行人、日期、步骤/命令 | 未执行 |
| 期望/实际结果 | 未执行 |
| 证据位置、缺陷编号 | 未执行 |
| 结论 | Not Executed |

当前只完成 PRD 修订及清单编写。所有验收保持未执行；文档 commit/push 完成后停止，不开始 Phase15-C1 代码实现。
