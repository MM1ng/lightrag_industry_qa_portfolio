# Iteration 1 验收执行报告

**执行时间**：2026-10-29 11:02–11:10
**执行对象**：`ira-iteration-1`（分支 `dev/iteration-1`，HEAD `3278c06e`）
**执行依据**：`reports/iteration-1-acceptance-checklist-2026-10-29.md`
**结论**：**22/23 项通过；1 项不通过（限流伪造头防护），判定 CONDITIONAL PASS**

---

## 一、分层结果

### 第 0 层 · 环境（4/4 通过）

| 检查 | 实测 | 判定 |
|---|---|---|
| 隔离环境工作区干净 | `git status --porcelain` → 0 行 | PASS |
| 提交序列 | 6 条，首行 `3278c06e` | PASS |
| 原目录 HEAD | `ac24600b` | PASS |
| 原目录仍保持未提交状态 | 68 行（47 改 + 21 未跟踪） | PASS |

### 第 1 层 · 门禁（4/4 通过）

| 命令 | 实测 | 判定 |
|---|---|---|
| `pytest -q`（不带 `--ignore`） | `1331 passed, 28 skipped, 1 warning in 130.75s`，退出码 0 | PASS |
| `ruff check .` | `All checks passed!` | PASS |
| `scripts/check_public_repo.py` | `PUBLIC_REPO_CHECK=PASS files=1468` | PASS |
| `npm run test -- --run` | `Tests 25 passed (25)` | PASS |

### 第 2 层 · 逐项证据（6/6 通过）

| 命令 | 实测 | 判定 |
|---|---|---|
| `test_config.py -k "short or length"` | 4 passed | PASS |
| `test_task_executor_concurrency.py` | 5 passed | PASS |
| `test_ready_probe.py` + `test_graph_threading.py` + `test_anonymous_rate_limit.py` | 14 passed | PASS |
| `test_document_source_api.py` + `test_public_route_contract.py` + `test_query_timeout.py` + `test_upload_limit.py` | 52 passed | PASS |

### 第 3 层 · 运维冒烟（8/9 通过）

| 项 | 实测 | 判定 |
|---|---|---|
| 3.1 `check_env.ps1 -EnvFile .env.local_staging` | 13 项 configured + `RESULT=OK` + exit 0 | PASS |
| 3.1 密钥长度 | service 43 / admin 43 / 互不相同 | PASS |
| 3.2 短密钥时启动门禁 | 在 `[2/6]` 抛错，**输出中无 `[3/6] Starting Qdrant`** | PASS |
| 3.2 环境文件还原 | sha256 与备份完全一致（`e78ae6ad…274ca`），工作区仍 0 行 | PASS |
| 3.3 `/ready` | `HTTP 200`，`{"status":"ready","components":{"config":"ok","db":"ok","qdrant":"n/a"}}`，耗时 **0.005s** | PASS |
| 3.4 匿名查询限流 | 第 31 次 `429`，`retry-after: 52`，body `code=RATE_LIMITED` | PASS |
| 3.4 feedback 独立桶 | 默认桶已满时仍放行 5 次，第 6 次 `429`；同期 query 仍 429 | PASS |
| 3.5 伪造 `X-Forwarded-For` | **不同伪造 IP 的请求全部放行（未限流）** | **FAIL** |

> 冒烟使用独立 sqlite 库（`alembic upgrade head` 后启动），未接触演示库，未启动 Qdrant（`qdrant: n/a`），验收后临时产物已删除。

---

## 二、不通过项：3.5 伪造 `X-Forwarded-For` 可绕过限流

### 现象

默认桶打满（第 31 次 429）后，带不同 `X-Forwarded-For` 的请求仍被放行：

```
无 XFF                     → 429
X-Forwarded-For: 10.1.2.3  → 422（已进入路由，未限流）
X-Forwarded-For: 127.0.0.1 → 429
X-Forwarded-For: 10.9.9.9  → 422（第二次仍 422，同一伪造 IP 也只占一个桶）
```

### 根因（已定位，非应用代码缺陷）

应用层 `_anonymous_client_key()` 逻辑正确：`IRA_TRUST_PROXY_HEADERS` 为 false 时不读 `X-Forwarded-For`。问题在 **ASGI 服务器层**：

- uvicorn 0.52.4 默认 `proxy_headers=True`、`trusted_hosts="127.0.0.1"`
- 直连对端在 trusted 列表内时，`ProxyHeadersMiddleware` 会用 `X-Forwarded-For` **改写 `request.client.host`**
- 限流器读的正是 `request.client.host`，于是伪造头直接换来一个新桶

对照实验（同一构建，仅改启动参数）：

| 启动方式 | 31 次各带不同 XFF | 结论 |
|---|---|---|
| `uvicorn …`（默认，即 `scripts/start_api.ps1` 的写法） | 全部放行（422） | 限流被绕过 |
| `uvicorn … --no-proxy-headers` | 前 30 次 422，**第 31 次 429** | 防护成立 |

`scripts/` 全目录 grep `proxy-headers` / `forwarded-allow-ips` → **0 命中**，即演示与 staging 栈都跑在默认值上。

### 影响面评估

| 拓扑 | 是否可利用 |
|---|---|
| 本机演示（浏览器 / 本地脚本访问 127.0.0.1） | **可**。本地任意脚本伪造头即可获得无限配额（浏览器 fetch 无法设置该头，故普通页面用户不能） |
| nginx 与 API 同机、`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for` | 否。uvicorn 0.52 从右往左取第一个非受信地址，取到的是 nginx 追加的真实 IP |
| nginx 覆写 XFF（`$remote_addr`） | 否 |
| 直连 uvicorn 且对端 IP 在 trusted 列表内 | 可 |

### 建议修复（下一轮，属 T04 的范围补齐）

1. `scripts/start_api.ps1` 的 uvicorn 启动参数显式加 `--no-proxy-headers`（本地/单机拓扑）；或在反代拓扑下显式 `--forwarded-allow-ips=<反代 IP>`，并配套文档要求反代覆写 XFF。
2. 把这条写进 `docs/operations/security-audit.md`：应用层 `IRA_TRUST_PROXY_HEADERS` 只控制"应用是否再解析一次"，**不构成对服务器层代理头改写的防护**。
3. 补一条真实服务器级回归（TestClient 会绕过 uvicorn 中间件，现有 `test_anonymous_rate_limit.py` 因此测不到该问题）。

---

## 三、附带确认的次要项

- **D11 复现**：密钥过短时 `check_env.ps1` 会连印两条 `invalid:`，`RESULT=FAIL` 名单同时含 `SERVICE_API_KEY_TOO_SHORT` 与 `SERVICE_API_KEY`。可读性可接受，但属冗余（P3，已登记）。
- **`/metrics` 匿名 401**：符合预期（非公开路径），限流计数需带 Bearer 查询；本轮未复核计数，沿用 QA Round 1 的 `rate_limited_total=3`。
- **`qdrant: n/a`**：本次冒烟未启动 Qdrant，探针按设计返回 `n/a` 而非 fail，`/ready` 仍 200；这与 Round 1 「Qdrant 不可用 → 503」的用例互补，说明两分支都覆盖到了。

---

## 四、判定

| 维度 | 结果 |
|---|---|
| 代码与测试 | 通过（1331 passed / ruff / public repo / 前端） |
| 隔离与"原版不动" | 通过 |
| 配置迁移（D1） | 通过（43/43，服务可启动） |
| 启动门禁（D2） | 通过 |
| 限流生效与桶隔离 | 通过 |
| 限流抗伪造 | **未通过（需部署层参数）** |

**CONDITIONAL PASS**：功能与门禁全部达标，唯一未通过项不涉及应用代码逻辑，而是启动参数缺失；修复成本约一行参数 + 一条文档。

---

## 五、验收后的环境状态

- `ira-iteration-1`：工作区 0 行改动，HEAD `3278c06e`，无残留服务进程（8123 / 8124 均已停止）
- 临时产物：`.run/acceptance-20261029.db` 与两份日志已删除
- `.env.local_staging`：已按 sha256 还原，与验收前逐字节一致
- 原目录 `...-retrieval-foundation-upgrade`：HEAD `ac24600b`，68 行未提交状态未变
