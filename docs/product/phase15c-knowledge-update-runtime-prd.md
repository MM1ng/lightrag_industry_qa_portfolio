# Phase15-C Product Requirements Document

## 1. Document Information

| 项目 | 内容 |
| --- | --- |
| Phase | Phase15-C |
| Name | Knowledge Update Runtime |
| 中文名称 | 知识库更新运行时 |
| Subtitle | Persistent Background Execution for Document Updates |
| Version | 1.1 — code-grounded amendment |
| Status | Ready for Review；架构定位已批准，PRD 已编写，尚未实施或完成运行验收 |
| Product | Industrial Knowledge Assistant Platform |
| Date | 2026-09-06 |
| Owner role | 产品负责人、技术架构师；研发负责实现，测试及部署运营负责人负责验收 |
| Repository baseline | `dev/retrieval-foundation-qa-downstream`；初版核查 `2be1577`，本次修订代码与远端基线 `0686940bd41989e47805a38f17eeab2c8aa6aade` |
| Deliverable | `docs/product/phase15c-knowledge-update-runtime-prd.md` |

本文将已批准的架构转化为产品行为、约束与验收条件。所有“必须”“不得”均是后续实现要求，不表示当前代码已经具备该行为。创建本文不授权进入 Phase15-C1，不执行业务开发、数据库迁移、测试修改或模型调用。

### 1.1 基线材料与证据优先级

1. 项目负责人本次批准的 Phase15-C 定位、架构决策及范围要求。
2. 最新《Phase15-C Architecture Review》，2026-09-06，文件 `Phase15-C_Architecture_Review.docx`。本地来源：`C:/Users/mming/.codex/visualizations/2026/09/06/01a0754c-581f-7c20-abab-14943b9340f3/Phase15-C_Architecture_Review.docx`。
3. [Phase15-B 最终验收报告](../phase15b-unified-document-lifecycle-pipeline-final-report.md)，2026-09-04。
4. 边界辅助材料：[Phase15 文档生命周期对齐计划](../superpowers/plans/2026-09-04-phase15-document-lifecycle-alignment.md)、[Phase15-A 架构评审](../phase-15a-document-lifecycle-architecture-review.md)。两者为前期材料，不能用尚未落地的计划或 Phase15-B 前的问题替代最终验收事实。

基线保存说明：架构报告为本次会话的本地 Word 产物；两份边界辅助材料在核查时是本地未跟踪文件。本次仅提交 PRD，不将这些材料或其他既有改动纳入文档提交。远端审阅如无法访问本地附件，应向项目负责人取得同版报告；本文已记录其关键决策、差异和验收约束。

代码仅用于确认既有字段、接口和角色，不构成新的架构设计来源。核查位置包括 `src/industrial_rag/db/models.py`、`routers/documents.py`、`routers/update_jobs.py`、`routers/generations.py`、`services/update_job_worker.py`、`services/incremental_update_service.py`、`repositories/update_job_repository.py`、`services/lifecycle_task_executor.py` 与 `api.py`。

### 1.2 与架构报告的差异处理

| 差异 | 本 PRD 的处理 |
| --- | --- |
| 架构报告将 Phase16 建议为 Knowledge Platform Runtime | 按最新批准路线修正为 **Phase16: Retrieval Intelligence**；Phase15-D 为 **Knowledge Observability / Admin Experience**。不承诺建设通用任务平台。 |
| 架构报告停机描述包含“隔离或终止执行” | 本期只允许协作停止及逻辑隔离；不得实现强杀线程、kill request 或不安全 interrupt。外部故障导致进程退出属于恢复场景，不是取消产品功能。 |
| 架构报告对 retry 接口使用“建议本期提供” | 本次要求已明确用户重试场景，纳入必做功能；仍受错误分类与次数上限限制。 |
| 前期生命周期计划曾包含后台执行相关任务 | 以 Phase15-B 最终验收中“未引入 Async Worker”为现状，将运行时接入归入 Phase15-C，不倒推为 Phase15-B 已完成。 |

以上为明确覆盖或需求细化。数据库队列、claim once、短事务、状态分离及显式发布边界均沿用最新架构评审，不重新设计生命周期。

### 1.3 本次代码核查修订

| 代码证据（基线 0686940） | 现状与修订要求 |
| --- | --- |
| `src/industrial_rag/services/incremental_update_service.py`：`_parse_document_pymupdf`，约 1229–1303 行 | 直接在 `kb_parsed_documents_dir(kb.id)/doc.id/current` 以写模式输出 `child_chunks.jsonl`、`parent_chunks.jsonl`，并更新 Document 解析元数据。当前路径不含 attempt。 |
| 同文件：`_candidate_snapshot_pairs`，约 1209–1220 行；`services/parse_service.py`：`load_child_chunks`、`load_parent_chunks`，约 543–568 行 | 变更文档的候选快照从共享 `current` 读取；即使 Candidate workspace 和向量集合隔离，解析输出仍可能被旧 Worker 覆盖后被新 attempt 读取。 |
| `services/parse_service.py`：解析临时目录与 current swap，约 196、280 行 | 另有 parse-task 临时输出再替换共享 current 的兼容逻辑；临时文件名不同不等于消费者绑定了 attempt。C1 要核对完整写入和读取链路，不能仅隔离最终索引。 |
| `services/index_service.py`：`index_knowledge_base`，约 59–71、229 行 | 显式 `target_backend` 是现有后端迁移前置条件；路径内仍有 Generation activate。不得以全局禁止 activate 的方式修改其职责。 |
| `services/handler_impls.py`：`handle_migrate_to_qdrant`、`handle_rollback_to_nano`，约 174–278 行 | 前者显式传入 Qdrant 迁移目标；后者检查 Nano 输入指纹后激活并更新后端/指针。两者是现有显式后端迁移/维护路径，不是五类文档更新的发布旁路许可。 |

修订结论：C1 的 P0 isolation 必须延伸至 **per-attempt parsed artifact staging/isolation**，同时精确限定 **Promote sole authority** 的适用对象。这里只增加需求和验收，不修改上述代码、不执行任何解析或迁移。对应验收清单见 [Phase15-C acceptance checklist](phase15c-acceptance-checklist.md)。

## 2. Background

Phase15-B 已完成 Unified Document Lifecycle Pipeline，解决 **Knowledge Lifecycle Correctness**：`add`、`replace`、`delete`、`reparse`、`reindex` 统一经持久化 UpdateJob 和 IncrementalUpdateService 构建隔离的 Candidate Generation，随后由显式 Validation 与 Promote 决定是否上线。Generation 版本、Candidate/Active 隔离、Validation Gate、Promote/Rollback、Lease/Fencing 和多实例保护构成必须保持的安全基础。

LifecycleTask 的文档处理器已经收敛为兼容适配路径，不拥有 Activate、跳过验证或修改 Active 的权限。`execute_job` 只构建 Candidate，不自动验证或发布。

现有同步路径仍使请求等待解析、分块、Embedding 与索引。部分文档路由虽然已声明 HTTP 202，实际服务仍可能在请求返回前完成长任务；仅有 202 状态码并不代表已实现异步提交。客户端断开、API 重启、外部服务慢响应和任务卡住，均使更新难以追踪和运营。

项目已有 UpdateJobWorker 的领取/心跳原语、持久化 claim 字段、查询/cancel/resume 接口和启动恢复逻辑。缺口是把这些能力整合为持续后台执行闭环，包括周期扫描、独立心跳、有限重试、协作取消以及安全的事务边界。不能将本阶段表述为从零新增全部恢复能力。

## 3. Product Positioning

Phase15-C 是 **Knowledge Update Runtime**，解决 **Knowledge Update Execution Reliability**。服务对象是知识库文档更新，执行单元是既有 UpdateJob，后台工作到 Candidate 构建完成为止。

批准的路径为：

```text
Document API / Legacy Adapter
  → Persist UpdateJob and durable input
  → HTTP 202 with job_id
  → UpdateJobRepository persistent task queue
  → Worker Poller
  → UpdateJobWorker claim + KB Lease + fencing
  → JobRunner with one ownership context
  → IncrementalUpdateService
  → Candidate Generation
  → execution_status = SUCCEEDED
  → STOP

管理员通过现有显式入口另行执行
  Validation → Validation Gate → Promote → Active Generation
```

202 表示提交已持久接受；它不要求 Worker 等待响应送达后才领取。图中次序表达职责边界，Worker 可在事务提交后立即看到 Job。创建新更新意图的请求不得等待后台构建。

本阶段不是 Knowledge Platform Runtime、Task Platform 或通用 Workflow Engine。UpdateJobRepository 本身就是 persistent queue，不新增 JobQueue 抽象或第二张 RuntimeJob 队列表。JobRunner 通过既有 Worker/Repository 契约领取和执行，不能形成第二次 claim。

**发布权限的作用域**：本文“Promote sole authority”“只有 Promote 才切换 Active”等表述，仅约束 `add / replace / delete / reparse / reindex` 五类 document lifecycle operation 的新内容发布。既有显式 Generation rollback 和后端迁移/维护（包括 `migrate_to_qdrant`、`rollback_to_nano`）保持各自入口、前置校验和行为，不要求它们统一改经 document UpdateJob/Promote。不得全局禁止底层 activate 或删除这些 handler；也不得允许五类文档操作借维护路径绕过 Validation/Promote。本期不重设计维护流程，不宣称它们已具备与文档 Promote 完全相同的 Gate/fencing 契约。

## 4. Goals

### 4.1 Product Goals

- 管理员提交五类文档更新后获得稳定 job_id 和查询地址，无需保持 HTTP 连接等待构建。
- 管理员可区分等待、执行、当前阶段、待恢复、取消中、失败和构建完成，能理解下一步动作。
- 对可恢复失败提供有限重试；对尚未完成的构建提供协作取消。
- 明确表达：**构建成功 ≠ 验证通过 ≠ 已发布 ≠ 知识已上线**。
- 通过现有管理 API 提供最小可用操作能力，不要求重新建设管理端界面。

### 4.2 Technical Goals

- 复用数据库 UpdateJob 队列与现有 KB Lease/Fencing，形成 Poller、Worker、Runner、Recovery Sweeper 的统一执行链路。
- 同一 attempt 只取得一次执行所有权，业务构建服务消费该上下文，不再次领取。
- 长外部工作在数据库事务之外执行；心跳、checkpoint 与完成提交采用独立的短事务。
- 重试和恢复使用独立 Candidate workspace/vector namespace；源输入持久化且可由独立进程读取。
- 新增 execution_status 并建立与既有 status 的完整一致性约束。

### 4.3 Reliability Goals

- 使用 at-least-once execution + fenced valid commit，不承诺 exactly-once external call。
- 在有效输入、可用依赖和未耗尽重试预算下，进程崩溃后的任务可自动重新领取；不可恢复任务收敛到可解释终态。
- 陈旧 Worker 的 checkpoint、success、failure 和业务元数据提交均不能覆盖新 attempt。
- 构建、失败、恢复及取消均不修改 Active；在线检索继续使用现有 Active Generation。

## 5. User Roles

| 角色 | 当前依据 | 本期能力 |
| --- | --- | --- |
| Knowledge Base Administrator | 文档、UpdateJob、Generation 管理接口使用 `require_admin_actor` | 提交、查询、取消、有限重试；通过现有显式接口验证和发布 |
| 部署及运行维护责任人 | 运营责任角色；不声明当前已有独立 System Operator RBAC 身份 | 部署/停用 Worker、检查日志指标、执行受控验收与恢复操作；业务 API 仍沿用现有管理员鉴权 |

不新增普通问答用户的文档管理、取消、重试或发布权限。角色表不授权跨 KB 越权，也不引入多租户调度模型。

## 6. User Stories

| ID | 用户故事 | 对应要求与验收 |
| --- | --- | --- |
| US-01 | 作为管理员，我提交文档更新后立即得到 job_id，以便离开页面仍能跟踪 | FR-01/02；AC-01/02 |
| US-02 | 我能查询后台执行状态，分辨等待、运行、失败和完成 | FR-07/15；AC-16 |
| US-03 | 我能看到当前实际执行阶段，判断是否有业务进展 | FR-08/09；AC-16/17 |
| US-04 | 我能看到脱敏失败原因和是否可重试，决定下一步操作 | FR-11/15；AC-18/24 |
| US-05 | 我能请求重试符合条件的失败任务，并保留原有历史 | FR-11；AC-09/19 |
| US-06 | 我能请求取消任务，并知道何时才真正停止 | FR-12；AC-10/20 |
| US-07 | 服务重启后，已接受任务仍能继续调度或明确报告恢复失败 | FR-10/13/14；AC-03/07/08 |
| US-08 | 构建成功后，我仍需主动验证和发布，防止未验收内容上线 | FR-17；AC-11/12/13/14 |
| US-09 | 我能分页查找本 KB 的任务，按状态筛选处理异常 | FR-18；AC-21 |

## 7. Scope

### 7.1 In Scope

- 五类文档操作提交与后台 Candidate 构建解耦。
- UpdateJobRepository 持久任务队列、Worker Poller、JobRunner。
- 单次 claim ownership、独立 heartbeat、execution state。
- 有限 retry/backoff、启动及周期 recovery、cooperative cancellation。
- 持久输入快照、attempt 产物隔离、短事务边界。
- per-attempt parsed artifact staging/isolation：隔离父块、子块等解析产物及候选消费引用；归属 Runtime isolation，不属于 Parser 算法升级。
- 基础任务查询、既有列表入口的分页修正和状态筛选。
- Legacy 文档 Adapter 收敛、历史数据兼容、最小运行指标。
- 独立 Worker 部署方式与生产安全验收；开发可支持 embedded worker。

### 7.2 Out of Scope

- 自动 Validation、自动 Promote，以及绕过 canonical Validation Gate。
- Retrieval 优化、Parser 算法升级、Chunk 策略升级、Embedding 模型升级。
- LangGraph、Workflow Engine、Celery、Kafka、Redis Queue。
- 多租户调度、优先级队列、自动扩缩容、通用任务平台、Knowledge Platform Runtime。
- 同 KB 多任务依赖编排或无限积压队列、任意指令级断点续跑。
- Dashboard/SSE/WebSocket 大改、精确完成百分比或预计剩余时间。
- 强杀线程、kill request、不安全 interrupt；通过取消触发回滚或物理删除。

本期允许的后续服务改动仅限运行接入所需的所有权上下文、事务拆分、持久状态与安全提交，不因此授权业务算法重写。本文编写任务本身不实施上述改动。

## 8. Functional Requirements

优先级定义：P0 为安全或核心闭环上线阻断项；P1 为本期必须完成的产品/运营能力；P2 为不阻断安全的改进建议。以下 FR 均为本期交付要求，不能以 P1 为由延期关闭。

### FR-01 Persistent Job Submission — P0

五类操作必须先持久化输入与一个 UpdateJob。仅在输入文件/引用可靠可读且 Job 事务提交成功后确认接受。数据库提交失败不得返回任务已接受；文件保存失败不得留下可领取的 Job。数据库与文件存储不假定具备跨系统原子事务，失败留下的孤立文件不得进入队列或影响 Active，沿用受控清理职责。

重复提交通过 KB、调用方、幂等键及输入摘要识别。相同键相同输入返回同一 Job；同键不同输入冲突。幂等检查先于同 KB busy 判断，防止响应丢失后的重复请求被错误视为新操作。

### FR-02 Immediate 202 Response — P0

新更新请求返回 202，至少包含 `job_id`、`knowledge_base_id`、`operation`、`status`、`execution_status` 和任务查询地址。响应不等待 parse、Embedding、Qdrant 或 indexing。HTTP 断开不取消已持久接受的任务。

“立即”不包括上传传输完成之前，也不免除权限、输入校验和持久提交。内容相同且业务判定 `no_change` 的既有短路结果不代表新任务；应保持明确的 no-change 语义，不伪造 job_id 或构建成功。已有 Job 的幂等返回附带其当前状态。

### FR-03 Worker Polling — P0

Poller 仅在有可用执行槽时选择已到期的 PENDING/RECOVERY_REQUIRED 任务。UpdateJobRepository 为队列事实来源，不使用内存排队结果作为持久状态。选择有界批次，跳过占用 KB，避免 KB A 的首条任务阻塞 KB B。

保留同 KB 单个未完成构建/待验证更新的准入策略；所有文档 API 和 Legacy Adapter 使用同一原子准入规则。不扩展同 KB 批量依赖队列。对 ready 后产生的其他更新继续实行 base_generation 一致性检查，不静默覆盖更新的发布。

### FR-04 Atomic Claim — P0

领取必须条件校验调度状态、到期时间、取消请求、attempt 上限及 KB Lease；并持久化 worker、Lease token、fencing token 和 attempt。并发领取只有一个成功。查询到 Job 不等于取得所有权。

复用现有先获取 KB Lease、再 claim Job 的契约。两步不必强行宣称是同一数据库事务，但仅在两步均成功后允许执行；claim 失败释放自己的 Lease，崩溃留下的孤立 Lease 可过期回收，不造成双写。

### FR-05 Single Execution Ownership — P0

使用 **ClaimedExecutionContext / equivalent ownership contract**，可复用已有 ClaimedUpdateJob/LeaseHandle，名称不是新增类的强制要求。上下文至少包含 job_id、kb_id、attempt、worker_id、lease_token、fencing_token、有效期及绑定的输入/候选标识。

同一 attempt 的所有权从 Worker 传给 Runner，再传给业务构建与持久提交。IncrementalUpdateService 不得在收到已领取上下文后重新 claim 或获取第二个生命周期执行所有权。上下文不能被 Job ID 或进程内锁替代，不能在外部接口暴露 token。

### FR-06 JobRunner — P0

Runner 管理一次构建尝试的开始、阶段、心跳、异常分类、协作停止和完成；调用现有 IncrementalUpdateService 生成 Candidate。禁止直接激活 Generation、调用自动验证/发布、绕过 Stage/Gate 或持有跨长调用的 DB 写事务。

已有 Worker.complete 等完成方法必须按双状态契约接入，不得无条件把业务 status 改为 succeeded。构建方法的返回值和 Candidate 的存在不是完成提交的替代证据。

### FR-07 Execution State Machine — P0

同一 UpdateJob 新增构建执行状态，使用第 10 节的六态及第 11 节矩阵。状态、归属与阶段结果必须条件更新；禁止任意 API 将 failed/running 直接覆盖成 pending。构建完成后停止 Runtime 自动调度。

### FR-08 Stage Tracking — P1

复用 current_stage，表达 preparing、parsing、chunking、embedding、indexing、finalizing、candidate_built 等实际阶段；各操作可跳过不适用阶段，delete/reindex 不伪造解析进度。报告当前 attempt 阶段以及最后一个已提交 checkpoint 的时间。

阶段日志与结果绑定 attempt。失败终态仍能查询最后执行阶段；不要仅显示 failed 而丢失故障位置。不新增 progress_stage；不承诺阶段数等于完成百分比。

### FR-09 Heartbeat — P0

由独立 Session 周期更新 Job claim 与 KB Lease；任一续租失败立即停止安排后续工作并拒绝有效提交。已有外部调用可继续返回，其结果仅落在隔离 attempt 中。不得在无法证明所有权时“尽力”提交失败状态。

解析等阻塞工作不得占住心跳执行循环。heartbeat 表达存活，阶段超时表达工作进展，两者分开判断。

### FR-10 Recovery Sweeper — P0

Worker 启动时扫描一次，并在运行期间周期扫描。只扫描本期构建执行，不凭生命周期 building/validating 就重放。heartbeat 陈旧是信号，需同时核对 claim、KB Lease 有效性和 fencing。

有效 Lease 尚在时不得抢占；确认失权后条件转为 RECOVERY_REQUIRED，按第 14 节核对 Candidate、输入、Active 和取消请求。多个 Sweeper 并行只能产生一次有效状态转换。

### FR-11 Retry with Backoff — P0

自动重试仅用于已分类的临时错误和可恢复中断；使用持久化 next_run_at、指数退避及有界抖动。重启不得丢失下一次可执行时间。具体参数在 C1 契约中固定，C4 压测后可配置调整，不引入调度策略平台。

attempt 在成功领取时递增，包含原始尝试；max_attempts 是同 Job 的总尝试上限。retry_count 表达实际开始的重试次数，新记录等于 `max(attempt - 1, 0)`，不是点击 retry 的次数。仅准备入队不增加 attempt。

人工 retry 仅接受执行 FAILED、错误可重试、输入与 base 仍有效、未取消/验证拒绝/发布且预算未耗尽的 Job；操作仅重排队，不同步构建。并发 retry 幂等，不清零历史计数，不自动提高 max_attempts。上限耗尽需要管理员重新判断后显式提交新更新意图，保留前序关联；Runtime 不自动创建新 Job 绕过预算。

### FR-12 Cooperative Cancellation — P0

未领取且无有效写入者的 PENDING/RECOVERY_REQUIRED 可条件取消。RUNNING 时持久化 cancel_requested_at 并返回“取消已请求”，直到安全 checkpoint 才确认 CANCELLED。取消请求不需要等待取得构建已持有的同一 KB 写 Lease，最终状态与候选处置仍受安全所有权约束。

取消不得强杀、不回滚 Active、不立即物理删除产物。Worker 崩溃后 Sweeper 应尊重已有取消请求，确认旧所有权失效并隔离旧 Candidate 后可收敛取消，不先重建再取消。

构建已经 SUCCEEDED、尚未发布时，沿用显式放弃 Candidate 的业务取消：保留执行成功事实，status=cancelled，禁止后续验证/发布。已 promoted/rolled_back 不可取消。成功、取消、验证和发布竞态的具体规则见第 10/13/16 节。

### FR-13 Persistent Input Snapshot — P0

执行只依赖持久化输入，至少绑定 operation、KB、document/source version、内容摘要、base_generation_id 和配置指纹。replace 必须有被替换对象及新版本；delete 必须有目标和基准版本；reparse 指定 document_id 与源版本；reindex 使用冻结的活动文档快照，不变更 Parser/Chunk/Embedding 配置。

源文件和必要引用须跨 Session、跨重启且对目标 Worker 可读。不依赖请求内 bytes、old_doc ORM 对象或临时路径。快照缺失或不匹配不猜测、不静默采用当前配置；明确失败或要求新提交。

### FR-14 Attempt Isolation — P0

每次实际 retry/recovery 构建尝试必须分配独立 Candidate workspace 和 vector namespace，并记录 attempt 与 Candidate 的对应。禁止复用仍可能被旧 Worker 写入的空间。

失权后的外部请求即使成功，也只能写入不可发布的旧 attempt。旧 Candidate 必须失去有效发布资格，新 Job 候选引用不得被旧 Worker 回写。物理回收由既有受控 GC 处理，并尊重 Active、回滚和输入保留条件。

**FR-14a Parsed artifact isolation — C1 P0**：reparse 当前解析输出写入共享 `parsed/documents/{doc_id}/current`，新 Runtime 不得继续将该路径作为进行中 attempt 的可变解析输出或新候选的隐式输入。每次构建尝试必须有绑定 KB、job_id、attempt、document/source version 的独立 parsed staging 目录，涵盖 parent/child chunks 及该次解析消费的其他产物。具体目录命名由 C1 契约确定，不新增 Parser 算法或改变解析配置。

Candidate 快照构建、chunk 读取及后续 Embedding/indexing 必须显式消费本 attempt 的已完整提交解析产物引用，并核验输入摘要/配置指纹，不能在读取时重新解析共享 `current` 指针。先写本 attempt staging，完整性校验后通过受 ownership/fencing 保护的短事务登记不可变引用，再进入下一阶段。失权 Worker 即使完成解析、文件写入或旧 current swap，也不得覆盖新 attempt 的文件、消费引用或 Document 元数据；仅在 DB 完成时检查 fencing 不足以保护共享文件。

若保留共享 current 供兼容或维护读取，本期后台 attempt 不直接更新它，且不得令维护消费者看到半成品 staging；兼容投影如确有必要，必须另行明确有效所有者和完整产物的发布契约，不允许猜测为“解析完成即可替换”。不同尝试不得复用仍可能被写入的 parsed 目录。失败、取消和失权产物保留隔离，沿既有受控清理处理。

本要求适用于 reparse，以及复用同一解析写入链路的 add/replace。这是 Runtime 的产物存储、所有权与消费绑定调整，不是 Parser、Chunk 或 Embedding 算法升级。

### FR-15 Query Job Status — P1

复用既有 GET 详情。返回双状态、当前阶段、attempt/max_attempts/retry_count、心跳/时间、next_run_at、取消申请、脱敏错误、Candidate 引用和构建结果；验证/发布事实来自各自现有事实来源。可执行动作由服务端依据状态、错误和权限计算。

不得公开 lease_token、内部 workspace、原始堆栈或密钥。不得将 succeeded 显示为“知识已上线”；历史 promoted 也不代表当前仍为 Active，应按当前 KB 指针单独表达。

### FR-16 Legacy Adapter Convergence — P0

LifecycleTaskExecutor 仅保留 legacy compatibility，不作为新 Runtime 核心执行器。文档 Adapter 只幂等创建/关联 UpdateJob 并映射业务状态，不直接构建、重试、恢复、验证或发布。

同一 Legacy 请求和 UpdateJob 的关联必须在可恢复的持久事务中完成；重复 handler 调用不创建多个 Job。task_id 可继续兼容返回，但必须提供同一 job_id。旧入口返回前已能查到该 Job，不能等待 Legacy Executor 之后才创建。

### FR-17 Explicit Validation/Promote Boundary — P0

本 FR 的 sole-authority 约束仅适用于五类 document lifecycle operation；范围定义见第 3 节。现有显式 backend migration/maintenance（`migrate_to_qdrant`、`rollback_to_nano`）不纳入文档更新状态机改造，必须保留原有正向行为及原有无效输入/陈旧指纹拒绝行为。不得通过全局拦截 `VectorIndexGenerationRepository.activate` 验收“sole authority”，而应分别验证文档 handler 无旁路及显式维护路径无误伤。

execution_status=SUCCEEDED 仅代表 Candidate 构建完成。开始显式验证前必须证明构建完成、产物完整冻结、Candidate 属于有效 attempt 且未取消；验证过程中 Runtime 不重新领取构建。

验证失败保持 Active 不变，且不抹去构建成功事实。Promote 继续重新校验 canonical evidence、配置/产物指纹及现有 fencing/CAS。取消或 superseded Candidate 不得 Promote。Runtime、Retry、Recovery 和 Legacy Adapter 均无自动发布权。

### FR-18 Job List Pagination / Filtering — P1

现有列表入口已存在且接受 offset/limit，但当前查询链路未正确传递参数，total 为返回列表长度。本期在原入口修正真实分页，并按架构报告增加 status/execution_status 的精确状态筛选，不创建重复资源或新看板。

保持 offset 默认 0、limit 默认 50/最大 100 的已有参数边界。按 created_at 降序、job_id 作为稳定并列排序键。total 是当前 KB 内匹配过滤条件的记录数，不是当前页长度；禁止跨 KB 混入记录。offset 分页不承诺跨多次请求的历史快照一致性，新增任务期间可刷新；静态数据集须无重复漏页。

## 9. Non-Functional Requirements

| 类别 | 可验收要求 | 优先级 |
| --- | --- | --- |
| Safety | Candidate 构建/恢复/取消不修改 Active；旧 owner 的成功、失败、checkpoint 及元数据提交全部拒绝；无验证绕过 | P0 |
| Idempotency | 相同提交意图返回同一 Job；并发 claim/retry/cancel 只有一个有效转换；不要求外部调用恰好一次 | P0 |
| Recoverability | 已接受输入与 Job 跨重启存在；租约到期后自动对账或恢复；耗尽预算收敛终态 | P0 |
| Consistency | 第 11 节合法组合约束覆盖所有持久提交；输入与 attempt 隔离；同 KB 单写与 base 一致性 | P0 |
| Observability | 可关联 job_id、attempt、KB、request/trace、worker、错误码及阶段；记录轮询、续租、恢复和控制动作结果 | P1 |
| Performance | 第 18 节的待压测目标；无长 DB 写事务阻塞心跳/取消；有界执行并发及轮询批次 | P1 |
| Backward Compatibility | 保留 status 值、既有资源路径和必要响应字段；resume 与 Legacy 接入同一 Runtime；历史已发布任务不重放 | P0 |
| Security | 沿用管理员鉴权及 KB 归属检查；输入路径不可任意注入；错误脱敏，token 不对外，敏感日志访问受部署权限约束 | P0 |

可观测性最小集合：队列深度、最老等待时长、运行数、阶段耗时、成功/失败/重试/恢复次数、Lease 冲突和续租失败、取消确认延迟、Worker 最后成功轮询时间。日志与查询接口足以承载本期运营，不要求 Dashboard。

## 10. State Machine

### 10.1 execution_status 定义

| 状态 | 定义 | 是否自动可领取 |
| --- | --- | --- |
| PENDING | 输入已持久接受，等待首次执行或人工重排队 | 到期、未取消、预算可用时可以 |
| RUNNING | 已取得当前 attempt 所有权，正在准备或构建；可附带取消请求 | 不可以再次领取 |
| RECOVERY_REQUIRED | 临时故障或失权后的受控恢复/退避等待 | 对账、安全条件与到期要求满足后可以 |
| SUCCEEDED | 已持久提交完整 Candidate 构建结果 | 不可以；等待现有显式生命周期动作 |
| FAILED | 当前构建终止，原因已持久记录 | 不可以；仅符合条件的人工 retry 可重排队 |
| CANCELLED | 取消已确认，当前构建不可再有效提交 | 不可以；不 resume/retry 同 Job |

### 10.2 转换及线性化条件

| ID | 来源 → 目标 | 触发与原子条件 | 生命周期处理 |
| --- | --- | --- | --- |
| ST-01 | PENDING → RUNNING | 到期、无取消、预算可用；完整 KB Lease + Job claim 成功 | 同事务进入 claimed/building 等合法执行状态 |
| ST-02 | RUNNING → SUCCEEDED | owner/fencing 有效、无先提交的取消、产物完整冻结、输入/base 仍有效 | 保持 building，标记 candidate_built；不 ready/promote |
| ST-03 | RUNNING → RECOVERY_REQUIRED | 可重试错误且还有预算；或旧所有权已失效，由恢复主体条件更新 | status=recovery_required；旧 attempt 隔离 |
| ST-04 | RECOVERY_REQUIRED → RUNNING | next_run_at 到期，对账后确需重建，新 Lease 与 claim 成功 | 新 attempt 独立 Candidate，status=claimed/building |
| ST-05 | RUNNING → FAILED | 非重试错误、输入/base 不合法或预算耗尽；当前 owner 合法 | status=failed，记录错误与终止时间 |
| ST-06 | RECOVERY_REQUIRED → FAILED | 对账发现不能恢复、输入损坏或预算已耗尽 | status=failed；不得永久滞留待恢复 |
| ST-07 | FAILED → PENDING | 人工 retry；错误可重试、预算与输入/base 有效，未被其他请求重排队 | status=pending；原 attempt 摘要保留，旧 Candidate 不可发布 |
| ST-08 | PENDING/RECOVERY_REQUIRED → CANCELLED | 确认无有效 owner；取消条件更新成功 | status=cancelled；不分配新构建 attempt |
| ST-09 | RUNNING → RUNNING | cancel_requested_at 提交成功；只是“取消中” | 生命周期暂不改变，不允许随后正常成功提交 |
| ST-10 | RUNNING → CANCELLED | 有效 owner 到安全 checkpoint 确认停止；或 Sweeper 确认旧所有权失效并完成隔离 | status=cancelled，保留审计及候选引用历史 |
| ST-11 | RECOVERY_REQUIRED → SUCCEEDED | 仅对账修复：可信的已完成提交/冻结凭据存在，并确认无旧写者，不发生外部重建 | status=building 或保留已有后续生命周期事实；不是新的构建 attempt |

仅存在文件、向量集合或 building Candidate 不能触发 ST-11；无法证明有效完成时必须走独立 attempt 重建。RUNNING 的租约过期不由旧 Worker自行标记失败或续命，交由恢复主体处理。

SUCCEEDED 的构建事实是终态。其后 status 可依显式操作进入 validating、ready、failed（验证失败）、cancelled（放弃候选）、promoted 或 rolled_back，execution_status 均保持 SUCCEEDED。已发布/回滚的 Job 不回到构建队列。

### 10.3 取消与成功竞态

- 取消请求先在同一 Job 上提交：正常构建成功条件必须失败，Worker 在安全边界确认取消。
- 构建成功先提交：返回“构建已完成”及当前状态。若本次请求是在 RUNNING 视图下发起，不悄悄转换为“放弃已完成 Candidate”；返回 409，管理员可按新状态显式取消候选。
- 显式放弃 SUCCEEDED Candidate 与验证/Promote 串行化：若生命周期操作持有 KB Lease，则返回 busy/409，不抢占；拿到权限后重新检查状态。
- Promote 已先成功则取消拒绝；取消已先成功则验证/Promote 拒绝。重复已确认取消返回幂等结果，不再触发清理。

## 11. Lifecycle / Execution Invariant Matrix

本表作用于 **UpdateJob.status × UpdateJob.execution_status**，不是 VectorIndexGeneration.status 的替代模型。生命周期值沿用当前小写枚举；执行值使用本 PRD 的大写规范。N=正常合法；C=满足下文条件才合法；X=禁止。所有新写入必须符合矩阵，状态成对修改需在同一短事务中生效。

| lifecycle status | PENDING | RUNNING | RECOVERY_REQUIRED | SUCCEEDED | FAILED | CANCELLED |
| --- | --- | --- | --- | --- | --- | --- |
| pending | N | X | X | X | X | X |
| claimed | X | N | X | X | X | X |
| running | X | C1 | X | X | X | X |
| building | X | N | X | C2 | X | X |
| validating | X | X | X | C3 | X | X |
| ready | X | X | X | C4 | X | X |
| succeeded | X | X | X | C5 | X | X |
| failed | X | X | X | C6 | N | X |
| cancelled | X | X | X | C7 | X | N |
| recovery_required | X | X | N | X | X | X |
| promoted | X | X | X | C8 | X | X |
| rolled_back | X | X | X | C9 | X | X |

条件定义：

| 条件 | 必须成立的事实 |
| --- | --- |
| C1 | 兼容现有 running 生命周期标签；当前构建 owner 与 claim 有效。不把它变成新的生命周期分支。 |
| C2 | 有持久的完整构建结果和冻结产物；展示“构建完成，待验证”，不依据 building 推断仍在执行。 |
| C3 | 构建已成功，管理员显式启动验证；验证由现有验证流程负责，构建 Sweeper 不重放。 |
| C4 | 构建已成功且现有验证流程已判定通过。ready 不替代 Promote 时对证据有效性的再次校验。 |
| C5 | 仅兼容历史 succeeded；须有构建完成依据。新 Runtime 不主动写该生命周期值，也不将其解释为已发布。 |
| C6 | 构建曾成功，随后显式验证失败。查询分别表达 validation failure 与 build success，不允许构建 retry API 重建此 Job。 |
| C7 | 构建成功后管理员显式放弃未发布候选；执行成功历史保留，候选不能验证或发布。 |
| C8 | 现有 Promote 事实存在。是否“当前在线”另查 KB Active 指针，不能只看历史 promoted 标签。 |
| C9 | 现有回滚生命周期事实存在；曾完成的构建不可重新排队或重新执行。 |

明确禁止 `PENDING + promoted`、`RUNNING + ready`、`RUNNING + validating`、`FAILED + ready`、`CANCELLED + promoted`。自动重试和恢复等待统一存为 recovery_required/RECOVERY_REQUIRED，不能长期保留 building/RECOVERY_REQUIRED 等半转换组合。

迁移期间不确定的历史组合不是新合法状态。隔离该记录的运行调度与候选发布，保留已有 Active 服务；由受控对账依据实际事实修复。若 Active 或验证审计与 Job 字段冲突，不自动回退 Active，不以“修复矩阵”为理由重放构建。

成功事实补偿优先于重放：已经有发布/回滚依据的记录必须补齐历史执行事实或报告异常，绝不变回 PENDING。第 19 节规定回填证据。

## 12. Data Model Requirements

### 12.1 Reuse existing fields

本次只读核查确认下列字段已存在，不能再次新增同义字段。

| 现有字段 | 本期契约 |
| --- | --- |
| status | 保留生命周期语义及枚举，按矩阵与 execution_status 联动 |
| operation / knowledge_base_id / document_id | 五类操作及归属；不从 HTTP 内存对象恢复含义 |
| base_generation_id / candidate_generation_id | 冻结基准及当前有效候选关联；旧 attempt 候选保留在历史摘要 |
| old_content_sha256 / new_content_sha256 | 绑定操作输入内容；不能代替完整 source version / replace target |
| created_at / updated_at | Job 生命周期时间；updated_at 不作为唯一存活或进度判据 |
| started_at | 第一次实际构建开始时间，新 Runtime 不在每次 attempt 重置；attempt 起止单独记录在有界摘要 |
| finished_at | 保留既有生命周期完成/失败/取消时间含义；不挪作后台构建成功时间 |
| heartbeat_at / lease_expires_at | 当前 claim 存活与过期判断；不能伪造业务进度 |
| worker_id / lease_token / fencing_token / claimed_at | 当前 attempt 的所有权；凭据不向外公开 |
| current_stage / checkpoint | 现行阶段、阶段提交、输入及产物凭据；不新增 progress_stage |
| error_code / sanitized_error_message | 当前错误分类与脱敏原因，失败历史按 attempt 保存，不新增原始 error_message |
| attempt / max_attempts / retry_count | 成功 claim 次数、总尝试上限及实际重试次数；历史值迁移前先审计，不伪造原始 attempt |
| request_id / trace_id | 请求与日志关联；现有 request_id 不能被假定已有唯一约束 |
| created_by / approved_by | 沿用原业务含义；取消/重试操作人另放控制审计，不假借 approved_by 声称发布审批 |
| metrics / result | 阶段耗时、构建输出、验证结果及有界 attempt/control 摘要；禁止写完整堆栈或无限日志 |

### 12.2 New fields proposed

当前核查的 UpdateJob 模型尚无以下四个字段，列为本期新增需求，本文不执行迁移。

| 字段 | 需求 | 时间/状态规则 |
| --- | --- | --- |
| execution_status | 构建执行六态 | 新 Job 为 PENDING；按第 10/11 节更新；历史先安全回填再纳入调度 |
| next_run_at | 持久化下一次允许领取的时间 | PENDING/RECOVERY_REQUIRED 必须有确定到期语义；终态不参与调度 |
| cancel_requested_at | 接受取消申请的时间 | 同一请求幂等；不等于取消确认时间，不抹去控制审计 |
| execution_finished_at | 当前构建执行终态到达时间 | SUCCEEDED/FAILED/CANCELLED 时写入；重排队前归档旧终态时间并清空当前值；后续验证/发布不覆盖构建成功时间 |

提交幂等需要持久唯一语义：按调用方、KB 与客户端键区分请求，并比较 operation/input 摘要。优先评估既有 request_id 是否能承担该契约；如不能，C1 需明确一个专用幂等字段与唯一约束。此处不是新增队列表的许可。

checkpoint/result 可采用有版本的结构持久化输入快照、attempt 摘要、前序 Job 关联及控制动作审计。输入快照不可被后续 checkpoint 覆写丢失；必要的幂等/队列检索字段不能只依赖无约束 JSON 查询。索引覆盖调度与过期扫描，但具体 SQL/迁移属于后续实现。

### 12.3 暂不增加

不新增 JobQueue、RuntimeJob、通用 JobAttempt 表、progress_percent、ETA、priority、租户调度配置或 Worker 注册中心。独立历史存储与更丰富管理体验延期；本期必须保留足以对账的有界 attempt 证据。

## 13. API Requirements

### 13.1 五类文档操作入口

以下路径由现有 `routers/documents.py` 确认，均沿用 `/v1/knowledge-bases/{kb_id}` 前缀。

| 操作 | 现有入口 | 本期要求 |
| --- | --- | --- |
| add | POST `/documents` | 持久输入与 Job 后返回 202，不等待构建 |
| replace | PUT `/documents/{doc_id}` | 固定替换对象和新版本，持久提交后返回 202 |
| delete | DELETE `/documents/{doc_id}` | 提交逻辑删除更新意图；构建/取消不物理删除来源或修改 Active |
| reparse | POST `/documents/{doc_id}/reparse` | 返回已持久化 job_id；兼容 task_id；不改 Parser 算法或配置 |
| reindex | POST `/documents/{doc_id}/reindex` | 保留兼容路径，但明确执行现有 KB 活动快照重建语义，不重新定义为仅该文档重建 |

同 KB 存在构建/待恢复/待验证冲突时，新意图返回既有 busy 语义及安全可见的冲突信息；不得用延迟 HTTP 响应排队等待长任务完成。相同幂等请求返回已有 Job，不新建任务。no_change 与非法请求不纳入“新 Job 必须 202”的成功路径。

### 13.2 UpdateJob 资源

统一前缀：`/v1/knowledge-bases/{kb_id}/update-jobs`。

| 接口 | 现状 | 目标语义 |
| --- | --- | --- |
| GET `/{job_id}` | 已有 | 200 返回双状态及 FR-15 字段；未知/不属于此 KB 的 Job 按既有 not-found 契约处理，不泄露其他 KB 数据 |
| GET 空路径 | 已有 | 修正 offset/limit/total；增加 lifecycle status 与 execution_status 精确筛选；非法过滤值拒绝 |
| POST `/{job_id}/cancel` | 已有 | 排队任务确认取消 200；运行中申请 202；重复申请返回一致结果；不适用状态/竞态返回 409 |
| POST `/{job_id}/retry` | 本期新增动作 | 有条件重排队 202；请求只修改调度意图；不可重试/预算耗尽/取消/发布冲突 409 |
| POST `/{job_id}/resume` | 已有 | 兼容入口接入同一重排队/恢复契约，不同步执行，不产生新的重试预算 |

resume 的确定语义：PENDING 返回当前已排队结果；RECOVERY_REQUIRED 可幂等请求恢复，但不越过 next_run_at/Lease/输入检查；FAILED 按 retry 规则；RUNNING 有有效 owner 时返回 409，不抢占；SUCCEEDED/ready/promoted/rolled_back 返回 already_complete；CANCELLED 返回 409。历史未知记录只允许对账，不能直接 resume 构建。

重试请求须携带可审计请求标识及被重试 attempt。网络重放相同请求返回第一次排队结果，即使任务已被领取，也不把它误作再次重试。取消请求以当前观察到的 execution_status/attempt 或等价前置条件保护第 10.3 节竞态。

新增字段保持向后兼容，字段缺省策略和旧客户端契约在 C1 固定。Legacy task 的完成提示必须说明是“已提交 UpdateJob”或对应实际生命周期结果，不得表示已上线。

### 13.3 显式 Validation 与 Promote

沿用 `/v1/knowledge-bases/{kb_id}/generations/{generation_id}/validate` 和 `/promote` 两个现有 POST 入口，以及现有 rollback 入口。

Runtime 不调用它们。验证前检查有效构建结果和非取消 Candidate；Promote 保留现有鉴权、canonical evidence 再校验、KB Lease/Fencing 及 Active CAS。字段补齐与前置安全检查不改变验证算法。明确验证失败的 Job 不通过 retry/resume 自动重新构建或反复验证。

## 14. Recovery Strategy

### 14.1 启动与周期扫描

Worker 启动后从数据库发现已接受任务，不依赖上次进程的内存集合。启动扫描之外必须周期执行 Sweeper，覆盖“启动时尚未过期、启动后才过期”的任务。嵌入式和独立 Worker 使用同一套恢复规则，旧 api.py 恢复路径不再并行直接调用同步构建。

### 14.2 失权判定与恢复步骤

1. 选择 RUNNING 且租约疑似失效的 Job；核对 claim 与 KB Lease 的 token、owner、有效期。使用统一服务端时间口径，不信任客户端时钟。
2. heartbeat 过旧但 KB Lease 仍有效时不抢占。Job/KB Lease 只有一侧续租成功时保守处理，不把不一致当作重新执行许可。
3. 确认旧 owner 已无有效写权后，条件更新为 recovery_required/RECOVERY_REQUIRED，保留旧 attempt 的错误、候选及输入证据。若已取消、完成或被别的 Sweeper 改变，退出而不是覆盖。
4. 优先处理取消请求、已有构建完成/验证/发布事实。明确已完成只对账，不重放。仅有 Candidate 文件或集合不代表可信完成。
5. 检查输入版本、哈希、配置与 base_generation_id；过时 base 拒绝自动 rebase，管理员重新评估后提交新意图。不得仅在 claim 时检查而忽略最终提交及发布前的变化。
6. 确需重建且仍有预算时，next_run_at 到期后取得新的 Lease/fencing、claim 新 attempt，在新 workspace/namespace 执行。旧 Candidate 被隔离，不能再次被验证/Promote。
7. 不可恢复或预算耗尽则 FAILED；执行中失权的旧 Worker不得自行覆盖这个结果。

重建隔离同时覆盖 parsed staging：恢复后的候选只读新 attempt 明确绑定的解析快照。旧 Worker 在解析写入后或共享 current 替换前失权再恢复，不能污染新 attempt 的父/子块。检查点记录 parsed artifact 引用和摘要，文件存在或旧 current 内容“看起来完整”不能充当本 attempt 完成证据。

### 14.3 安全承诺

允许重复 Embedding/Qdrant 请求与重复计算费用，不允许重复的有效状态提交。数据库 fencing 保护元数据，attempt namespace 隔离保护外部副作用；两者缺一不可。所有 success/failure/checkpoint 提交和相关文档状态写入都必须校验当前 ownership/fencing，不能只保护“成功”而放过异常分支。

显式验证的崩溃恢复属于现有验证流程。本期构建 Sweeper 对 execution_status=SUCCEEDED、status=validating 的 Job 不重新构建，也不自动再运行 Validation；查询应报告其真实状态，交由已有管理操作处理。

### 14.4 恢复可用性前提

Worker 必须可读取共享的数据库和持久输入；独立进程不能仅拿到另一进程的本地临时路径。推荐先在同一代码库、可控部署拓扑下运行独立 Worker。实际数据库类型、持久卷访问、最大文档规模及依赖限流在 C4 前确认，不假定当前生产是 PostgreSQL 或多主机共享存储。

## 15. Transaction Boundary

数据库事务不得覆盖 parse、Embedding、Qdrant 或 indexing 等长耗时外部工作。要求的逻辑顺序是：

```text
claim transaction
  → commit ownership
  → short transaction to persist attempt/candidate intent
  → long external work outside DB transaction
  → checkpoint transaction with ownership/fencing check
  → commit durable stage result
  → next external stage
  → completion transaction with ownership/fencing/cancel/input checks
  → commit SUCCEEDED + frozen candidate result
  → release this owner's lease
```

- claim 完成和 attempt/candidate 意图持久化之后才执行对应外部写入，使崩溃后的产物可以追踪。
- heartbeat 使用独立 Session/短事务，不能与 Runner 协程共享同一个 AsyncSession。
- 领取、心跳、取消请求、checkpoint、success/failure 的事务各自有界；业务异常处理不得通过不受 fencing 保护的 commit 覆盖新 owner。
- cancel_requested_at、当前执行状态、有效 owner 和结果条件在 completion transaction 中共同判断，避免检查后又被取消的窗口。
- 外部写入完成但 DB checkpoint 失败：不认为已完成，按输入/产物凭据对账或隔离重建；不宣称 DB rollback 能撤销外部调用。
- parsed staging 写入同样在长 DB 事务之外，写入目标从开始即绑定本 attempt；完整产物引用的登记和 Document 解析元数据提交必须受 ownership/fencing 保护，禁止“先覆盖共享 current，后在 DB 检查租约”。
- DB 完成提交成功但释放 Lease 失败：任务仍是完成状态，只处理过期 Lease，不因释放失败重新构建。
- 现有默认 SQLite 的长写事务是心跳/取消阻塞风险。应通过短事务与低并发验收解决；本期不默认引入新数据库或外部队列。

## 16. Failure Model

| 故障 | 是否 retryable | 本期行为与安全结果 |
| --- | --- | --- |
| Parse failure：损坏/不支持的输入 | 否 | 执行 FAILED；显示输入原因，要求更正输入并新提交，不循环解析 |
| Parse failure：临时可用性错误 | 条件允许 | 只有明确分类为临时错误且输入完整时有限退避；未知错误默认人工处理 |
| Embedding timeout / 暂时限流 | 是，受预算限制 | RECOVERY_REQUIRED + next_run_at；每次重建独立 Candidate；允许重复费用，401/配置错误不按超时重试 |
| Vector backend error | 条件允许 | 临时连接/服务错误退避；鉴权、schema、维度或输入不符直接失败；旧集合不复用 |
| Process crash | 条件允许 | 旧 Lease 过期并完成对账后恢复；不要求旧进程主动清理；输入缺失/预算耗尽则失败 |
| Stale worker | 旧 owner 不允许重试/提交 | 新 owner 可按恢复规则接管；旧 success/failure/checkpoint 拒绝，外部副作用仅落在隔离空间 |
| Stale base generation | 否 | 报告基准已变化，禁止静默 rebase 或发布覆盖；管理员新提交 |
| Duplicate submission | 不属于执行失败 | 相同键/摘要返回同一 Job；同键不同摘要冲突，不消耗 attempt |
| Cancel race | 不触发自动重试 | 请求与成功条件线性化；取消先提交则不得成功；成功先提交则重新确认候选放弃语义；Active 不变 |
| Retry race | 幂等控制操作 | 同一 failed attempt 仅排队一次；不重复递增计数、不重复构建 |
| Validation failed | 不属于构建 retry | status=failed、execution_status=SUCCEEDED；保留验证失败证据及旧 Active，不自动修复/重建 |
| Input snapshot missing or mismatched | 否 | FAILED 或历史对账隔离；不得猜测源版本/替换对象/配置 |
| Heartbeat DB write failure | 条件恢复 | 当前 owner 停止后续有效工作；不凭本地判断继续提交；确认 Lease 状态后恢复 |
| Completion response lost | 不自动重建 | 通过 Job 当前状态和可信完成证据返回原结果，消除重复完成提交 |
| Attempt limit reached | 同 Job 否 | FAILED，提供明确错误及下一步提示；不得永远停在 RECOVERY_REQUIRED |

超时是错误分类及协作停发后续工作的信号，不是强杀功能。安全 checkpoint 迟迟不到达时保持“取消中/超时待处理”并告警；如果执行权随后失效，可逻辑隔离候选并安全收敛。不能为了满足取消延迟而进行不安全中断。

## 17. Acceptance Criteria

以下为后续实施阶段的可执行验收清单。本 PRD 任务不运行这些测试，不调用模型、检索或 Embedding。测试需保存输入、状态、时间和 generation/attempt/token 关联证据。

| ID | 给定及操作 | 必须观察到的结果 | 追溯 |
| --- | --- | --- | --- |
| AC-01 | 对五类操作设置可阻塞的构建替身，提交合法新意图 | HTTP 在构建释放前返回 202 和可查询 job_id；非仅路由声明 202 | FR-01/02 |
| AC-02 | Worker 暂停，提交有效更新 | Job 持久 PENDING，无 Candidate 构建副作用，Active 不变 | FR-01/03 |
| AC-03 | AC-02 后重启 API，再启动 Worker | 无需原请求上下文即可领取同一 Job，源文件可读，attempt 正确增长 | FR-10/13 |
| AC-04 | 两个 Worker 同时领取同一 Job | 恰好一个有效 claim/KB owner；另一方不得调用业务执行；服务内部不二次 claim | FR-04/05 |
| AC-05 | 同 KB 并发新提交及 Legacy 提交 | 幂等重复复用原 Job；冲突新意图按 busy 处理；不存在两个有效 KB writer | FR-03/16 |
| AC-06 | KB A 的 Lease 持续占用，KB B 有到期任务且有执行槽 | B 在有界轮询内被领取，A 不阻塞全队列 | FR-03 |
| AC-07 | 分别在 claim 后、外部阶段中、候选落盘后、完成提交前注入进程崩溃 | 租约失效后自动对账/独立恢复；仅启动时扫描遗漏的后到期任务也能被周期扫描发现 | FR-10/14 |
| AC-08 | 新 attempt 接管后，旧 Worker 提交 success/failure/checkpoint/文档状态 | 全部被 ownership/fencing 拒绝；新 Job 状态、候选引用和 Active 均不被覆盖 | FR-05/09/14 |
| AC-09 | 制造暂时故障并触发 retry/recovery | 每个实际重建 attempt 的 workspace 与 vector namespace 不同；旧 Candidate 不可 Promote | FR-11/14 |
| AC-10 | RUNNING 时申请取消，保持外部调用暂未返回 | 先返回 202/取消中；到安全 checkpoint 或确认失权并隔离后才确认取消；全过程不 Promote、不强杀 | FR-12 |
| AC-11 | 五类构建成功 | execution_status=SUCCEEDED，完整产物可核验，status=building，Active ID/epoch 不被构建改动 | FR-07/17 |
| AC-12 | 对成功构建执行显式 Validation，并使 Gate 失败 | status=failed，execution_status 保持 SUCCEEDED，Active 不变；构建 retry 被拒绝 | FR-17 |
| AC-13 | 对五类文档操作的验证通过 Candidate 显式 Promote | 五类文档新内容只有此路径按现有 Gate/fencing 发布；无证据、取消或陈旧候选拒绝；既有显式 rollback/backend migration/maintenance 不适用全局禁写规则 | FR-17；§3 |
| AC-14 | 新 Candidate 构建期间并发查询 | 查询仍从旧 Active 读取，不读取 Candidate 或失败 attempt；既有代际刷新契约保持 | FR-14/17 |
| AC-15 | 完成实现后运行既有回归基线 | Phase15-B、Phase9、Validation Gate、Job Recovery、Multi-instance 相关测试全通过，保存本次结果 | NFR Safety |
| AC-16 | 在多个真实阶段提交 checkpoint 并查询 | 阶段、attempt、双状态可见；失败可找到最后阶段；无伪造百分比或“已上线” | FR-08/15 |
| AC-17 | 阶段工作持续超过多个 heartbeat 周期 | 独立心跳可提交，取消请求可落库；没有覆盖外部工作的长 DB 事务 | FR-09；§15 |
| AC-18 | 注入可重试、不可重试及未知错误 | 按第 16 节分类，next_run_at 在重启后保留；未到期不领取；耗尽预算 FAILED | FR-11 |
| AC-19 | 同一 failed attempt 并发 retry 及网络重放 | 只重排队一次，实际 claim 前计数不增长，历史不清零 | FR-11 |
| AC-20 | 分别令取消先提交、完成先提交、发布先提交 | 按第 10.3 节返回确定结果，不出现“已确认取消且又成功发布” | FR-12/17 |
| AC-21 | 静态 125 条 KB 任务，以 limit=50 分页并按状态过滤 | 正确页条数/排序/过滤后 total；无跨 KB 泄漏；非法 limit/状态拒绝 | FR-18 |
| AC-22 | 同键同输入并发提交，以及同键不同输入 | 前者同 job_id，后者冲突；源文件或 Job 提交失败不返回已接受 | FR-01/13 |
| AC-23 | 对包含全部历史 status 的数据执行回填验证 | ready/promoted/rolled_back 不重放；building 按证据分类；非法矩阵组合被识别并隔离 | §11/19 |
| AC-24 | 无管理员权限、错误 KB、包含敏感内部错误的查询/控制请求 | 按现有授权拒绝；返回错误脱敏，不返回 lease token/内部路径/堆栈 | FR-15；NFR Security |
| AC-25 | 停止领取、排空/逻辑隔离 Worker 并关闭 Runtime | Job/Candidate 保留、Active 不回滚；同一 Job 的同步兼容路径不与后台并行 | §20 |
| AC-26 | 人为仅创建部分 Candidate 文件/集合，缺少有效完成凭据 | 不因 candidate_built/存在候选就确认成功；隔离新 attempt 重建或明确失败 | FR-06/10 |
| AC-27 | 等待执行期间改变 base，或发布前出现新 Active | 当前 Job 不静默使用新基准、不覆盖较新发布；给出 stale-base 冲突 | FR-13/17 |
| AC-28 | Job 完成已提交但 Lease release 或 HTTP 响应丢失 | 构建不重放，返回已有成功事实；处理残留 Lease 不覆盖生命周期 | §14/15 |
| AC-29 | reparse attempt A 在解析写入/旧 current swap 前停顿并失权，B 领取后产生带可区分标记的父/子块，再恢复 A；对 add/replace 共用链路参数化 | A/B parsed staging 不同；A 只能写隔离旧目录，不能修改 B 文件、消费引用或 Document 元数据；B 的 Candidate/Embedding 输入只含 B 绑定快照，共享 current 不被后台 attempt 覆盖 | FR-14a；C1 P0 |
| AC-30 | parsed staging 写一半崩溃、checkpoint 登记前崩溃，以及取消后迟到解析结果 | 半成品不能被快照构建或维护消费者读取；新尝试独立 staging；无有效 owner 不登记引用；重试不退回共享 current；Active 产物与受保护兼容数据不受污染 | FR-13/14a；§14/15 |
| AC-31 | 对五类文档 handler 执行旁路负向检查，再通过现有显式 migrate_to_qdrant/rollback_to_nano 路径做正反向回归 | 文档操作不能借迁移 handler 发布；显式迁移/维护在满足原条件时仍可工作，缺少迁移目标及陈旧 Nano 指纹仍按原规则拒绝；不得全局禁用 activate 或强制维护经 document UpdateJob | FR-17；§3 |

回归文件基线：`tests/test_phase15b_unified_document_lifecycle.py`、`tests/test_phase9.py`、`tests/test_phase9b_validation_gate.py`、`tests/test_phase9b_job_recovery.py`、`tests/test_phase9b_multi_instance.py`，以及实际受影响的旧 Lifecycle/Runtime 测试。Phase15-B 报告的 26/24/8/4/10 passed 仅是历史证据，不能代替 Phase15-C 回归结果。

生产验收必须包含真实向量后端、真实 validation endpoint 与可观测 Promote/Rollback canary；离线替身通过不足以关闭 Phase15-C4。

本次新增 AC-29/30 为 C1 isolation 的设计与后续实现阻断项，AC-31 为权限作用域及维护兼容阻断项。既有可参考回归文件包括 `tests/test_vector_backend_api.py`、`tests/test_qdrant_e2e_migration.py` 和 `tests/test_phase15b_unified_document_lifecycle.py`；不假定它们已覆盖全部新断言，后续阶段需补齐证据。本次文档修订不运行这些测试。

## 18. Performance Targets

**所有数值均为 Target / Not Yet Measured（待压测确认目标），不是当前系统测量结果或既有 SLA。**

| 指标 | 目标或初始候选配置 | 测量/成立条件 |
| --- | --- | --- |
| 提交延迟 | P95 < 1 秒，excluding upload transfer | 从完整请求输入接收完毕至 202 发出，包含校验与持久提交；不得把构建等待排除后仍在请求内运行 |
| 查询干扰 | 后台更新负载下 query P95 increase ≤ 10% | 相同硬件、数据、查询集合与并发，比较无后台更新基线与受控更新负载，记录重复运行分布 |
| 调度 | 轮询候选间隔 1–3 秒并抖动 | 有空闲执行槽、可用依赖、非 busy KB；不得被占用队首无限延迟 |
| 心跳与 Lease | heartbeat 10 秒；TTL 60 秒 | 初始候选参数；压测长阶段、DB 延迟和事件循环阻塞后确认，TTL 必须容纳正常抖动 |
| 过期扫描 | 间隔 15 秒 | 包括启动后才过期的任务；不以 heartbeat 陈旧单独抢占 |
| 恢复领取延迟 | 租约到期后一个 sweep 周期 + 一个 poll 周期 + 必需退避 | 不含既定 next_run_at 等待、容量不足或外部不可用时间；分别记录调度与重建完成时间 |
| 取消确认 | 下一个安全 checkpoint 加状态提交时间；不承诺固定秒数强停 | 记录申请/确认时间及不可中断调用剩余时长；超出阶段预算必须可见并告警 |

C4 压测前固定并记录：数据库类型、CPU/内存、持久存储方式、文档大小和数量、并发 KB、任务操作比例、查询基线、Embedding/向量服务限流与 Worker 并发。负载未定义不得宣称达标。

指数退避基准、上限、抖动边界、阶段调用超时、总执行预算和最大并发属于 C1/C2 参数契约，在 C4 用实际负载确认。无论参数如何调整，都不得越过 max_attempts、attempt isolation 或 fencing。

## 19. Migration & Compatibility

### 19.1 历史 Job 回填

- 先增加兼容读取能力，再在受控禁领窗口回填 execution_status；未完成回填的记录不进入新 Poller。
- ready/promoted/rolled_back：按已有验证/发布事实确认历史构建成功，不重放；时间缺失保持未知，不伪造完成时间。
- building：结合当前 Lease、有效 checkpoint、完成结果及 Candidate 指纹分类为仍运行、待恢复或已构建；禁止一律 RUNNING，也禁止仅因有 Candidate 就 SUCCEEDED。
- validating：检查已有构建/验证事实，可信时回填 SUCCEEDED，继续由显式验证机制负责；不放入构建恢复队列。
- failed：区分构建失败与构建成功后的验证失败，分别映射 FAILED 与 SUCCEEDED；证据不足则隔离对账。
- cancelled：区分构建前/中取消与构建成功后放弃候选，分别映射 CANCELLED 与 SUCCEEDED。
- pending/recovery_required：校验输入可恢复性、取消请求与预算后纳入队列；缺失输入不得“尝试一下”执行。
- legacy succeeded：仅凭可信完成证据兼容为 SUCCEEDED；没有证据时不直接标记可验证或已上线。

历史 attempt/retry_count 可能采用不同计数方式，保留原值/迁移依据并明确后续预算，不用简单公式伪造过去执行历史。所有回填结果必须满足第 11 节或进入受控隔离，不改变 Active。

### 19.2 入口及状态兼容

保留现有 status 枚举、API 命名空间和 Legacy task_id。新增 execution_status 使旧客户端可逐步迁移；解释说明必须清楚，不能删除 ready/promoted/rolled_back 以换取更简单六态。

LifecycleTask 文档 handler 与 api.py 启动恢复统一转为提交/关联/对账职责，不继续同步执行 UpdateJob。其他非文档 Legacy 任务不纳入本期通用改造，但不能成为新文档构建的备用发布通道。

迁移还应验证 Source/Candidate/GC 引用保留，确保失败重试不会删除已发布或回滚所需的文件。此为兼容与安全验收，不扩展新的存储生命周期产品。

## 20. Rollout / Rollback

1. **受控启用**：用明确 feature flag 或等价配置按受控部署/KB 开启。默认不开启未完成回填的数据；选取可演练 KB 做 canary。新 Worker 与旧同步入口使用同一所有权规则。
2. **部署**：开发允许 API lifespan 内 embedded worker；推荐同一代码库独立 Worker Process。API 与 Worker 共享可靠输入存储，无外部队列。
3. **运行观测**：记录 queue、heartbeat、claim、恢复、取消、查询干扰和现有发布指标，核对 Active 不变量。
4. **停止领取**：关闭消费入口后不再 claim；对已运行任务继续受控心跳并等待安全 checkpoint。
5. **排空或逻辑隔离**：在运维窗口内协作排空；无法及时排空则隔离旧 Candidate 的有效提交/发布资格，并在确认所有权失效后交由恢复。不得通过强杀线程/kill request 实现取消。
6. **回退执行模式**：仅在旧 owner 不再有效后启用同契约的同步兼容路径；同一 Job 不得同时被后台 Worker 和同步路径有效执行。
7. **保留事实**：现有 Job、attempt 摘要和 Candidate 不删除。禁用 Runtime 不 rollback Active，不执行破坏性 schema 回退；知识回滚仍由既有显式授权入口完成。

回退成功判据为 AC-25，另验证已完成任务没有因停用/启用反复执行，旧客户端仍能查询任务事实。

## 21. Risks

| 等级 | 风险 | 控制与停止条件 |
| --- | --- | --- |
| P0 | Worker claim 后服务再次 claim/取同一所有权 | C1 所有权契约测试失败即停止 C2 |
| P0 | 旧 Worker 的失败分支/元数据提交未受 fencing 保护 | success/failure/checkpoint 全路径拒绝陈旧写；任何一条可覆盖新 attempt 均阻断上线 |
| P0 | 复用旧 attempt namespace，外部写入污染可发布 Candidate | 独立 workspace/namespace；旧 Candidate 不可验证/Promote；AC-08/09 不通过停止 |
| P0 | parsed current 共享写入使新 attempt 消费旧 Worker 的解析结果 | C1 必须覆盖 parsed staging、读取绑定和元数据 fencing；AC-29/30 不通过停止 |
| P0 | 把文档 Promote sole authority 扩大为全局 activate 禁令，误伤显式迁移/维护 | 五类文档旁路负向检查与迁移/维护正反向回归分开；AC-31 不通过停止 |
| P0 | execution success 被解释成 ready 或自动上线 | 矩阵、显式 Gate 与接口展示共同约束；发现自动 Validation/Promote 停止 |
| P0 | Cancel/Retry 与 Promote 竞态导致取消后上线 | 条件更新和现有 KB Lease 串行化；竞态验收不通过停止 |
| P0 | 输入丢失或 stale base 导致更新错误版本 | 持久快照和 base 检查；不得自动 rebase 或猜测输入 |
| P1 | 长事务/事件循环阻塞造成误过期或取消不可落库 | 短事务和独立心跳；AC-17 及性能验收未通过不进入生产关闭 |
| P1 | 重试风暴、预算耗尽仍挂起、重复外部费用 | 持久退避、有界次数、终态及运营指标；不宣称 exactly-once |
| P1 | 队首阻塞或 Worker 抢占资源影响问答 | 有界轮询、执行槽与资源限额；KB B 调度/查询干扰验收 |
| P1 | 历史状态回填错误或 Legacy 双执行 | 按证据迁移、单一恢复路径与回归；未解释历史记录不纳入运行 |
| P2 | 仅阶段提示不足以估算剩余时间 | 本期明确不提供精确百分比/ETA，后续 Admin Experience 评估 |
| P2 | offset 分页在新任务持续插入时发生页面漂移 | 稳定排序和真实 total；文档说明刷新语义，不引入本期游标平台 |

未知生产拓扑、负载、限流和阶段超时参数属于待确认运行条件，须在对应阶段 stop gate 前落实，不能以待确认项长期替代验收。

## 22. Development Breakdown

本节为后续开发拆分，不是当前执行授权。四阶段顺序推进，任何阶段 stop gate 未通过不得进入下一阶段。Phase15-B 的安全不变量贯穿全部阶段。

### 22.1 Phase15-C1 — Runtime Contract & Ownership

- **Goal**：冻结双状态、claim once、输入和事务契约，使后台执行可安全接入。
- **Scope**：FR-01/04/05/07/13/14 的契约与必要数据迁移设计；状态矩阵、幂等、错误分类、重试参数、历史回填策略；识别所有未 fenced 的关键提交和长事务边界；明确 FR-14a per-attempt parsed staging、完整性登记与候选读取绑定，保留显式后端迁移/维护契约。不启用生产消费。
- **Acceptance**：原子 claim 与上下文传递设计无二次领取；每种输入可跨 Session 重建；字段复用清晰；矩阵覆盖全部现有枚举；兼容策略及数据库/输入存储选择已记录。后续实现需通过 AC-04/08/22/23 及 AC-29/30/31 的相应测试；只隔离最终 Candidate/Qdrant 而未隔离 parsed artifact 不算通过。
- **Stop gate**：所有权、事务或输入无法证明安全；历史 ready/promoted 可能重放；parsed/Candidate 产物或消费引用不能隔离；失权 Worker 仍可写共享 current/覆盖新解析输入；维护路径被全局 sole-authority 禁令误伤；参数契约仍缺少有限边界。任一存在则停止 C2。

### 22.2 Phase15-C2 — Background Worker Runtime

- **Goal**：完成五类操作 202 持久提交与后台 Candidate 构建闭环。
- **Scope**：Poller、Worker/Runner 接入、执行槽、独立 heartbeat、阶段查询、Legacy Adapter 收敛；embedded 开发与独立 Worker 部署模式；真实列表分页/筛选。
- **Acceptance**：AC-01 至 AC-06、AC-11、AC-14、AC-16/17、AC-21 通过；构建后停在 SUCCEEDED，不自动 Validation/Promote；服务内部不重复 claim。
- **Stop gate**：HTTP 仍等待构建、心跳被长事务堵塞、Legacy 仍直接执行、busy KB 阻塞其他 KB、Candidate 进入在线检索。任一存在则停止 C3/生产启用。

### 22.3 Phase15-C3 — Recovery & Cooperative Control

- **Goal**：崩溃、失权、临时失败、取消和并发控制下可解释地恢复或终止。
- **Scope**：启动与周期 Sweeper、有限 backoff、retry/resume、cancel_requested_at、安全 checkpoint 停止、Candidate 对账、历史回填和停机隔离。
- **Acceptance**：AC-07 至 AC-10、AC-18/19/20、AC-23、AC-25 至 AC-28 通过；旧 owner 全关键写入被拒绝；外部调用重复只影响隔离产物；耗尽预算终止。
- **Stop gate**：取消通过强杀实现、旧 Candidate 仍可发布、恢复依赖请求内数据、计数可被重试接口清零、任何未解释重复有效执行。任一存在则停止 C4。

### 22.4 Phase15-C4 — Production Acceptance

- **Goal**：证明运行时在目标环境中安全、可运营，并完成受控启用与回退演练。
- **Scope**：全量验收清单、既有回归、真实向量/验证依赖 canary、性能与资源干扰测量、日志指标、部署配置及 runbook。
- **Acceptance**：全部 AC 有通过证据；Phase15-B/Phase9 回归通过；真实 Validation/Promote/Rollback 保持 Gate/fencing；性能目标在约定负载达标或经产品/架构负责人明确接受修订目标；停止领取与逻辑隔离演练完成。
- **Stop gate**：P0/P1 验收未通过、只有离线替身、实际负载或存储拓扑不明、性能数据缺失却宣称达标、回退会隐式改变 Active。不得关闭 Phase15-C。

## 23. Definition of Done

Phase15-C 只有同时满足以下条件才可关闭：

- 五类操作统一持久提交并返回可查询 job_id；暂停 Worker 仍可靠接受，启动后可领取。
- UpdateJobRepository 是唯一持久队列；新 Runtime 不依赖 LifecycleTaskExecutor 核心执行，不存在双 claim 或重复发布路径。
- 六态 execution_status 与全部 lifecycle status 满足第 11 节矩阵，历史回填可解释，已验证/发布/回滚记录不重放。
- 持久输入、短事务、独立心跳、attempt 隔离和所有关键提交 fencing 均通过故障注入验证。
- parsed artifact staging/消费引用隔离通过 AC-29/30；显式 backend migration/maintenance 保持原行为并通过 AC-31。两项均为 P0，不以“Parser 不在范围内”或“所有 activate 都必须删除”规避。
- Retry/backoff/limit、resume 兼容、协作取消、查询与列表分页全部实现且行为可审计。
- 构建成功停在 Candidate；Validation 和 Promote 保持显式，现有 Gate、Rollback、Active 检索稳定性不退化。
- 第 17 节全部验收有本阶段证据，Phase15-B/Phase9 相关回归通过；真实后端 canary 与运行回退演练通过。
- 第 18 节目标有实际测量与负载说明，未测项目不能记为通过；运行参数、部署/存储前提和操作说明已固定。
- 无未解决的 P0/P1 发布阻断问题；产品负责人、架构负责人及测试/部署责任人确认关闭。

PRD 完成只表示需求文档可审阅，不表示上述 DoD 达成，也不授权自动进入 C1。当前任务完成后停止。

## 24. Future Work

### 24.1 Phase15-D — Knowledge Observability / Admin Experience

围绕知识更新与管理体验评估更完整的任务历史、运营可视化、异常定位、管理员交互和状态推送。Dashboard/SSE/WebSocket 等能力在该阶段重新定义需求，不作为本期上线的隐含依赖。显式验证后台化如有需求需单独评审，本 PRD 不承诺自动验证或自动发布。

### 24.2 Phase16 — Retrieval Intelligence

后续阶段方向固定为 Retrieval Intelligence，具体能力及验收由该阶段独立定义。当前不启动 Retrieval 优化，也不将 Phase16 改名为 Knowledge Platform Runtime。

Knowledge Platform Runtime、通用任务平台、多租户调度、优先级及自动扩缩容均未在本文获得立项承诺。运行层的可复用代码不构成扩大产品范围的理由。
