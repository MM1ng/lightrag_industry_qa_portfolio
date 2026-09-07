# Phase15-C1 Runtime Contract & Ownership Design Report

| 项目 | 内容 |
| --- | --- |
| 日期 | 2026-09-07 |
| 范围 | C1 模型数据契约、ownership 值对象、输入快照与 parsed isolation 设计、非活动迁移草案、测试骨架 |
| 代码基线 | `0686940bd41989e47805a38f17eeab2c8aa6aade` |
| 开始时 HEAD | `c783240d0f0d4c79de6ee979d08f93000f11cbda`；相对代码基线只有已批准的两份文档修订，保留不回退 |
| 依据 | [PRD v1.1](phase15c-knowledge-update-runtime-prd.md)、[Acceptance Checklist](phase15c-acceptance-checklist.md) |
| 状态 | C1 限定范围的模型/设计交付；未启用 Runtime，未应用 migration，不代表 C1 全部运行验收通过 |

## 1. 交付及非目标

本次采用“先固定契约，再接入执行”的顺序：建立小型契约测试骨架；实现 additive ORM 字段与不可变 ownership 值对象；编写非活动 migration draft；冻结输入与 parsed staging 设计；运行静态检查和隔离的模型测试；仅提交这些文件。

已实现内容：

- `UpdateJobExecutionStatus` 六态；四个可空 ORM 字段。
- 合法执行状态值和 lifecycle/execution 结构组合的数据库 CHECK。
- `CandidateAttemptReference` 与 `ClaimedExecutionContext` 两个纯模型值对象。
- `migrations/drafts/phase15c_c1_execution_contract.py`，不进入 Alembic revision chain。
- 模型契约测试及明确跳过的后续服务接入/parsed 失权场景骨架。

只完成设计、尚未接入：持久输入快照写入与不可变校验、每次 attempt 的 parsed staging 写入/读取绑定、将现有 claim 结果传入业务服务、所有关键提交的持久 fencing 校验。

没有创建或修改 Worker、Poller、async loop、retry/recovery 执行、API 行为、Parser/Chunk/Embedding 算法。未启动任何后台执行。现有同步路径及其共享 current 写入仍存在；不将纯数据对象误报为已经解决运行隔离。

## 2. UpdateJob 字段审查

### 2.1 现有字段覆盖

依据 `src/industrial_rag/db/models.py::UpdateJob`，以下均复用，不新增同义列。

| 领域 | 当前字段 | C1 结论 |
| --- | --- | --- |
| 身份与归属 | id、knowledge_base_id、operation、document_id | 已覆盖五类操作身份；无独立 RuntimeJob |
| 代际关联 | base_generation_id、candidate_generation_id | 已有基准/当前候选；缺少持久的 attempt→旧候选历史语义，设计放入有界结果摘要 |
| 内容摘要 | old_content_sha256、new_content_sha256 | 已有哈希；不足以单独重建 source version、旧文档对象、冻结文档清单 |
| 生命周期 | status | 保留全部 12 个既有枚举，不改名、不删除、不把 ready/promoted 简化成执行成功 |
| 执行阶段 | current_stage、checkpoint | 可作为版本化快照/检查点容器；当前普通覆盖写法未提供不可变输入保护，需要后续接入 |
| 错误 | error_code、sanitized_error_message | 已覆盖脱敏错误，不新增原始 error_message |
| 重试统计 | attempt、max_attempts、retry_count | 已有；C1 不改写旧计数或执行逻辑。新契约以成功领取计 attempt，输入重排队不计一次执行 |
| claim | worker_id、lease_token、fencing_token、claimed_at | 已有；值对象携带它们不能代替当前 Job + KB Lease 的持久验证 |
| 存活 | heartbeat_at、lease_expires_at | 已有；不新增 heartbeat 列，不启用续租循环 |
| 时间 | created_at、updated_at、started_at、finished_at | 已有；旧生命周期 finished_at 与构建执行终态时间分开 |
| 追踪与审批 | request_id、trace_id、created_by、approved_by | 已有；request_id 无已证明的幂等唯一约束，不能直接宣称防重复提交 |
| 输出 | metrics、result | 已有；承载有界 attempt 摘要与输出，不放完整日志 |

Document 另有 `version`、`file_hash`、`file_path` 等来源信息；snapshot 固定这些值，不假设已存在新的 SourceVersion 表。文件路径的存在不等于已具备不可变源版本保证。

### 2.2 本次补齐字段

| 新字段 | 类型及默认 | 约束 |
| --- | --- | --- |
| execution_status | `UpdateJobExecutionStatus`，可空，无 PENDING 默认 | 六态大写字符串；NULL 专用于 expand 阶段 legacy/unclassified，不能被未来 Runtime 领取 |
| next_run_at | timezone-aware DateTime，可空 | 只建立列；C1 不安排退避/领取 |
| cancel_requested_at | timezone-aware DateTime，可空 | 只建立取消意图列；C1 不实现取消处理 |
| execution_finished_at | timezone-aware DateTime，可空 | 与 lifecycle finished_at 分离；C1 不自动填值 |

执行状态为 PENDING、RUNNING、RECOVERY_REQUIRED、SUCCEEDED、FAILED、CANCELLED。数据库存储采用非 native enum 的 VARCHAR(17) + CHECK，避免修改既有 PostgreSQL lifecycle enum；SQLAlchemy 还校验 ORM 字符串值。

### 2.3 仍缺失的运行契约

没有新增快照列、JobAttempt 表或 JobQueue。持久输入 schema、输入摘要、幂等唯一性、attempt 产物绑定和 checkpoint 合并语义在本文定义，但没有在现有 service/repository 中写入或执行。将它们接入前，不能把现有 pending Job 直接当作新 Runtime 可执行任务。

## 3. Lifecycle 与 Execution 数据契约

### 3.1 结构组合

非 NULL 的 execution_status 允许以下组合；其余组合由 `ck_update_jobs_lifecycle_execution` 拒绝。

| execution_status | 可对应 lifecycle status |
| --- | --- |
| PENDING | pending |
| RUNNING | claimed、running、building |
| RECOVERY_REQUIRED | recovery_required |
| SUCCEEDED | building、validating、ready、succeeded、failed、cancelled、promoted、rolled_back |
| FAILED | failed |
| CANCELLED | cancelled |

这与 PRD 12×6 矩阵的结构条件一致。CHECK 只验证字段组合：SUCCEEDED + failed 仍须证明失败来自后续验证；SUCCEEDED + cancelled 仍须证明构建完成后放弃候选；ready/promoted 仍需已有验证/发布依据。数据库组合合法不代表所有权、冻结产物或 canonical evidence 合法。

SUCCEEDED 只表示构建完成，不表示验证/发布/在线。维护路径的激活权限边界不由此矩阵扩展或收回。

### 3.2 NULL 兼容策略

本次不能修改旧同步执行器，而旧执行器只维护 status。因此 execution_status 不设自动 PENDING 默认，旧创建/更新路径在新建测试 schema 上仍保留 NULL，不因 lifecycle 推进而制造冲突组合。这是 PRD §19 的 expand/回填过渡状态，不是新增第七个 Runtime 状态。

后续运行接入必须显式生成正确的非 NULL 双状态；所有写入者完成接入之前不得给旧 Job 批量默认 PENDING。未来 Poller 不得查询 NULL 记录；本次没有 Poller，也没有变更现有 UpdateJobWorker 的查询行为。

NULL 也不是安全授权：未知历史只可受控分类/隔离，不能被新 Runner 构建、验证或发布。保留已经存在的 Active，绝不以矩阵修复为由回滚 Active。

## 4. Migration Draft 方案

草案：[phase15c_c1_execution_contract.py](../../migrations/drafts/phase15c_c1_execution_contract.py)。文件位于 `migrations/drafts`，没有 revision/down_revision 标识，不会被当前 Alembic versions 目录发现；当前 `upgrade head` 不会应用此草案。

草案冻结 SQL 与模型一致，不导入正在变化的应用模型。拟添加四个 nullable 列、状态值 CHECK 和组合 CHECK；不改 lifecycle enum、不改变 operation、不回填数据、不新增调度索引。索引在后续查询接入和查询计划验证时决定。

### 4.1 后续批准后的部署顺序

1. 核对实际 migration head（当前基线为 `f15b0a1c2d3e`）、数据库类型和备份；停止旧写入，避免 schema 转换与未分类旧任务同时活动。
2. 审核草案并创建正式 revision；SQLite batch alter 需在副本验证行数、外键、原约束和索引保留，PostgreSQL 验证 VARCHAR/CHECK 和事务行为。
3. 在维护窗口应用增量 schema；全量既有记录保持 execution_status=NULL，其余新列同样 NULL，不改变 status、generation 指针和任务输入。
4. 部署兼容模型和之后获批的服务接入；按证据分类历史记录，只有明确具备新输入契约的任务才进入后续 Runtime 调度。
5. 明确 ready/promoted/rolled_back 只补齐完成事实，building 依据租约/产物分类；验证失败与构建失败分开。无法分类保持隔离，不凭状态猜测。

本次 **未执行** 上述迁移、回填或 schema 修改。测试仅在进程内创建一次性 SQLite 内存数据库，不打开项目数据库。

### 4.2 部署兼容限制

**新 ORM 不能直接部署到尚未添加四列的旧数据库。** SQLAlchemy 普通实体查询也会选择新增列；既有 `create_all` 不会升级已有表。因为用户仅授权 migration draft，本次交付是非部署就绪的 C1 基础，必须先批准正式 schema 升级，不能仅 push 后启动应用就声称兼容。

Phase15-B 兼容承诺是保留旧 lifecycle enum、既有列和语义，并在已扩展 schema 上允许 legacy NULL；不是承诺未升级 schema 的旧数据库可以直接使用新模型。

应用回退优先保留 additive 列；草案的 proposed_downgrade 明确拒绝破坏性降级，不能删除已记录的执行历史。需要物理移除列时必须单独审核数据保留，C1 不提供自动擦除动作。

## 5. Ownership Context

### 5.1 已实现的模型值对象

`ClaimedExecutionContext` 为 frozen、slots、keyword-only dataclass：

| 字段 | 契约 |
| --- | --- |
| job_id | 已持久 claim 的 UpdateJob id，不接受空身份 |
| knowledge_base_id | KB 身份，必须与 Job/Lease/候选一致 |
| attempt | 已成功领取的尝试序号，正整数，不通过重排队伪增 |
| worker_id | 当前 owner 身份 |
| lease_token | 必填现有 Lease token；默认 repr 隐藏，不作为公开响应 |
| fencing_token | 当前单调 fencing token，正整数；正整数本身不是有效性的证明 |
| lease_expires_at | 带时区的 Lease 截止时间；可携带历史/过期时间，不在值对象构造时以本地时钟伪判当前有效 |
| candidate_reference | 可空的 CandidateAttemptReference；分配候选前为 None，分配后绑定同 Job/KB/attempt |

`CandidateAttemptReference` 固定 job_id、knowledge_base_id、attempt、candidate_generation_id。跨 Job、KB 或 attempt 的绑定会在上下文构造时被拒绝。候选分配后创建新的不可变上下文副本；不能原地改 attempt/token。该引用不含用户路径，也不自动获得对 Candidate 的写权限。

### 5.2 不具备的能力与后续传递方式

值对象没有 claim、execute、heartbeat、renew、retry 或 recovery 方法。构造成功只代表字段结构合法，不能证明调用者真的取得 Lease；日志 repr 隐藏 token 也不代表通用序列化已脱敏，不得直接 asdict 后返回 API。

后续接入方式：由现有 UpdateJobWorker/KBLeaseService 唯一领取路径生成上下文 → Runner 持有该上下文 → IncrementalUpdateService 消费既有所有权。服务不能在内部第二次 claim 或再获取同一生命周期所有权。现有 `ClaimedUpdateJob` 尚未转换或替换，本次也未改变 `_execute_persisted_job` 的旧同步行为。

所有关键 checkpoint/success/failure/Document 元数据提交，必须在同一短事务内同时检查：当前 Job id/attempt/worker/token/fencing/执行状态、当前 KB Lease 身份与有效期、Candidate 归属、取消请求及输入快照摘要。仅检查上下文副本、仅检查过期时间或只在成功分支检查都不满足契约。

本次没有实现这些持久校验器，因此不能将 dataclass 验证测试当作 AC-08 陈旧提交拒绝的运行证据。所谓“禁止 Worker 绕过 Lease”在此冻结为接入硬约束，现有 Worker 未被赋予新的旁路能力。

## 6. Persistent Input Snapshot v1

### 6.1 持久化位置与不可变性

设计采用 `checkpoint.input_snapshot` 保存版本化不可变输入，`checkpoint.stage` 保存可变阶段，`result.attempts` 保存有界尝试摘要。容器名称是本次冻结的设计契约，不表示当前 repository 已支持保留式合并。

接受新 Runtime Job 时必须在提交事务前完成 source staging，在同一 Job 提交中写入完整 input_snapshot 和摘要。后续 checkpoint 更新只替换阶段数据，必须保留原输入和摘要；禁止整对象覆盖丢失 input_snapshot。拒绝可变文件、缺失旧文档、来源摘要不匹配，不从当前共享目录猜测输入。

输入摘要为规范 JSON（字段排序、固定 UTF-8、明确 null/空集合语义、不包含临时时间）的 SHA-256；原始来源文件另用内容 SHA-256。后续应将实际 canonicalization 及唯一性规则接入 Repository，本次不实现序列化执行器或迁移输入数据。

### 6.2 通用字段

| 键 | 必须表达的事实 |
| --- | --- |
| schema_version | 固定为 1；未知版本拒绝执行，不猜测转换 |
| operation / knowledge_base_id | 对应 Job，不允许 checkpoint 改变操作含义 |
| base_generation | 固定 generation_id、manifest/config 摘要；空 KB 首次 add 可显式为 null，不能用 null 表示未知基准 |
| source / target / replacement | 以下操作表规定对象；每个来源固定 Document id、version、内容摘要与服务端生成的持久存储引用 |
| document_bindings | 基准文档及已冻结父/子块引用；reindex 必须完整，不在执行时重新列出“当前”活动文档 |
| configuration | 已批准 parser/chunk/embedding 身份、版本/维度及配置指纹；缺失值显式说明，不能以当前配置静默补齐 |
| input_digest | 不包含该键本身的规范输入摘要 |

request_id/trace_id/created_by 继续使用现有 Job 字段；attempt、worker/token、next_run_at 等可变执行信息不纳入不可变业务输入。重复提交键须另具唯一约束，不能单靠 input_digest 或现有 request_id 假定防重复已完成。

### 6.3 五类操作

| 操作 | 必须冻结的输入 | 可空与禁止行为 |
| --- | --- | --- |
| add | 新 Document/version、source 引用/hash、基准清单、原配置 | 首个 KB 可无 base；不能仅保留 HTTP bytes，不能在后续读取同名可变文件 |
| replace | 新 source/version 与被替换 target id/version/hash、基准清单 | target 不可空；不依赖请求中的 old_doc 对象，不能在重启后按名称猜测替换谁 |
| delete | target id/version/hash、基准 generation/清单及冻结块引用 | 不需要新源文件；构建只移除候选内容，不删除 Active/source 物理数据 |
| reparse | 指定 document_id/version/source hash、原 parser/chunk/embedding 指纹、基准清单 | document_id 不可空；不升级算法；解析输出必须在本 attempt staging |
| reindex | 固定 base generation 及完整活动文档/source/parsed 绑定、原配置 | 不按执行时“最新 Active”重列清单，不把 per-document 兼容路由重定义为单文档索引；不改变模型或策略 |

未变化文档优先从基准 Generation 冻结快照读取，不重新解析。没有 Active 的兼容输入也必须在新任务接受前固定为不可变引用；不能在构建途中回退共享 current。

### 6.4 失配与历史记录

source/hash/version/config/base 不一致均不能静默 rebase 或回退当前配置。当前旧代码未完整持久化这些输入，legacy NULL 任务需要受控分类，不可直接作为新快照任务恢复。本次不实现重试/恢复；错误分类与重提交规则沿用 PRD。

## 7. Parsed Artifact Isolation C1 Design

### 7.1 当前风险

基线 `_parse_document_pymupdf` 直接写 `parsed/documents/{doc_id}/current/child_chunks.jsonl` 与 `parent_chunks.jsonl`，并更新 Document；`_candidate_snapshot_pairs` 经 load_child_chunks/load_parent_chunks 读取共享 current。另一 ParseService 路径也有 temporary parse → current swap。

仅隔离 Candidate workspace 或 Qdrant namespace 不能阻止 A 失权后覆盖 B 的解析输入。数据库回滚不能撤销这种文件覆盖；在“写 current 之后”再检查 Lease 也无效。

### 7.2 冻结的命名与引用契约

per-attempt 解析目录使用服务端生成的隔离键：

```text
<kb parsed root>/attempts/<job_id>/<attempt>/<document_id>/staging/
<kb parsed root>/attempts/<job_id>/<attempt>/<document_id>/artifacts/
```

这是未来实现的布局设计，本次不创建目录、不写解析文件。所有 id 段由可信身份生成和校验，拒绝路径穿越；相同 doc 的 A/B 尝试不得共享可写目录。child/parent JSONL、清单及同次解析所需附件全部归属于本 attempt。

流程：持久化 attempt 意图 → 本 attempt staging 写入 → 校验完整性及内容摘要 → 封存为本 attempt artifacts → 在 fenced 短事务中登记 artifact reference → 由 Candidate 消费这个确定引用。封存文件存在但登记事务失败时只产生未登记孤立产物，不代表任务成功。

artifact reference 至少包含 KB/job/attempt/doc/source version、相对存储键、parent/child 清单摘要、配置摘要和封存版本；只能指向同 attempt 的完整产物。Candidate/Embedding/indexing 读取显式引用，不在消费时解析 current alias，也不在缺失新引用时回退旧 attempt。

### 7.3 失权和取消

- A 失权后即使外部解析继续返回，也只能写入 A 的隔离目录；不能写 B 的目录或更新 B 的引用。
- 当前有效 owner 才能登记 parsed 引用及 Document 元数据；旧成功/异常分支一律不能覆盖。
- 取消或失权的产物不能被选为可验证 Candidate；仍按既有受控 GC 保留/回收，不物理删除 Active 依赖。
- 新后台 attempt 永不写共享 `parsed/documents/{doc_id}/current`。兼容/维护读取若仍需要 current，只能使用既有完整稳定输入；不得让它指向半成品 staging。任何未来兼容投影发布须单独定义权限，C1 不实现投影更新。
- Runtime isolation 覆盖 reparse 以及复用相同写链路的 add/replace；不改变 parse_pdf、ChunkerConfig、块内容策略、Embedding 模型或维度。

### 7.4 C1 验收边界

本次只确认设计与模型引用约束；AC-29/30 的实际 A/B 解析故障注入仍未执行。测试骨架明确 skip，不能记为 passed。服务接入前必须实现上述生产路径并验证旧 A 不污染 B；仅有本报告或 dataclass 不足以越过 C1/C2 的隔离 stop gate。

## 8. Document Promote 与维护边界

Promote sole authority 只适用于 add/replace/delete/reparse/reindex 的文档新内容发布。`migrate_to_qdrant`、`rollback_to_nano` 及既有显式维护/rollback 保留其入口、前置条件和 activate 行为，不强制纳入 UpdateJob 执行状态机。

本次没有修改 IndexService、maintenance handler、activate 方法或其鉴权；没有新增全局发布禁令。后续接入必须分别验证五类文档无旁路与显式维护正反向兼容，不能用全局拦截 activate 代替验收，也不能借维护路径绕过文档 Gate。

## 9. 验证与证据

### 9.1 当前运行结果

- 契约测试先行：新增模型尚不存在时，测试在 CandidateAttemptReference 导入处按预期失败；随后完成最小模型实现。
- `python -m ruff check src tests`：通过。第一次发现新增测试 import 排序问题，已仅修复新增测试导入。
- `python -m pytest tests/test_phase15c_c1_contract_models.py tests/test_vector_index_generation_models.py -q`：**34 passed, 2 skipped**。
- migration draft 的 Python AST 可解析，冻结的组合 CHECK 与 ORM 定义一致，且无活动 revision 标识；没有调用 proposed_upgrade。
- 独立只读代码复核未发现需修正缺陷；再次确认 schema 升级前不可部署、旧写入路径不能提前使用非空状态，以及条件证据/真实 ownership 仍需运行接入验证。
- 检查内容：全部旧 lifecycle 枚举、每种旧 status 保持 NULL 的持久化兼容、执行六态相关组合存储、非法值/组合的 DB 拒绝、上下文不可变/脱敏/候选绑定/必填字段，以及既有 Generation 模型绑定。

两个 skip 分别是未来单 claim 服务接入和 parsed stale-writer 故障注入；它们是骨架，不是已实现功能。未运行完整 Phase15-B/Phase9 服务回归、真实 Qdrant/Validation canary 或性能压测，因此不宣称这些验收通过。

### 9.2 环境与数据影响

使用项目现有 `.venv/Scripts/python.exe`；模型持久化测试只使用 `sqlite:///:memory:` 并释放引擎。未读取生产数据库连接配置、未执行 Alembic upgrade、未调用模型/检索/Embedding、未创建后台循环。

## 10. 后续批准前的 Stop Gate

1. 正式 schema migration 未获批准并验证之前，新 ORM 不可部署到旧 schema；本次 draft 不会自动修复这一限制。
2. 现有服务尚未接受 ClaimedExecutionContext；不得把纯值对象当作单 claim 已接入，也不能直接把新 execution_status 非 NULL 写入旧执行路径。
3. persistent input_snapshot 不可变写入、checkpoint 合并与幂等唯一性尚未实现；不得进行新 Runtime 恢复。
4. parsed staging 尚未接入生产解析/消费链路，AC-29/30 仍未通过；共享 current 风险仍是后续接入阻断项。
5. 不因本次模型测试通过自动进入 C2。只提交 C1 指定范围文件并 push，随后停止。

## 11. 变更文件

| 文件 | 角色 |
| --- | --- |
| `src/industrial_rag/db/models.py` | additive ORM 数据契约及纯 ownership 模型 |
| `migrations/drafts/phase15c_c1_execution_contract.py` | 非活动迁移草案，明确部署和破坏性回退边界 |
| `tests/test_phase15c_c1_contract_models.py` | 小型模型验证及两项 skip 的未来运行接入骨架 |
| `docs/product/phase15c-c1-contract-design-report.md` | 字段审查、migration、ownership、五类输入快照、parsed isolation 设计与限制 |

未修改 Worker/Poller/服务/API/既有测试逻辑；用户原有 package-lock、venv、cache 和未跟踪实验/报告文件不纳入提交。
