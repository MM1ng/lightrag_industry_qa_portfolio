# Iteration 1 · Round 2 回归验收报告（QA）

- **验收人**：QA（严过关）
- **验收日期**：2026-10-29
- **待验目录**：`D:/基于Lightrag的工业手册问答系统/ira-iteration-1`（worktree，分支 `dev/iteration-1`）
- **待验提交**：`3278c06e fix: close the executor identity-read gap, rotate staging keys, tighten diagnostics`（T05）
- **验收范围**：只验 T05 对本轮 5 项缺陷（D1/D2/D3/D4/D6）的修复 + 确认无回归；上一轮的全量反证不重做，仅 D3 按要求独立复现
- **方式**：只读（探针脚本均在仓库外 `D:/tmp/`）。唯一一次写操作是**经批准的** `.env.local_staging` 临时改写，已按哈希校验还原（见 §3.2）
- **Round 1 报告**：`reports/iteration-1-qa-verification-2026-10-29.md`（结论 YELLOW / IS_PASS: NO）

`git log --oneline -7`：

```
3278c06e fix: close the executor identity-read gap, rotate staging keys, tighten diagnostics  ← 本轮
28252f1b feat(api): rate limit the anonymous browser surface
bee9fbf8 perf(api): move readiness probes, graph build and PDF page reads off the loop
dbd75274 fix(executor): bound connection/session lifetime and guarantee terminal status
4de9cf91 test: harden quality gate baseline (ragas skip, API key length, parser test portability)
ef207490 chore: baseline snapshot before iteration 1
ac24600b chore: prepare demo release candidate checkpoint
```

---

## 1. 门禁复跑（Round 2，全部由我本人执行）

| # | 命令 | 实际结果 | 期望 | 结论 |
|---|---|---|---|---|
| 1 | `pytest -q`（不带 `--ignore`） | **1331 passed, 28 skipped**, 退出码 **0**，132.76s（junit：1359 collected / 0 failures / 0 errors / 28 skipped） | 1331 / 28 | **一致** |
| 2 | `ruff check .` | `All checks passed!`（exit 0） | 通过 | **一致** |
| 3 | `scripts/check_public_repo.py` | `PUBLIC_REPO_CHECK=PASS files=1468`（exit 0） | PASS | **一致**（与 Round 1 的 1468 相同，T05 只改脚本/源码，未增文件） |
| 4 | `cd frontend && npm run test -- --run` | `Test Files 6 passed (6)` / `Tests 25 passed (25)`（exit 0） | 25 | **一致** |
| 5 | `git status --porcelain` / `git log --oneline -7` | 工作区**空**；7 条提交与清单逐字一致 | 干净 / 一致 | **一致** |

用例数变化：1330 → **1331**（+1 = T05 新增 `test_identity_read_failure_does_not_leave_task_running`，junit 中确认该用例存在于且通过）。skip 仍为 28，未新增跳过。

T05 改动面（`git show --stat`）：`check_env.ps1(+5)`、`check_local.ps1(+41)`、`start_workbench.ps1(+21)`、`rate_limit.py(+9)`、`lifecycle_task_executor.py(+8)`、`tests/test_task_executor_concurrency.py(+31)` —— **6 个文件，无 api.py / 无图谱 / 无限流中间件**，与"只修诊断与执行器兜底"的范围相符，回归面可控。

---

## 2. D3 · 执行器终态兜底（独立复现，不使用工程师的新测试）

探针：`D:/tmp/r2_d3_probe.py`（自建 SQLite + `AsyncAdaptedQueuePool`，任务状态一律用**新会话**读取，避免 identity map 缓存）

**① identity 读失败后是否还卡 `running`**

| 步骤 | 结果 |
|---|---|
| `_load_knowledge_base_id` 注入 `raise RuntimeError("identity read exploded")` | — |
| `_poll_once()` → 等 `_active_tasks` 清空 → 新会话读状态 | **`retrying`**（`is_not_running = true`） |
| 旧行为（Round 1 同一探针） | `running` / `running` |

→ **D3 已修复**：这是用 Round 1 完全相同的探针在修复后重跑，数字直接对照（`running` → `retrying`）。

**② 取消语义是否被吞**（关停时应"留给下次启动的 stale 恢复"，不能写成 `failed`）

| 指标 | 实测 |
|---|---|
| handler 阻塞中被 `stop()` 取消后的状态 | **`running`**（`not_written_as_failed = true`） |
| 模拟下次启动的 `mark_retrying()` 结果 | **`retrying`** |

→ 取消没有被 `except` 吞掉：代码层面确认 `_execute_task` 的兜底分支写的是 `except Exception`（`lifecycle_task_executor.py:193`），而 `CancelledError` 自 Python 3.8 起继承 `BaseException`，因此不会被捕获；行为层面实测与设计一致（关停不写终态，交给启动恢复）。

---

## 3. D1 · 密钥轮换

### 3.1 内容与构造

| 检查项 | 实测 |
|---|---|
| `SERVICE_API_KEY` 长度 | **43** |
| `ADMIN_API_KEY` 长度 | **43** |
| 两者互不相同 | **True**（未输出任何明文） |
| `IRA_DEPLOYMENT_ENVIRONMENT` | `local_staging`（13 字符） |
| 文件 mtime | `2026-10-29 10:48:42`（Round 1 时为 `2026-10-12`，确认已轮换） |
| 把该文件注入环境后 `Settings.from_env()` | **构造成功**：`env=local_staging, service_len=43, admin_len=43, distinct=True`（Round 1 同口径为 `ValueError`） |

### 3.2 `check_env.ps1` 与还原校验

| 命令 | 结果 |
|---|---|
| `.\scripts\check_env.ps1 -EnvFile .env.local_staging` | 13 项全部 `configured:`，`RESULT=OK`，`LASTEXITCODE=0` |

D2 验证时曾临时改写该文件，按下列流程保证零残留：

| 步骤 | 证据 |
|---|---|
| 改写前备份 | `sha256 = e78ae6ad00cdd3bed11dca6b9d3e1ae0b7b8284004d34ac71ea4591cba3274ca`（仓库内文件与 `D:/tmp/env_backup.local_staging` 哈希一致） |
| 植入短值后 | `SERVICE_API_KEY` 长度 19 |
| 还原后 | 哈希仍为 `e78ae6ad…274ca`，两 key 长度均 43，`env_restored_identical = True` |
| 还原后复跑 `check_env.ps1` | `RESULT=OK` / exit 0 |

### 3.3 gitignore 与工作区

| 检查项 | 结果 |
|---|---|
| `git check-ignore -v .env.local_staging` | `.gitignore:3:.env.*` → 仍被忽略 |
| `git status --porcelain` | **空**（轮换与临时改写都未污染工作树） |

→ **D1 关闭**。

---

## 4. D2 · 启动前门禁

### 4.1 顺序与失败点（实测）

把 `SERVICE_API_KEY` 临时改成 19 字符后运行 `.\scripts\start_workbench.ps1 -NoBrowser`，实际输出（节选）：

```
[1/6] Loading local staging environment...
[2/6] Validating local staging environment...
invalid: SERVICE_API_KEY must be at least 32 characters (generate with: python -c "import secrets;print(secrets.token_urlsafe(32))")
configured: ADMIN_API_KEY
...
invalid: SERVICE_API_KEY (shorter than 32 characters)
RESULT=FAIL missing_or_invalid=SERVICE_API_KEY_TOO_SHORT,SERVICE_API_KEY
CAUGHT: Environment validation failed (see the 'missing:' / 'invalid:' lines above). Fix .env.local_staging - regenerate short API keys with: python -c "import secrets;print(secrets.token_urlsafe(32))"
```

| 验收点 | 结果 |
|---|---|
| 是否在启动 Qdrant **之前**失败 | **是**。输出里完全没有 `[3/6] Starting Qdrant dependencies...`，说明 `throw` 发生在 `start_phase9b_qdrant.ps1` 之前（无容器/端口副作用） |
| 是否打印重新生成命令 | **是**，`invalid:` 行与 `CAUGHT:` 行各含一次 `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| 退出语义 | PowerShell `throw`（未继续执行后续步骤） |
| 还原后 | `check_env.ps1` 回到 `RESULT=OK`（见 §3.2） |

### 4.2 两条 `invalid:` 是清晰还是噪声？——结论：**可接受，但属冗余，建议收一处（P3）**

事实：短密钥时同一变量被报**两次**（`must be at least 32 characters …` + `(shorter than 32 characters)`），且 `RESULT=FAIL` 的名单里同时出现 `SERVICE_API_KEY_TOO_SHORT` 与 `SERVICE_API_KEY`。

判断依据：

- **改对的部分（有价值）**：Round 1 的"先说 invalid 再说 configured"的矛盾输出已消失 —— 本次输出里 `SERVICE_API_KEY` 不再出现 `configured:`，第一条消息带行动指引。这正是 T05 注释里写明的原因（"do not claim it is configured as well, which reads as contradictory"）。
- **仍冗余的部分（噪声）**：同一根因产生 2 行输出 + 2 个失败 token。对机器/人都意味着"两个问题"，实际只有一个；而提交信息写的是"the length failure is reported once"，与实际（报两次）不符。
- **建议**：`check_env.ps1` 第二个循环里，若 `$fail` 已含 `"$name`_TOO_SHORT"` 则直接 `continue`（不打第二行、不重复入 `$fail`），仅保留带生成命令的那一条。
- **是否阻断**：不阻断。exit code、执行顺序、可操作提示、还原后状态全部正确，故记 P3。

### 4.3 门禁覆盖面（供 Iteration 2 待办）

| 入口 | 是否有环境门禁 |
|---|---|
| `start_workbench.ps1` | **有**（本轮新增 [2/6]，在 Qdrant 之前） |
| `start_api.ps1` | **有**（既有：`check_env.ps1 -EnvFile`，非 0 则 `exit 1`） |
| `start_frontend.ps1` / `start_phase9b_qdrant.ps1` | 无（不消费 API 密钥，可接受） |
| 直接 `uvicorn` / `python -m …` | 无（不可门禁化，属固有旁路，建议在 docs 里一句话点明） |

注：`start_workbench` 与 `start_api` 会各跑一次 `check_env`（重复但幂等，仅几十毫秒），可接受。

---

## 5. D4 / D6 · 措辞与诊断可区分性

### 5.1 D4 `rate_limit.py` docstring 是否与行为一致

新表述（`rate_limit.py:5-12`）明确写：公开路由白名单在 Bearer 认证**之前**判定 → 打到公开路径的请求即便带 `Authorization` 也消耗匿名桶（未认证的 token 不可信）；非公开路径先认证，故不受限。

实测复核（Round 2 重跑，仍是当前代码）：

| 场景 | 实测 |
|---|---|
| 带有效 Bearer 连续 31 次 `GET /v1/knowledge-bases`（公开路径） | 第 31 次 **429**（tail `[200, 200, 429]`） |
| 带同一 Bearer 连续 35 次 `GET /metrics`（非公开路径） | 全部 **200**，不受限 |

→ 旧的错误表述（"Bearer-authenticated traffic is never rate limited here"）已删除，新表述与实测**逐字对应**。**D4 关闭**。

### 5.2 D6 `check_local.ps1` 能否区分 503 与不可达

用真实假服务（`D:/tmp/fake503.py`：`/ready` 固定返回 `503 {"status":"not_ready","components":{...}}`，`/health` 返回 200 degraded）与一个空端口各跑一次：

| 场景 | `api /ready:` 实际输出 |
|---|---|
| 假服务返回 503（端口 18081） | `not_ready (HTTP 503) components={"config":"ok","db":"unavailable","qdrant":"n/a"}` |
| 空端口（65500） | `unreachable (无法连接到远程服务器)` |

→ 两种情况下输出**语义可区分**，503 分支保留了 `HTTP 503` 与 components 明细，不再一律误报 `unreachable`。**D6 关闭**。
（附带观察：同一次运行中 `/version` 在两种情况下都打印 `unreachable` —— 假服务对 `/version` 返回 401，不在本次修复范围。）

验证后假服务已停止（`127.0.0.1:18081` 与 `:8000` 直连均无监听），工作区仍为 0 条改动。

---

## 6. 未修项登记（Round 1 遗留，供 Iteration 2 待办）

| 编号 | 级别 | 事项 | 当前状态（Round 2 复核） |
|---|---|---|---|
| D5 | P2 | ASGI 不报告 peer 时 `_anonymous_client_key` 回落 `"unknown"`，全站共用一个 30/min 桶 | **未修**（`api.py` 本轮无改动）。uvicorn 默认有 peer，风险低；建议加一次 WARN + docs 说明 |
| D7 | P2 | `/healthz` 无 `/ready` 那样的外层超时护栏，靠被调方自觉 | **未修**。线上 `_db_reachable` 自带 2 s 超时，不可达；属脆弱不变式 |
| D8 | P2 | `http_status_429_total` 恒为 0（限流响应在鉴权中间件内层提前返回），只有自定义 `rate_limited_total` 可见 | **未修**。按 `http_status_*` 做告警的看板看不到限流 |
| D9 | P3 | `tests/test_phase14a_parser_ab.py` 两条 identity 断言在 CI/新克隆永不执行（跳过条件依赖未入库 manifest 里的机器绝对路径） | **未修**（本轮仍为 28 skip 中的 2 条）。跳过本身安全，但 parser AB 制品身份在 CI 等于零覆盖 → 建议在 docs 登记为已知盲点 |
| D10 | P3 | 布局缓存 32 条，多中心场景会重算 `spring_layout` | **未修**，仅性能 |
| 新增 D11 | P3 | `check_env.ps1` 对短密钥仍输出 2 条 `invalid:` 且 `RESULT=FAIL` 名单重复计数（见 §4.2） | 本轮引入的诊断细节，建议下轮收敛为 1 条 |
| 门禁覆盖面 | 说明 | `start_workbench` / `start_api` 均已门禁；`start_frontend` / `start_phase9b_qdrant` 不涉及密钥；直接 `uvicorn` 为固有旁路 | 见 §4.3 |

以上无一项是本轮**新引入**的回归；D5/D7/D8/D9/D10 在 Round 1 已登记并明确留给 Iteration 2。

---

## 7. 结论

**VERDICT: GREEN**

判定依据：

1. **五项修复全部经独立验证成立**，且都是"改前 vs 改后"同口径对照，不是读代码推断：
   - D3：同一探针，`running` → **`retrying`**；取消语义保留（停机关停后状态仍为 `running`，下次启动 `mark_retrying` 可恢复），代码侧确认 `except Exception` 不捕 `CancelledError`。
   - D1：43/43、互不相同、`from_env()` 可构造、`check_env` `RESULT=OK`、仍被 gitignore、工作区干净；mtime 证明已轮换。
   - D2：短密钥时在 `[2/6]` 就 `throw`，**未触碰 Qdrant**，并打印重新生成命令；还原后哈希与内容完全一致、`check_env` 回到 OK。
   - D4：错误表述已删，新表述与实测（公开路径 429 / 非公开路径不限）逐字一致。
   - D6：503 与不可达输出可区分，503 分支带 `HTTP 503` 与 components。
2. **无回归**：1331 passed / 28 skipped / 退出码 0（较 Round 1 仅 +1 新用例）、ruff 通过、1468 文件 PASS、前端 25/25、工作区 0 改动、提交序列完整；T05 改动面仅 6 个文件且不含 api.py / 图谱 / 限流中间件。
3. **Round 1 的两个卡点（D1 本地栈起不来、D3 终态缺口）均已关闭**，这正是上一轮判 NO 的全部 P1 依据。
4. 剩余项最高只到 P2，且都不是本轮引入，已明确登记进 Iteration 2 待办；新发现的 D11 为 P3 诊断冗余，不影响可用性与安全性。

---

IS_PASS: YES
