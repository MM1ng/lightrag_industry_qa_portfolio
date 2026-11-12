# 项目审计报告

**项目**：`lightrag_industry_qa_portfolio-retrieval-foundation-upgrade`
**分支**：`dev/retrieval-foundation-qa-downstream` @ `ac24600b`
**审计日期**：2026-10-23
**审计方式**：只读（未修改任何源码/配置）

---

## 一、审计基线（确定性证据）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| Python 静态检查 | `python -m ruff check .` | ✅ All checks passed |
| Python 测试 | `python -m pytest -q --ignore=tests/evaluation` | ✅ **1247 passed, 23 skipped**（157s） |
| 测试收集 | `pytest --collect-only` | ⚠️ 1286 项，**3 个 collection error**（缺可选依赖 `ragas`） |
| 前端单测 | `npm run test -- --run` | ✅ **25 passed / 6 files** |
| 公开仓库安全检查 | `python scripts/check_public_repo.py` | ✅ PASS（1459 files） |
| 密钥入库 | `git ls-files "*.pdf"` / 敏感文件扫描 | ✅ 无 PDF、无 `.env`、无硬编码密钥 |
| 工作区状态 | `git status` | ⚠️ **40 个已改文件（+848/−217）+ 20+ 未跟踪文件** |

**未覆盖范围**（不具备条件，本次未验证）：
- 真实 LLM / Qdrant / MinerU E2E（需 `IRA_QDRANT_E2E=1` 等 opt-in 开关与真实凭据）
- 前端 Playwright E2E
- README 中的检索指标（见 M7，部分产物未入库）

---

## 二、总体结论

**综合健康度：6.5 / 10（B−）**

这是一个工程素养显著高于同类 RAG 项目的仓库：分层方向正确、认证密码学实现规范（`secrets.compare_digest`）、SQL 全量走 SQLAlchemy 表达式（无拼接）、路径参数有强正则校验、错误模型统一、关闭/资源释放路径严谨、显式 `TODO` 在 `src/` 中为 **0**。

真正的短板集中在三处：

1. **匿名公开面的边界靠字符串匹配定义**，且写入/整包下载语义超出了"只读核验"的设计范围。
2. **查询主链路零超时、全链路零限流**，把公开面直接变成成本与可用性攻击面。
3. **三个上帝文件**（2290 / 1536 / 1283 行）承载了绝大部分变更风险。

| 维度 | 评分 | 主要依据 |
| --- | --- | --- |
| 架构清晰度 | 7.5 / 10 | 无反向依赖、无循环 import；扣分于 3 个上帝文件、双运行时、4 套配置来源 |
| 安全 | 6.5 / 10 | 密码学与注入防护扎实；扣分于 fail-open 默认、无限流、整包下载、弱密钥无校验 |
| 可靠性 | 6.0 / 10 | 关闭路径严谨；但查询无超时、事务边界隐式、事件循环阻塞 |
| 可维护性 | 5.5 / 10 | 4 组重复实现、700 行单方法、13 处函数内 import |
| 测试与可复现性 | 7.5 / 10 | 1247 项测试覆盖广；扣分于安全边界零测试、证据产物未入库 |

---

## 三、高危（已逐条核验）

### H1 · 认证 fail-open：漏设一个环境变量即全员管理员

`src/industrial_rag/api.py:734-736`

```python
if service_api_key is None and admin_api_key is None:
    request.state.authenticated_actor = local_development_actor()   # role="admin"
    return await call_next(request)
```

`src/industrial_rag/auth.py:25-31` 返回 `role="admin"`。强制密钥的守卫只在特定环境生效：

`src/industrial_rag/config.py:115-118`

```python
if self.deployment_environment in {"local_staging", "staging", "production"} and (...):
    raise ValueError(...)
```

而 `deployment_environment` 默认值为 `local_dev`（`config.py:153-155`）。

**影响**：只要忘记设置 `IRA_DEPLOYMENT_ENVIRONMENT`，上传、删除、promote/rollback、GC、admin 诊断、`/metrics` 对匿名者等价于管理员可用。这是典型的"配置失误即完全失守"。

**建议**：改为 fail-closed。`local_development_actor()` 仅在显式 `IRA_DEV_INSECURE=1` 且绑定 `127.0.0.1` 时启用；启动日志打印认证模式；`/readyz` 暴露 `auth_mode`。

---

### H2 · 引用核验接口把整本手册一次性发出，`page` 参数形同虚设

`src/industrial_rag/routers/documents.py:271-295`

```python
async def get_document_source_file(kb_id, doc_id, page: int = Query(..., ge=1), ...):
    ...
    page_error = _validate_page(doc, page)   # 仅校验范围
    ...
    return FileResponse(file_path, media_type=..., filename=doc.original_file_name)
```

`page` 只做边界校验（`:99-116`），**不参与裁剪**，返回完整 PDF。该路径被列入匿名白名单：`api.py:540-545`（`path.endswith("/source-file")`）。

**注意**：匿名可读本身是设计意图（`tests/test_document_source_api.py:134` 无 `Authorization` 即断言成功）。**缺陷在于语义**——产品定位是"受限原文摘录"（README 第 32 行），实现却是整包下载。任何匿名用户遍历 `doc_id` 即可拖走全部工业手册原文。

**建议**：按 `page` 用 PyMuPDF 裁单页返回；或要求 service 凭据；`generation_id` 由可选改为必填（`:229, :276` 当前可空）。

---

### H3 · 公开路径判定用"路径段数 == 4"推断，边界脆弱且零测试覆盖

`src/industrial_rag/api.py:550-555`

```python
return (
    request.method == "POST"
    and len(path.strip("/").split("/")) == 4
    and path.startswith("/v1/knowledge-bases/")
    and path.endswith("/query")
)
```

**核验结果**：全仓搜索 `_is_public_browser_request` / `public-browser`，**测试中命中 0 条**。这是整个认证边界唯一的分流函数，却没有任何回归保护。任何新增的四段式 `POST` 子路径会被自动公开。

同时 `api.py:741-743` 把匿名者伪造成 `role="service"`：

```python
request.state.authenticated_actor = AuthenticatedActor(
    role="service", actor="public-browser", credential_type="anonymous"
)
```

而 `ActorRole = Literal["service", "admin"]`（`auth.py:14`）**没有 `anonymous`**，`require_authenticated_actor`（`auth.py:63-67`）无法区分真 service 与匿名浏览器。

**建议**：改为显式路由白名单常量表，例如 `PUBLIC_ROUTES = {("POST", "/v1/knowledge-bases/{kb_id}/query")}`，并为其补 3 类测试（命中/未命中/带凭据）；`ActorRole` 增加 `"anonymous"`，`require_authenticated_actor` 显式拒绝。

---

### H4 · 查询主链路零超时，`QA_REQUEST_TIMEOUT_SECONDS` 是死配置

`src/industrial_rag/services/query_application_service.py:189-194`

```python
result = await runtime.query(
    retrieval_question,
    mode=..., top_k=..., chunk_top_k=...,
)     # 无 asyncio.timeout / wait_for
```

`lightrag_service.py:872` 的 `await self._backend.aquery_data(...)` 同样无超时。全仓 `wait_for` 仅 3 处，均不在查询路径。

`QA_REQUEST_TIMEOUT_SECONDS` / `QA_MAX_RETRIES` 只在 `production_config.py:52-53,115-116` 定义与校验，**源码中无任何消费者**。`AppErrorCode.QA_TIMEOUT`（`api.py:159`）也无抛出点。

legacy 路径的"超时"同样无效：`runtime.py:99-102` 的 `future.result(timeout=)` 超时后 `cancel()` 无法中断已持锁运行的协程（`runtime.py:248 async with self._query_lock`）。

**影响**：上游 LLM 卡死时协程永久占用，`_query_lock` 串行化所有后续查询 → 单次上游抖动即可导致整体不可用；且无重试，瞬时 5xx 直接暴露为 502。

**建议**：在 `_execute_kb_query` 外层统一 `async with asyncio.timeout(...)`；接入 `production_config` 的 timeout/retry；超时后重建 runtime。

---

### H5 · 上传先全量读入内存，20 MB 限制在校验之后

`src/industrial_rag/routers/documents.py:145` 与 `:169`

```python
content = await file.read()      # 无上限
...
await svc.add_document(..., content=content, ...)
```

大小校验在服务层且在校验之后：`services/incremental_update_service.py:2243-2250`。全仓无 body-size 限制中间件（`Content-Length` 限制搜索 0 命中）。

**影响**：单个 multipart 请求即可把数 GB 读入进程内存 → OOM 打挂 API 与后台任务执行器。虽然上传需 admin 凭据，但结合 H1（fail-open）风险显著放大。

**建议**：中间件/反代设置 `client_max_body_size`；流式落盘并在超阈值时中断。

---

## 四、中危

| # | 问题 | 证据 | 影响 |
| --- | --- | --- | --- |
| M1 | 图谱接口使用**应用级** settings，不按 KB 隔离 | `routers/graph.py:48-60`（`request.app.state.resolved_settings`），路由为 `/v1/graph/*` 不带 kb_id | 多 KB 场景下 A 库用户看到 B 库图谱；这是正确性缺陷，不只是权限问题 |
| M2 | `/v1/tasks` 两个端点无任何 actor 依赖 | `routers/tasks.py:40-50, 53-73`，返回 `payload` / `result` / `cleanup_steps`（`:34-36`） | service 级低权凭据可读任意 KB 任务内部数据（含错误信息与路径） |
| M3 | 匿名 `POST /v1/feedback` 无认证无限流 | `api.py:548-549` + `routers/feedback.py:129-149` | 匿名可无限写入反馈表，污染评测数据、膨胀库 |
| M4 | 生命周期执行器先开 DB 会话再抢信号量 | `services/lifecycle_task_executor.py:133-146` | `max_concurrency=2` 时其余任务持连接阻塞在 Semaphore，连接池耗尽拖垮 API；`:124` 的 `create_task` 句柄未跟踪 |
| M5 | 事务边界隐式，repository 自行 `commit()` | `db/session.py:82-91` vs `repositories/kb_lease_repository.py:30,68,93`、`update_job_repository.py:145,209,248...` | 上层回滚无法撤销已提交部分，产生"半成品状态 + 锁残留" |
| M6 | `/ready` 同步端点内 `asyncio.run()` 复用全局异步引擎 | `api.py:871-920`（`:895`、`:913`） | 连接绑定在另一 loop，典型报错 `attached to a different loop`；高频探针自身成为开销 |
| M7 | README 引用的证据产物被 `.gitignore` 忽略、未入库 | `.gitignore:96` (`evaluation/**`)；`evaluation/retrieval_foundation/formal_development_effectiveness_2026-09-03.json` → `tracked=False, ignored=True` | README "Frozen A2 snapshot"（Recall@5 0.818 等）克隆后无法验证，证据链断裂 |
| M8 | 无密钥最小长度校验，本地 staging `ADMIN_API_KEY` 仅 6 字符 | `config.py:108-118` 只校验"非空 + 两者不同" | 弱密钥可被离线暴力/撞库；建议 `min_length>=32` |
| M9 | 上传落盘用固定 `.tmp` 名 | `services/incremental_update_service.py:327-330, 409-412` | 同 KB 同内容并发上传写同一临时文件，可能产出截断/混杂 PDF |
| M10 | 解析产物目录切换在 Windows 不原子且无回滚 | `services/parse_service.py:279-284, 313-317` | `current` 被移走后新目录 rename 失败即文档不可检索，`backup` 不恢复 |
| M11 | LLM 降级状态是无锁闭包变量 | `lightrag_service.py:237, 245, 252, 275` | 请求间互相覆盖降级结果；无 TTL/冷却；`_is_model_failover_error`（`:325-344`）靠字符串匹配判定 429 |
| M12 | 事件循环被 CPU/阻塞 IO 占用 | `routers/graph.py:77`（`nx.spring_layout` O(n²)）、`routers/documents.py:108,126`（`fitz` 同步解析） | 单次图布局可阻塞数百 ms～数秒，所有并发请求一起卡顿，放大 H1/H3 |
| M13 | `/version` 公开，暴露 commit、模型名与全部实验开关 | `api.py:833-865`（在 `:738` 健康白名单内） | 资产测绘与针对性攻击 |
| M14 | FastAPI 默认 `/docs`、`/openapi.json` 全量暴露 | `api.py:707` `FastAPI(lifespan=lifespan)` 未设 `docs_url=None` | 生产环境暴露完整 API schema |
| M15 | 外部依赖四套超时/重试语义 | MinerU 有退避（`mineru_client.py:383-402`）但批量轮询硬编码 `range(max_retries*50)`（`parse_service.py:442-472`）绕过 `mineru_task_timeout`；Qdrant/LLM 无超时；仅 Reranker 有超时降级（`reranker_runtime.py:64-66`） | 故障行为不可预测，排障困难 |

---

## 五、低危与技术债

| # | 问题 | 证据 |
| --- | --- | --- |
| L1 | `pytest -q` 在未装 `ragas` 时**直接中断**（3 个 collection error），而非跳过 | `tests/evaluation/test_conversation_e2e_{runner,semantic}.py`、`test_ragas_migration.py` 顶层 `from ragas import Dataset` |
| L2 | `requirements.txt` 与 `pyproject.toml` 漂移 | requirements 缺 `fastapi` / `uvicorn` / `alembic` / `httpx`，却含 dev 工具 `pytest` / `ruff` |
| L3 | `source-file` 未校验 `generation_id` 与文档一致性以外的一致性 | `routers/documents.py:276` 参数可选 |
| L4 | `is_safe_to_delete` 用子串判断 | `storage_layout.py:131-133` `if kb_id not in str(resolved)` |
| L5 | 废弃 API | `lifecycle_task_executor.py:87-88` `asyncio.get_event_loop().time()` |
| L6 | 空 `DASHSCOPE_API_KEY` 启动不报错，首次调用才炸 | `config.py:150`，`__post_init__` 未校验非空 |
| L7 | 三处各自 `load_dotenv`，先到先得 | `config.py:12`、`production_config.py:18`、`db/session.py:34` |
| L8 | 无 CORS 配置 | 全仓 `CORSMiddleware` 命中 0（当前依赖同源，但一旦需要跨域易被临时配成 `*`） |
| L9 | `# type: ignore` / `# noqa` 共 11 处 | `api.py`(2)、`query_application_service.py`(2)、`documents.py`(2) 等 |
| L10 | 未提交变更规模较大，发布状态不干净 | 40 文件 +848/−217，另有 `docs/demo/`、`docs/operations/`、`scripts/check_public_repo.py`、`migrations/versions/g1a1b2c3d4e5_*` 等未跟踪 |

---

## 六、架构评估

### 分层方向正确

`api.py` → `routers/*` → `services/*` → `repositories/*` → `db/models.py`，**未发现 service→router 的反向依赖**，也未发现循环 import。

### 但依赖关系被函数内 import 掩盖

`api.py` 有 **13 处函数体内 import**（`:269, 597, 611, 619-623, 632-639, 836-837, 936, 1030, 1067, 1093-1096, 1203-1205`），其中 `services/incremental_update_service.py` 同时 import 6 个 repository + 4 个 service，实际是"隐式编排中枢"。

### 三个上帝文件

| 文件 | 行数 | 承载的关注点 |
| --- | --- | --- |
| `services/incremental_update_service.py` | **2290**，单类 60+ 方法 | 上传校验、OCR/解析、分块、Qdrant 点复制、workspace 继承、租约/围栏、promote/rollback、GC 清扫、KB 元数据同步 |
| `lightrag_service.py` | **1536**，`query()` 单方法 `:839-1535`（约 700 行） | 归一化、稀疏融合、rerank、grounding、结构化引用、trace 装配、6 个实验开关分支 |
| `api.py` | **1283** | DTO、错误码表、证据投影、CORS/认证中间件、lifespan、6 类异常处理器、health/metrics、双查询端点 |

### 四组重复实现

1. **上传校验两份**：`services/document_service.py:23-73` 与 `services/incremental_update_service.py:73-75, 2235-2253`。路由只用后者（`routers/documents.py:146`），前者为死代码。
2. **检索指标三份**：`conversation/retrieval_evaluation.py:31`、`services/retrieval_evaluation.py:9-28`、`evaluation.py:237-249`，另有 `services/retrieval_ab_evaluation.py`（25 KB）。
3. **校验/门禁三份**：`validation_policy.py`(13.3 KB)、`validation_gate_service.py`(7 KB)、`generation_validation_service.py`(20.2 KB)，语义边界在代码中不可辨。
4. **配置来源四套**：`config.py:149` / `production_config.py:38` / `kb_runtime_settings.py:36` / 散落 `os.environ`（`api.py:1029,1153,677,364`、`db/session.py:31`、`storage_layout.py:27`、`routers/evaluation.py:59`）。运行时也有两套：`runtime.py:30`（legacy 线程桥）与 `services/runtime_manager.py:19`（KB 查询）。

---

## 七、做得好的地方（有证据）

| 项 | 证据 |
| --- | --- |
| Bearer 校验密码学正确 | `auth.py:52-59` `secrets.compare_digest(bytes, bytes)`；`:46-50` 严格前缀、拒绝首尾空白；`:34-36` actor 只输出 sha256 前 12 位 |
| admin/service 分离存在 | `auth.py:70-78` `require_admin_actor` 正确实现 403 |
| 无 SQL 注入 | repositories 全量 `select()/update()`；`execute(f"` / `text(f"` 命中 0 |
| 路径穿越防护扎实 | `storage_layout.py:17-23` `kb_id=^[a-f0-9]{8,64}$`、`:55-56` generation、`:81-86` job/doc id；文件名两处正则净化 |
| 无 Zip Slip | `parse_service.py:50-65` 仅内存 `ZipFile.read()`，无 `extractall` |
| 无硬编码密钥 | 敏感字段统一 `field(repr=False)`（`config.py:43-45,57,99`）；`check_public_repo.py` PASS |
| 无敏感日志 | `logger.*(api_key|token|secret|password)` 命中 0 |
| 错误模型统一 | `api.py:144-165` `PublicError{code,message,retryable}`，响应构造中无 `traceback` |
| 关闭路径严谨 | `runtime.py:106-139` 关闭后校验线程已退出，失败即抛错；`api.py:699-703` 顺序释放 runtime→manager→db |
| trace 会话隔离 | `db/session.py:70-79` 独立 factory，注释明确不复用请求会话 |

---

## 八、修复路线图

### 第 1 优先（1 周内，安全红线）

1. **H1** — 认证改 fail-closed：无密钥 ⇒ 拒绝一切非健康端点；`IRA_DEV_INSECURE=1` 才启用本地管理员。
2. **H3** — 公开路由改为显式白名单表；`ActorRole` 增加 `anonymous`；补 3 类边界测试。
3. **H2** — `source-file` 按 `page` 裁单页返回。
4. **H4** — 查询外层 `asyncio.timeout`，接入已有 `QA_REQUEST_TIMEOUT_SECONDS`。
5. **M8** — 密钥最小长度 32 校验。

### 第 2 优先（2～4 周，稳定性）

6. **H5** — 上传流式 + body-size 上限。
7. **M4 / M5 / M6** — 执行器先抢信号量后开会话；repository 去掉内部 commit；`/ready` 改 `async def`。
8. **M1** — 图谱改 `/v1/knowledge-bases/{kb_id}/graph` 并校验 generation。
9. **M12** — 图布局/PDF 解析移入 `asyncio.to_thread` 并加缓存。
10. **H1 后续** — 匿名查询限流（IP + 配额）。

### 第 3 优先（结构性，季度级）

11. 拆分 `incremental_update_service.py`（按 ingestion / lifecycle / gc 三个边界）。
12. 拆 `lightrag_service.query()`，把 6 个实验开关收敛为策略对象。
13. 统一 4 套配置来源与 2 套运行时，明确 legacy 边界。
14. 收敛 4 组重复实现。
15. **M7** — 把 README 引用的关键证据产物纳入版本控制（在 `.gitignore` 增加白名单），否则删除对应指标声明。

---

## 九、一句话结论

工程功底扎实、测试与文档密度高、认证与注入防护的实现细节经得起检查；风险不在"写错"，而在**默认值与边界定义**——漏设一个环境变量即完全放开（H1），公开面缺限流与超时（H4），引用核验接口名义上给一页、实际给整本（H2）。修完第 1 优先级的 5 项，这个项目即可从"高质量内部工具"提升到"可对外演示的安全基线"。
