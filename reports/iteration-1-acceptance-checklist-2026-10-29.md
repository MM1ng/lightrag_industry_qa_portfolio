# Iteration 1 验收清单

**验收对象**：`D:/基于Lightrag的工业手册问答系统/ira-iteration-1`（分支 `dev/iteration-1`）
**验收基线**：`ef207490` → `3278c06e`（5 个提交）
**验收日期**：2026-10-29
**证据报告**：`reports/iteration-1-qa-verification-2026-10-29.md`（Round 1）、`...-round2.md`（Round 2，GREEN）

> 所有命令在项目根目录执行，shell 为 Git Bash。PowerShell 脚本用 `powershell.exe -File` 调用。

---

## 第 0 层：环境对不对（30 秒）

| 检查 | 命令 | 期望 |
|---|---|---|
| 工作区干净 | `git status --porcelain` | 输出为空 |
| 提交齐全 | `git log --oneline -6` | 首行 `3278c06e`，共 6 条（含基线 `ef207490`） |
| 原目录未被动过 | `git -C "../lightrag_industry_qa_portfolio-retrieval-foundation-upgrade" log --oneline -1` | `ac24600b` |
| 原目录仍待提交 | `git -C "../lightrag_industry_qa_portfolio-retrieval-foundation-upgrade" status --porcelain \| wc -l` | `68`（47 改 + 21 未跟踪） |

> 这一层是"用户要求原版代码不动"的验收点。任一项不符即说明隔离环境被污染。

---

## 第 1 层：门禁（自动化，约 3 分钟）

| # | 命令 | 期望输出 |
|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m pytest -q` | `1331 passed, 28 skipped`，**无** `error` / `Interrupted`，退出码 0 |
| 2 | `./.venv/Scripts/python.exe -m ruff check .` | `All checks passed!` |
| 3 | `./.venv/Scripts/python.exe scripts/check_public_repo.py` | `PUBLIC_REPO_CHECK=PASS files=1468` |
| 4 | `cd frontend && npm run test -- --run` | `Test Files 6 passed` / `Tests 25 passed` |

**注意第 1 条不要加 `--ignore=tests/evaluation`**。T01 的核心成果就是让这条命令在未安装 `ragas` 时也能跑完；加上 `--ignore` 等于把这项修复的验收绕过去了。
28 个 skip 的构成：3 个 `tests/evaluation/*` 模块级（无 ragas）+ 25 个 in-tree（含 2 个 phase14a，原因为原始手册未分发）。

---

## 第 2 层：逐项修复证据（自动化，每项 <5 秒）

| 任务 | 命令 | 期望 | 这一项证明什么 |
|---|---|---|---|
| T01 密钥长度 | `pytest -q tests/test_config.py -k "short or length" -v` | 4 passed | `staging/production` 拒短密钥、`local_dev` 只告警、`None` 不参与校验 |
| T02 执行器 | `pytest -q tests/test_task_executor_concurrency.py -v` | 5 passed | 等信号量时不持连接、句柄可回收、异常必落终态、身份读失败也不卡 running |
| T03 `/ready` | `pytest -q tests/test_ready_probe.py -v` | 5 passed | 端点是 async、无 `asyncio.run`、探针挂死也 <3s 返回 503 |
| T03 图谱/PDF | `pytest -q tests/test_graph_threading.py tests/test_document_source_api.py -v` | 2 + 9 passed | 布局不在 loop 线程、graphml 只解析一次、PDF 页读取在线程里 |
| T04 限流 | `pytest -q tests/test_anonymous_rate_limit.py -v` | 7 passed | 第 31 次 429 + `Retry-After`、Bearer 管理路径不限、`0` 关闭、桶隔离 |
| S0–S4 回归 | `pytest -q tests/test_public_route_contract.py tests/test_query_timeout.py tests/test_upload_limit.py` | 33 + 6 + 4 passed | 上一轮安全修复没有退化 |

> 这些测试是工程师写的，属于"自证"。真正的反证在 QA 报告里：用同一脚本在旧实现上跑出 `pool.checkedout()=6`（新实现 `=2`）、把 `to_thread` 换回直接调用后并发 `/version` 从 0.047s 变成 0.438s。要复核请读 Round 1 报告第二节。

---

## 第 3 层：运维冒烟（手工，最容易被漏掉的一层）

自动化测试全绿 ≠ 服务能起来。这一层专门验"配置改动是否把本地栈弄死"。

### 3.1 环境变量自检
```powershell
powershell.exe -File scripts/check_env.ps1 -EnvFile .env.local_staging
```
期望：13 项 `configured:` + `RESULT=OK` + 退出码 0。

密钥长度（不要打印明文）：
```bash
./.venv/Scripts/python.exe -c "
from dotenv import dotenv_values
v = dotenv_values('.env.local_staging')
print('service', len(v['SERVICE_API_KEY']), 'admin', len(v['ADMIN_API_KEY']), 'differ', v['SERVICE_API_KEY']!=v['ADMIN_API_KEY'])"
```
期望：`service 43 admin 43 differ True`。

### 3.2 启动门禁必须挡在 Qdrant 之前
把 `.env.local_staging` 里的 `SERVICE_API_KEY` 临时改成 19 个字符（**先备份**），然后：
```powershell
powershell.exe -File scripts/start_workbench.ps1 -NoBrowser
```
期望：输出 `[1/6]` → `[2/6] Validating` → `invalid: SERVICE_API_KEY must be at least 32 characters` → `RESULT=FAIL` → 抛错退出，**输出里不应出现 `[3/6] Starting Qdrant`**。
验完必须还原文件，核对与备份完全一致。

### 3.3 服务真能起来 + 探针语义
```powershell
powershell.exe -File scripts/start_workbench.ps1 -NoBrowser
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/ready   # 期望 200
curl -s http://127.0.0.1:8000/ready                                    # 期望 status=ready, components.db=ok
```

### 3.4 限流真的生效（手工最直观）
```bash
KB=<当前 KB 的 id>
for i in $(seq 1 31); do
  curl -s -o /dev/null -w "$i:%{http_code} " -X POST \
    "http://127.0.0.1:8000/v1/knowledge-bases/$KB/query" \
    -H "Content-Type: application/json" -d '{"question":"smoke"}'
done; echo
```
期望：前 30 次非 429（可能是 200 或业务错误码），**第 31 次为 `429`**。

```bash
curl -si -X POST "http://127.0.0.1:8000/v1/knowledge-bases/$KB/query" \
  -H "Content-Type: application/json" -d '{"question":"smoke"}' | grep -i "retry-after\|RATE_LIMITED"
```
期望：有 `Retry-After: <秒>`，body 里 `code` 为 `RATE_LIMITED`。

feedback 独立桶（第 6 次应为 429）：
```bash
for i in $(seq 1 6); do curl -s -o /dev/null -w "$i:%{http_code} " -X POST \
  http://127.0.0.1:8000/v1/feedback -H "Content-Type: application/json" -d '{}'; done; echo
```

### 3.5 伪造头不能绕过（安全属性）
```bash
# 未开 IRA_TRUST_PROXY_HEADERS 时，换 X-Forwarded-For 仍应被限流
for i in $(seq 1 31); do curl -s -o /dev/null -w "%{http_code} " -X POST \
  "http://127.0.0.1:8000/v1/knowledge-bases/$KB/query" \
  -H "Content-Type: application/json" -H "X-Forwarded-For: 10.0.0.$i" -d '{"question":"x"}'; done; echo
```
期望：第 31 次仍是 `429`（否则等于限流可被伪造头绕过）。

---

## 第 4 层：代码评审入口（可选）

按风险从高到低读 diff：

| 提交 | 内容 | 建议关注 |
|---|---|---|
| `dbd75274` | T02 执行器 | `_execute_task` 两段会话是否真的不跨会话复用 ORM 实例；`_ensure_terminal_status` 的幂等守卫 |
| `bee9fbf8` | T03 事件循环 | `/ready` 外层超时是否覆盖被 patch 的探针；图缓存 key 是否含 `center` |
| `28252f1b` | T04 限流 | 限流判定是否严格在匿名分支内（位置提前会让 1300+ 存量测试 429）；`_ERRORS` 与 `HTTP_STATUS_MAP` 是否都登记 |
| `4de9cf91` | T01 门禁 | `importorskip` 是否在 `evaluation.*` import 之前；phase14a 的 skip 是否为动态条件（资产存在时必须真跑） |
| `3278c06e` | T05 收尾 | 兜底 except 是否只捕 `Exception`（不能吞 `CancelledError`） |

---

## 判定标准

**PASS**：第 1 层 4 项全绿 + 第 3.1/3.2/3.3 通过 + 第 3.4 第 31 次为 429。
**不通过**：任一 pytest 失败 / `check_env` 非 OK / `/ready` 非 200 / 限流未触发 / 原目录 HEAD 不是 `ac24600b`。

---

## 本清单覆盖不到的盲点（必须知情）

| 盲点 | 原因 | 影响 |
|---|---|---|
| phase14a 两条 identity 断言在 CI 永久 skip | 跳过条件读未入库的 manifest 绝对路径 | parser A/B 制品身份在 CI 等于零覆盖（D9） |
| 真实 LLM / Qdrant / MinerU 端到端 | 需真实凭据与 `IRA_QDRANT_E2E=1` 等 opt-in 开关 | 检索质量回退无法被本清单发现 |
| 前端 Playwright E2E | 本轮未跑 `npm run e2e` | 管理页交互回归不可见 |
| 多副本限流 | limiter 是进程内单实例 | 多副本部署需 ingress 层兜底 |
| `http_status_429_total` 指标 | 恒为 0，只有 `rate_limited_total` 可见（D8） | 监控看板会缺一个维度 |
