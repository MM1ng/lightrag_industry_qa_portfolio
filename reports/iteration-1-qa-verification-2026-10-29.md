# Iteration 1 独立验收报告（QA）

> **Round 2 结果见**：`reports/iteration-1-qa-verification-2026-10-29-round2.md`
> （T05 `3278c06e` 修复 D1/D2/D3/D4/D6 后的回归验收 → **VERDICT: GREEN / IS_PASS: YES**）
> 本文以下内容为 Round 1（提交 `28252f1b` 时点）的原始记录，结论 YELLOW / IS_PASS: NO，保留作对照。

- **验收人**：QA（严过关）
- **验收日期**：2026-10-29
- **待验目录**：`D:/基于Lightrag的工业手册问答系统/ira-iteration-1`（git worktree）
- **分支**：`dev/iteration-1`
- **验收方式**：只读。未修改源码/测试，未执行 commit / checkout / stash / reset；所有探针脚本放在仓库外（`D:/tmp/*.py`）
- **验收口径**：不复跑工程师的测试即交差，而是用**独立手法 + 对照组**反证每条修复是否成立

## 待验提交（实测 `git log --oneline -6`）

```
28252f1b feat(api): rate limit the anonymous browser surface                     (T04)
bee9fbf8 perf(api): move readiness probes, graph build and PDF page reads off the loop (T03)
dbd75274 fix(executor): bound connection/session lifetime and guarantee terminal status (T02)
4de9cf91 test: harden quality gate baseline (ragas skip, API key length, parser test portability) (T01)
ef207490 chore: baseline snapshot before iteration 1
ac24600b chore: prepare demo release candidate checkpoint
```

与原目录的隔离核验：`lightrag_industry_qa_portfolio-retrieval-foundation-upgrade` 仍是 `HEAD=ac24600b`、`47 modified + 21 未跟踪条目`（与上轮验收结束时完全一致），未被本轮工作污染。

---

## 一、门禁结果表（全部由我本人执行）

| # | 命令 | 实际结果 | 期望 | 结论 |
|---|---|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m pytest -q`（**不带 --ignore**） | `1330 passed, 28 skipped, 1 warning in 131.26s`，退出码 **0**，无 error / Interrupted | 1330 passed / 28 skipped | **一致（精确吻合）** |
| 2 | `./.venv/Scripts/python.exe -m ruff check .` | `All checks passed!`，退出码 0 | 全通过 | **一致** |
| 3 | `./.venv/Scripts/python.exe scripts/check_public_repo.py` | `PUBLIC_REPO_CHECK=PASS files=1468`，退出码 0 | PASS | **一致**（文件数比迭代前 1463 多 5，对应本轮新增源码/测试文件） |
| 4 | `cd frontend && npm run test -- --run` | `Test Files 6 passed (6)` / `Tests 25 passed (25)` | 25 passed | **一致**（`/v1/graph/*` 契约变更未打破前端） |
| 5 | `git status --porcelain` / `git log --oneline -6` | 工作区**空**；log 与待验清单逐字一致 | 干净 / 一致 | **一致** |

`junitxml` 复核（1358 collected = 1330 passed + 28 skipped，0 failures / 0 errors）：

| 跳分类别 | 数量 | 说明 |
|---|---|---|
| `tests/evaluation/*` 模块级 collection skip | 3 | T01 的 `pytest.importorskip('ragas')` 生效 —— 未装 `[evaluation]` extra 时不再 Interrupted，而是干净跳过 |
| 其余 in-tree skip | 25 | 含 `tests.test_phase14a_parser_ab` 的 2 条（reason：`protected source manuals are not distributed`），即上轮验收的 2 个失败用例已按 T01 转为条件跳过；其余为既有的 qdrant/mineru 资产缺失跳过 |

---

## 二、逐项独立反证（含我自己跑出的数字）

### T02 · 连接/会话边界

**① 队列中的 runner 是否真的不再占连接**（独立脚本 `D:/tmp/t02_probe.py`，`max_concurrency=2` + 6 个 pending 任务、各自独立 KB、handler 阻塞在 `Event` 上，稳态后连续采样 20 次 `engine.pool.checkedout()`）：

| 被测实现 | 稳态 `checkedout` 采样 | 峰值 | 说明 |
|---|---|---|---|
| 新实现（`dbd75274`） | `2,2,2,...,2`（20 次全为 2） | **2** | 恰等于 `max_concurrency`，等待中的 4 个 runner 不持连接 |
| 旧实现（`dbd75274~1` 抽出到临时目录，用 `importlib` 加载，**不走 PYTHONPATH 覆盖**） | `6,6,6,...,6`（20 次全为 6） | **6** | 每个排队 runner 各占 1 条连接，与修复报告的复现描述一致 |

→ **该数字确实由修复改变**（同一脚本、同一数据规模、同一 handler，唯一变量是被测模块）。
补充诚实说明：若只在 `_active_tasks==6` 成立的**瞬间**取一次读数，新实现偶尔读到 4（4 个 runner 正处在第一阶段 identity 读的短会话里）。这是测量时机造成的瞬时值，不是稳态；采样 20 次后稳定为 2。仓库自带测试用「信号量等待者数 == 4」来判定稳态，方向正确。

**② runner 抛异常后 `stop()` 是否真的清空 `_task_handles`**（`D:/tmp/t02_stop_probe.py`，注入必抛的 `_load_knowledge_base_id`）：

| 指标 | 新实现 | 旧实现 |
|---|---|---|
| 是否存在 handle 登记表 | `True` | `False`（`create_task` 句柄直接丢弃） |
| runner 全部异常退出后 `len(_task_handles)` | **0** | 无此结构 |
| `stop()` 之后 `len(_task_handles)` | **0** | 无此结构 |
| 逃逸异常被取回并记录 | **4 条 `Task runner crashed`** | **0 条**（异常被静默吞掉，永远拿不到） |

→ 结论：句柄登记、取消、异常取回三项均真实生效；旧实现确实「看不见」。

**③ 额外发现（未被修复覆盖的路径）**：`_execute_task` 的第一阶段 `_load_knowledge_base_id()` 位于 `_run_task_in_session` 的 `try/except` **之外**。我用必抛的 identity 读模拟一次瞬时 DB 故障，poll 已把任务置为 `running` 后 runner 崩溃：

```
{"statuses_after_crash": {"0cf4d1e3": "running", "e78972d8": "running"}}
```

两个任务都**永久停在 `running`**，只能等下次进程启动由 `_recover_stale_tasks()`（对所有 `running` 无条件 `mark_retrying`）救回。这与 T02 提交信息里「guarantee terminal status」的表述不完全相符（详见缺陷 D3）。旧实现同样有此问题，故**不是回归**，但说明该保证只覆盖了 handler 会话这一段。

### T03 · 事件循环卸载

**① `/ready` 是不是真 async**（`inspect`，非字符串匹配）：`is_coroutine_function=True`、`is_asyncgen=False`、源码无 `asyncio.run`、签名 `(request: Request) -> dict[str, object] | JSONResponse`。

**② 挂死的 DB 探针下 `/ready` 墙钟**（`_db_reachable` patch 成 `asyncio.sleep(10)`，`TestClient` 实测）：

| 端点 | 墙钟 | 状态码 |
|---|---|---|
| `/ready` | **2.016 s** | 503（body `status=not_ready`，`components.db=unavailable`） |
| 同期并发 `/version` | **0.047 s** | 200 |

→ 探针被 `_cached_probe` 的 2 s 超时兜住，且不阻塞其他请求。旧实现每次请求两次 `asyncio.run`，这个性质无从谈起。

**③ 高频探测下 `AsyncQdrantClient` 是否只建一次**（patch 构造计数器，12 次 `/ready`，`vector_backend=qdrant`）：构造次数 **1**，12 次全部 200，`_READY_CACHE_TTL_SECONDS=5.0` → TTL 缓存生效。

**④ 图谱并发不阻塞事件循环**（40 节点 graphml，`load_graph_cached` 人为加 0.4 s 同步开销，两个请求同时发出、各自独立计时）：

| 场景 | `/v1/graph/overview` 耗时 | 并发 `/version` 耗时 |
|---|---|---|
| 现实现（`asyncio.to_thread`） | 0.468 s | **0.047 s** |
| 对照组：把 `asyncio.to_thread` 换成直接调用（模拟修复前的内联写法） | 0.438 s | **0.438 s** ← 被拖到与图谱请求同长 |

→ 这不是"看代码里有没有 `to_thread`"，而是用对照组证明该数字确实由 `to_thread` 造成。

**⑤ 缓存不会返回过期数据**（对 `nx.read_graphml` 计数，而非对包装函数计数）：

| 请求 | 实际解析次数 |
|---|---|
| 第 1 次 `/v1/graph/overview` | 1 |
| 第 2 次（文件未变） | 1 → **命中缓存，未重复解析** |
| 重写 graphml（内容 + mtime 变化）后第 3 次 | 再次解析，新节点 `ZZZ-fresh-node` 可见（node_count 41），`_graph_cache` 3 条 |

（注：计数 3 中含我自己重写文件时的一次 `nx.read_graphml`；净新增解析为 1 次，位置正确。）

**⑥ `/ready` 语义变更的消费方检索**（`grep -rn "/ready" scripts/ docs/ .github/ README.md frontend/src`）：

| 消费方 | 影响 |
|---|---|
| `scripts/check_local.ps1:9-10` | `Invoke-RestMethod` 在 503 时抛异常 → 走 `catch` 打印 `api /ready: unreachable`，**丢失 components 明细且措辞误导**（服务可达但未就绪）。不阻断脚本、无退出码依赖 |
| `.github/` 工作流 | 无任何 `/ready` 引用 |
| `frontend/src` | 无引用 |
| `docs/phase-6-production-readiness-report.md:133` | 早已把「失败返回 503 与脱敏组件」写成目标契约 → 本次改动其实是**代码向文档对齐** |

→ 旧实现实测：DB/Qdrant 不健康时返回 **200 + body `status=not_ready`**，只有「runtime 未初始化」那一条返回 503。所以「200-always → 503」的说法需要精确化为「**组件不健康时的状态码** 200 → 503」。

**⑦ PDF 单页读取仍在线程池**：`routers/documents.py:368 await asyncio.to_thread(_single_page_pdf, ...)`，另有 `:131 _pdf_page_count`、`:146 _pdf_page_text` 同样离线。

### T04 · 匿名限流

| 验收点 | 我的实测 | 结论 |
|---|---|---|
| ① 第 31 次是否 429 | 前 30 次全部非 429，**第 31 次 429**，`code=RATE_LIMITED`，`Retry-After: 60`（`isdigit()` 为真且 ≥1） | 通过 |
| ② `IRA_PUBLIC_RATE_LIMIT_PER_MINUTE=0` | 默认桶 `limit_for('default')=0`，40 次请求只有 200，无 429 | 通过（**注**：只关默认桶；feedback 桶仍为 5，实测第 6 次 feedback 仍 429 → 「完全关闭」需同时把 `IRA_FEEDBACK_RATE_LIMIT_PER_MINUTE=0`） |
| ③ 伪造 `X-Forwarded-For` 能否绕过（`IRA_TRUST_PROXY_HEADERS` 未开） | 打满 30 次后带 `X-Forwarded-For: 203.0.113.7` → **429**；换成 `198.51.100.9, 10.0.0.1` → **429**（不新增预算）。对照组：`trust_proxy_headers=True` 时同一伪造头 → **200**（新预算） | **不可绕过**，安全属性成立；且开关语义正确 |
| ④ feedback 桶与查询桶隔离 | feedback 前 5 次 422、第 6 次 **429**；随后 `POST /v1/knowledge-bases/kb-1/query` **未被限流**（503 = 无 runtime，不是 429），`GET /v1/knowledge-bases` 仍 200 | 通过（桶独立） |
| ⑤ `/metrics` 的 `rate_limited_total` 增长 | 33 次请求后 `counters.rate_limited_total=3`（第 31/32/33 次） | 通过 |

**额外发现（T04 侧）**：

- 若 ASGI server 不报告对端地址（scope 无 `client`），`_anonymous_client_key` 回落成 `"unknown"`，所有客户端塌缩进同一个桶：实测 31 次后 `tracked_keys=[('default','unknown')]` 且第 31 次 429（缺陷 D5）。
- `/metrics` 需 Bearer 才能读（合理），但 `http_status_429_total` 从未增长 —— 指标中间件在鉴权中间件外层，拿不到被限流提前返回的响应，只有自定义 `rate_limited_total` 可见（缺陷 D8）。

### T01 · 密钥长度策略与门禁基线

| 场景 | 实测 |
|---|---|
| `local_staging` + `SERVICE_API_KEY` 31 字符（admin 32） | `ValueError: SERVICE_API_KEY 长度必须不少于 32 个字符` |
| `local_staging` + 32 字符 | OK |
| `local_staging` + 33 字符 | OK（边界精确落在 32） |
| `staging` + `ADMIN_API_KEY` 31 | `ValueError: ADMIN_API_KEY ...` |
| `production` + 10 字符 | `ValueError` |
| `local_dev` + 6 字符 | 不阻断，仅告警两条（service / admin 各一条，文案含 `allowed only when IRA_DEPLOYMENT_ENVIRONMENT=local_dev`） |
| `Settings.from_env()`（真实启动路径，`local_staging` + 31 字符） | `ValueError`（不是只有直接构造才校验） |

`.env.local_staging` 未被改动：mtime 仍为 `2026-10-12 11:26:33`，且该文件本就不在 git 索引中（`git ls-files --error-unmatch` 报未跟踪）。**但它也没有被迁移** → 见缺陷 D1。

---

## 三、三处语义变更的风险评估

### 变更 1：`/ready` 由「200 + body 表达」改为「不 ready 返回 503」

- **精确事实**：旧实现仅在「runtime 未初始化」返回 503，组件不健康时返回 **200 + `status=not_ready`**；新实现组件不健康也返回 503（实测 503 + `components`）。
- **回归风险**：低。唯一硬依赖方 `scripts/check_local.ps1` 用 try/catch 包裹，503 只会让该行打印 `unreachable`（措辞失真但不中断），无退出码依赖；`.github/`、前端、其它脚本均无引用。
- **与文档冲突？** 不冲突，反而**更贴合** `docs/phase-6-production-readiness-report.md:133` 已写明的「失败返回 503」契约，也符合 k8s/LB 的 readiness 语义。
- **建议**：**保留**。配套修 `check_local.ps1`（让它在 503 时仍打印 components，见 D6）。

### 变更 2：带 Bearer 的请求打到公开路径仍进匿名限流桶

- **实测确认**：带 `Authorization: Bearer <有效 service key>` 连续请求 `GET /v1/knowledge-bases`，第 31 次 **429**；而同一 Bearer 打死 admin-only 路由（`/metrics`）35 次全部 200，**不受限**。原因就是 `_is_public_browser_request` 判定在 `authenticate_bearer` 之前。
- **回归风险**：**中**（仅在配置了密钥的部署生效）。受影响的是管理端/演示端的读放大：
  - `KnowledgeBasesView` 每次刷新 = `listKnowledgeBases` + 每个 KB 一次 `getKnowledgeBase`（N+1），5 个 KB 即 6 次；
  - `source-file`（每次点引用翻页 1 次）、`/query`（每次提问 1 次）、`/v1/graph/overview|neighborhood`（每次切图谱 1 次）**共用同一个 30/分钟 桶**；
  - 结论：一个管理员在 1 分钟内刷 4~5 次知识库页 + 问两三个问题就可能撞 429。前端 `client.ts:160` 把 429 标为 `retryable`，会弹"服务暂时不可用，请稍后重试"，**不会白屏**，但体验降级。
  - `local_dev`（无密钥 + `IRA_DEV_INSECURE=1`）走 fail-open 分支，**完全不受限**，本地演示不受影响。
- **与文档冲突？** **是**：`src/industrial_rag/rate_limit.py:5` 写「Bearer-authenticated traffic is never rate limited here」，与实测行为矛盾（见 D4）。
- **建议**：**保留当前行为（安全优先）**——公开路径上无法在不校验 token 的前提下区分真管理员与伪造头，把 Bearer 头当免死金牌反而会成为绕过手段。但必须：①修正 docstring；②在 `docs/operations/security-audit.md` 明写「公开路径按 IP 限流，含已登录管理端，30/min」；③后续迭代可考虑「公开路径上若 Bearer 校验通过则不计桶」，注意实现顺序（校验失败也不得放行）。

### 变更 3：图谱布局缓存 key 比规格多 `kind` / `center` 两个维度

- **实测反证**（`D:/tmp/t03_layout_key_probe.py`，两个不同中心的邻域请求）：

| key 形态 | 请求 1（中心=泵0） | 请求 2（中心=阀0） |
|---|---|---|
| 现实现（含 kind/center） | 200，3 节点 | 200，3 节点，节点集与请求 1 **完全不相交** |
| 对照：去掉 center（只剩 fingerprint/limit/hops/seed） | 200 | **`KeyError: 'b9'` → 500**（`_serialize_graph` 用上一份坐标去索引新子图节点） |

→ 额外维度是**正确性要求**，不是过度设计：缺了它，第二次邻域请求会拿第一次的坐标字典，直接 500。
- **回归风险**：无正确性风险；只有容量风险 —— `_LAYOUT_CACHE_MAX_ENTRIES=32`，若大量不同中心轮询会抖动重算 `spring_layout`（CPU），但结果始终正确，且容量有界。
- **与文档/前端冲突？** 无。前端只消费坐标字段，不感知缓存 key。
- **建议**：**保留**。可选优化：把 `center` 归一化（如按 node id 稳定排序）后再入 key，避免同义请求重复计算。

---

## 四、缺陷清单

### P1

**D1 · `.env.local_staging` 未迁移，本地 staging 栈当前无法启动**
- 事实：`SERVICE_API_KEY` 长度 **26**、`ADMIN_API_KEY` 长度 **6**（均 < 32），`IRA_DEPLOYMENT_ENVIRONMENT=local_staging`，文件 mtime 仍为 `2026-10-12`（本轮未改）。
- 复现：把该文件逐行注入环境后调 `Settings.from_env()` → `ValueError: SERVICE_API_KEY 长度必须不少于 32 个字符（当前长度 26，IRA_DEPLOYMENT_ENVIRONMENT=local_staging）`。
- 影响：`scripts/start_workbench.ps1:29` 加载的就是这个文件 → API 起不来。文档写了迁移步骤，但**迁移没被执行**；而该文件不入库，任何门禁都发现不了。
- 修复：`python -c "import secrets;print(secrets.token_urlsafe(32))"` 生成两个新 key（≥32 且互不相同）写入 `.env.local_staging`，重启验证 `/ready`=200。

### P2

**D2 · `start_workbench.ps1` 未先跑 `check_env.ps1`，密钥长度失败发生在 Qdrant 启动之后且无行动指引**
- 事实：脚本第 [1/5] 步只 import env 文件，第 [2/5] 步就拉起 Qdrant，第 [3/5] 步才起 API；`check_env.ps1` 已能输出 `invalid: SERVICE_API_KEY must be at least 32 characters` 并 `exit 1`，但没有任何调用点。
- 影响：D1 这类失败要等到基础设施起来后才以 uvicorn 报错的形式暴露，排障成本高。
- 修复：`Import-IraEnvFile` 之后立即 `& check_env.ps1`，非 0 则中止。

**D3 · T02 的「terminal status」保证存在缺口：runner 在进入会话前崩溃会让任务永久停在 `running`**
- 事实：`_execute_task` 的 `try/finally` 只回收 `_active_tasks`，`_load_knowledge_base_id()` / 后续步骤抛异常时不写终态；`_run_task_in_session` 的兜底 `_ensure_terminal_status` 覆盖不到这一段。
- 复现：注入必抛的 `_load_knowledge_base_id`，poll 一次后 `statuses_after_crash = {task1: "running", task2: "running"}`。
- 影响：任务卡在 running 直到下次进程启动（`_recover_stale_tasks` 无条件把 running 改 retrying），期间 API/UI 显示运行中、不重试。
- 修复：`_execute_task` 外层再包一层 `except Exception: await self._ensure_terminal_status(task_id)`。

**D4 · `rate_limit.py` 文档与行为矛盾**
- `src/industrial_rag/rate_limit.py:5` 写 "Bearer-authenticated traffic is never rate limited here."，但公开路径上的 Bearer 请求实测会被限流并 429。
- 修复：改成"Bearer traffic on non-public paths is never limited; public paths are limited by client identity regardless of the Authorization header"。

**D5 · 无对端地址时所有客户端共用一个桶**
- `_anonymous_client_key` 在对端未知时返回 `"unknown"`；实测 scope 无 `client` 时 `tracked_keys=[('default','unknown')]`，第 31 次 429 —— 等于给全站加了 30/min 的总闸。
- 影响：仅在 ASGI server 不报告 peer（如某些 unix socket / 反代配置）时触发；uvicorn 默认无此问题。
- 修复：无 peer 时记录一次 WARN 并建议开启 `IRA_TRUST_PROXY_HEADERS`；或在文档中明确该前置条件。

**D6 · `check_local.ps1` 把 503 误报为 `unreachable`**
- `scripts/check_local.ps1:9-11`：`Invoke-RestMethod` 遇 503 抛异常 → 打印 `api /ready: unreachable`，丢失 `components` 明细。
- 修复：catch 中读 `$_.ErrorDetails.Message`，或改用 `-SkipHttpErrorCheck`（PS7）后统一解析 body。

**D7 · `/healthz` 缺少 `/ready` 那样的外层超时护栏**
- `healthz` 直接 `await _db_reachable()`；`_cached_probe` 的 2 s 兜底只用在 `/ready`。把探针换成不带内部超时的挂起实现后实测 `/healthz` = **10.015 s**（同时 `/version` 仍 0.032 s，事件循环未被阻塞，只是该响应慢）。
- 结论：当前发布的 `_db_reachable` 自带 `asyncio.timeout(2.0)`，线上不可达；但这是"靠被调方自觉"的脆弱不变式。
- 修复：`healthz` 也走 `_cached_probe`，或统一在外层 `asyncio.timeout`。

**D8 · `http_status_429_total` 永远为 0**
- 限流响应在鉴权中间件内层提前返回，指标中间件取不到，实测 `counters` 中只有 `rate_limited_total=3`、无 `http_status_429_total`。
- 影响：按 `http_status_*` 做告警/看板的运维侧看不到限流（自定义计数器可见）。
- 修复：在 `_rate_limited_response` 里同时 `increment("http_status_429_total")`，或调整中间件顺序。

### P3

- **D9** · `tests/test_phase14a_parser_ab.py` 的 2 条 identity 断言在 CI/新克隆中**永不执行**：跳过条件读的是未入库的 `source_manifest.json` 里的**机器绝对路径**（`D:\...\lightrag_industry_qa_portfolio\data\manuals\...`）。跳过是安全的（不会误跑），但"parser AB 制品身份"在 CI 上等于无覆盖，建议在文档里显式登记为已知盲点。
- **D10** · 布局缓存 32 条，极端多中心场景会反复重算 `spring_layout`（仅性能）。

---

## 五、结论

**VERDICT: YELLOW**

判定依据：

1. **门禁部分完全达标**：5 条命令逐条与工程师自测数字精确吻合（1330/28、退出码 0、ruff 全通过、1468 文件 PASS、前端 25/25、工作区干净、提交序列一致），且 4 个提交都在 `dev/iteration-1` 上（不是未提交的工作区改动）。
2. **四项修复经对照组反证为真**，不是"测试写过就算"：连接池 2 vs 旧 6；句柄清理 0 且 4 条异常被取回 vs 旧 0 且静默；`/ready` 2.0 s 有界 + 并发 0.047 s；图谱内联对照把并发请求从 0.047 s 拖到 0.438 s；缓存命中/失效与解析次数正确；限流 429/Retry-After/伪造头不可绕过/桶隔离/指标增长全部实测通过；密钥长度边界精确落在 32。
3. **不到 GREEN 的原因（都是可小改修的硬事实，不是风格问题）**：
   - **D1**：本地 staging 环境文件未迁移 → 这台机器上 `start_workbench.ps1` 起不来（复现了 ValueError）；文档有步骤但未执行，且该文件不入库、门禁看不见。
   - **D3**：T02 自称的"永不留在 running"存在可复现缺口（identity 读阶段崩溃 → 任务卡 running 至下次重启）。
   - **D4/D5/D6/D7/D8**：限流与就绪探针周边的文档/诊断/可观测性偏差，会在运维与演示时误导人。
4. **不是 RED**：没有任何数据完整性问题、鉴权绕过、数据泄漏；三处语义变更中两处（`/ready` 503、布局 key）经论证应保留，一处（Bearer 进匿名桶）安全方向正确但需补文档与后续优化。

**转 GREEN 的最小动作**：修 D1（重生成两个 key）+ D3（`_execute_task` 外层兜底）+ D4/D6（文档与脚本措辞），再复跑 `pytest -q` 与 `start_workbench.ps1` 冒烟即可。

---

IS_PASS: NO
