# 安全修复报告 S0–S4

**分支**：`dev/retrieval-foundation-qa-downstream`
**日期**：2026-10-23
**范围**：审计报告中第 1 优先级的 5 项安全修复

---

## 一、最终验证结果

| 门禁 | 结果 |
|---|---|
| `pytest -q --ignore=tests/evaluation` | **1290 passed, 23 skipped** |
| 修复前基线 | 1247 passed（净增 43 项测试） |
| `ruff check .` | **All checks passed** |
| `scripts/check_public_repo.py` | **PASS**（1463 files） |
| 前端代码 | 未改动 |

---

## 二、S0 · 匿名路由边界（H3）

**问题**：`_is_public_browser_request` 用 `len(path.strip("/").split("/")) == 4` 推断公开路径，且测试覆盖为 0。

**改动** `src/industrial_rag/api.py`

- 引入 `_PUBLIC_EXACT_ROUTES`（精确路由白名单）、`_PUBLIC_DOCUMENT_SOURCE_SUFFIXES`、`_KB_ROUTE_PREFIX`、`_GRAPH_ROUTE_PREFIX`。
- 新增 `_is_public_kb_query_path`：结构化解析 `{kb_id}/query`，要求恰好两段且 kb_id 非空。
- `_is_public_browser_request` 改为常量表 + 结构化匹配，不再依赖段数。

**测试** `tests/test_public_route_contract.py`（新增）

- 8 条公开路径、19 条近似误判路径（`/v1/graph` 无尾斜杠、`GET /v1/feedback`、`POST .../query/extra`、`.../reprocess` 等）。
- 3 条中间件集成测试：管理端点 401、公开查询不被拦截、健康检查可达。

**发现并修复的真实缺陷**：`POST /v1/knowledge-bases//query`（空 kb_id）原实现会被判定为公开。新实现拒绝，已由测试锁定。

---

## 三、S1 · 原文按页裁剪（H2）

**问题**：`GET .../source-file` 的 `page` 参数只做范围校验、不参与裁剪，返回完整 PDF。产品定位是“受限原文摘录”，实现却给整本手册。

**改动** `src/industrial_rag/routers/documents.py`

- 新增 `_single_page_pdf(file_path, page)`：用 PyMuPDF 的 `insert_pdf(from_page, to_page)` 生成单页 PDF。
- 新增 `_attachment_disposition(filename)`：RFC 5987 `filename*=utf-8''...` 编码，正确处理中文手册名。
- `get_document_source_file` 改为 `asyncio.to_thread` 提取单页后返回 `Response`；非 PDF 回落 `FileResponse`。
- 提取失败（文件损坏/页数不一致）返回 404 `SOURCE_DOCUMENT_UNAVAILABLE`，不再回退整包。

**测试** `tests/test_document_source_api.py`

- 种子 PDF 由伪字节 `b"%PDF-1.4\n% test\n"` 改为 PyMuPDF 生成的真实多页 PDF。
- `test_document_source_file_returns_only_the_requested_page`：断言 `page_count == 1` 且文本为 `page 2`。

**附带收益**：`asyncio.to_thread` 同时消除了审计 M12 中“事件循环被 PDF 解析阻塞”的一处。

---

## 四、S2 · 查询硬超时（H4）

**问题**：KB 查询链路无任何超时。`QA_REQUEST_TIMEOUT_SECONDS` 只在 `production_config.py` 定义与校验，源码中无消费者；`QA_TIMEOUT` 错误码定义了但从未抛出。

**改动**

`src/industrial_rag/config.py`
- `Settings.qa_request_timeout_seconds: float = 180.0`
- `from_mapping` 读取 `QA_REQUEST_TIMEOUT_SECONDS`，非正数报错
- `__post_init__` 对直接构造做同样校验

`src/industrial_rag/errors.py`
- 新增 `AppErrorCode.qa_timeout = "QA_TIMEOUT"`，登记 `HTTP_STATUS_MAP → 504`

`src/industrial_rag/services/query_application_service.py`
- 新增 `_run_query_with_timeout(...)`：`asyncio.timeout` 包裹 `runtime.query`，并保留旧签名（仅 `mode`）的回退分支
- `_query` 中替换原有的裸 `runtime.query` 调用

**测试** `tests/test_query_timeout.py`（新增）

- 挂起 runtime 在 50 ms 内抛 `QA_TIMEOUT` / 504
- 检索参数（mode/top_k/chunk_top_k）正确透传
- 旧签名回退可用
- 配置默认 180s、可读 45s、拒绝 0

**阈值选择**：180s，与 legacy `/v1/query` 的硬编码值（`api.py:986`）和 `ProductionQASettings.request_timeout_seconds` 默认一致。README 记录 P95 为 5.861 s，余量约 30 倍。

---

## 五、S3 · 认证 fail-closed（H1）

**问题**：未配置 `SERVICE_API_KEY` / `ADMIN_API_KEY` 时，任意请求被赋予 `role="admin"`，且 `deployment_environment` 默认 `local_dev`。

**改动** `src/industrial_rag/api.py`

- 新增 `_insecure_local_dev_enabled(settings)`：**同时**要求 `deployment_environment == "local_dev"` 与 `IRA_DEV_INSECURE` 为真值。
- 新增 `_log_auth_mode(...)`：启动时打印 `INSECURE_LOCAL_DEV` / `FAIL_CLOSED` / `bearer` 三种模式。
- `_HEALTH_PATHS` 常量提取；健康检查判定**前移**到 fail-open 分支之前，保证探针在 fail-closed 下仍可用。
- 中间件：无密钥且未开启本地开关时返回 401，不再注入 admin 身份。
- `create_app` 构造期即计算该状态（使不走 lifespan 的测试客户端观察到相同默认值），lifespan 用最终 settings 重算。

**新增** `tests/conftest.py`：autouse fixture 设 `IRA_DEV_INSECURE=1` + `IRA_DEPLOYMENT_ENVIRONMENT=local_dev`，保持既有测试的本地开发语义。

**测试**（追加至 `tests/test_public_route_contract.py`）

- 无密钥无开关：管理端点 401、公开图谱 401、`/readyz` 200
- 本地开关开启：未认证访问恢复
- 开关在 `local_staging` 下被忽略：仍 401

**行为变更矩阵**

| 环境 | 已配置密钥 | 未配置密钥 |
|---|---|---|
| `local_dev` + `IRA_DEV_INSECURE=1` | 正常 Bearer 鉴权 | 全放行（admin，仅本地） |
| `local_dev` + 无开关 | 正常 Bearer 鉴权 | 仅健康检查可达，其余 401 |
| `staging` / `production` | 正常 Bearer 鉴权 | 启动即失败（`config.py` 强制） |

**文档**：`.env.example` 更新认证说明；README「Configure」段落补充 fail-closed 与本地开关。

---

## 六、S4 · 上传流式限量（H5）

**问题**：上传先 `await file.read()` 全量读入内存，20 MB 限制在校验之后才生效。

**改动** `src/industrial_rag/routers/documents.py`

- 新增 `_read_upload_limited(file, *, max_bytes=MAX_UPLOAD_SIZE)`：按 1 MB 分块读取，累计超限立即抛 413；若 `UploadFile.size` 已知且超限则**读取前**直接拒绝。
- 复用 `incremental_update_service.MAX_UPLOAD_SIZE`，不新增重复常量。
- `upload_document` 与 `replace_document` 均改用该函数。

服务层原有的 `len(content) > MAX_UPLOAD_SIZE` 校验保留，作为第二道防线。

**测试** `tests/test_upload_limit.py`（新增）
- 限额内返回原字节
- 超限抛 `file_too_large` / 413
- 已知声明大小超限时读取前即拒绝
- 空载荷可用

---

## 七、需要你注意的事项

1. **工作区仍未提交**。当前改动与审计前已有的 40 个未提交文件混在一起，`git diff` 无法区分。建议先提交或 stash 审计前的改动，再单独提交本次修复。

2. **`IRA_DEV_INSECURE` 未注入到启动脚本**。`scripts/start_workbench.ps1` 使用 `.env.local_staging`（已配置密钥），因此不受影响。但任何依赖“无密钥直接可用”的旧工作流现在会 401，需要显式设置该变量。

3. **`evaluation` 可选依赖**：3 个 `tests/evaluation/*` 文件在未安装 `ragas` 时会中断整个 `pytest -q`。CI 安装了 `[dev,evaluation]` 所以不受影响；本地建议 `pip install -e ".[dev,evaluation]"`。

4. **未做的事**（审计报告第 2/3 优先级，未在本轮范围）
   - 匿名查询限流与配额
   - `/v1/tasks` 补 actor 依赖
   - 图谱接口按 KB 隔离
   - 生命周期执行器“先抢信号量后开会话”
   - repository 内部 `commit()` 的事务边界
   - 三个上帝文件的结构性拆分

---

## 八、改动文件清单

**源码**
- `src/industrial_rag/api.py`
- `src/industrial_rag/config.py`
- `src/industrial_rag/errors.py`
- `src/industrial_rag/routers/documents.py`
- `src/industrial_rag/services/query_application_service.py`

**测试**
- `tests/conftest.py`（新增）
- `tests/test_public_route_contract.py`（新增，33 项）
- `tests/test_query_timeout.py`（新增，6 项）
- `tests/test_upload_limit.py`（新增，4 项）
- `tests/test_document_source_api.py`（增强）

**文档**
- `.env.example`
- `README.md`
