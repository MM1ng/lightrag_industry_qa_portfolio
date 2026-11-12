# 接管前确定性门禁核验报告

- **核验人**：QA（严过关）
- **核验日期**：2026-10-29
- **仓库路径**：`D:/基于Lightrag的工业手册问答系统/lightrag_industry_qa_portfolio-retrieval-foundation-upgrade`（git worktree）
- **分支 / 基线提交**：`dev/retrieval-foundation-qa-downstream` @ `ac24600b`（chore: prepare demo release candidate checkpoint）
- **核验性质**：只读。未修改任何源码、未执行 commit / stash / checkout
- **核验对象**：`reports/fix-report-2026-10-23.md` 中 S0–S4 五项安全修复及其声明的门禁基线

---

## 一、门禁结果表（实测 vs 声明）

| # | 命令 | 实际结果 | 与声明是否一致 |
|---|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m ruff check .` | `All checks passed!`（exit 0） | **一致** |
| 2 | `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/evaluation` | `2 failed, 1288 passed, 23 skipped, 1 warning in 131.07s`（exit 1） | **不一致**：声明 1290 passed / 0 failed；实测 **passed −2、failed +2**，skipped 23 一致，用例总数 1313 一致 |
| 3 | `./.venv/Scripts/python.exe scripts/check_public_repo.py` | `PUBLIC_REPO_CHECK=PASS files=1463`（exit 0） | **一致**（文件数与 1463 完全相同） |
| 4 | `cd frontend && npm run test -- --run` | `Test Files 6 passed (6)` / `Tests 25 passed (25)`，15.08s | **测试数一致**；但声明「前端代码 未改动」**不一致**（见 §五 风险 3） |
| 5 | S0–S4 代码落地核对 | 5 项全部落地（见 §二） | **一致**（但**全部未提交**，HEAD 中不存在，见 §五 风险 1） |

补充实测（用于定位失败用例，非门禁项）：`pytest -q tests/test_public_route_contract.py tests/test_query_timeout.py tests/test_upload_limit.py tests/test_document_source_api.py` → **51 passed**（33 + 6 + 4 + 8，与修复报告声明的 33 / 6 / 4 完全吻合）。

---

## 二、S0–S4 落地核对表

| 项 | 是否落地 | 证据（文件:行号） |
|---|---|---|
| **S0** 匿名路由白名单 `_PUBLIC_EXACT_ROUTES` | 是 | `src/industrial_rag/api.py:530-535`（精确路由表）、`:537` `_PUBLIC_DOCUMENT_SOURCE_SUFFIXES`、`:539-540` 前缀常量 |
| **S0** `_is_public_kb_query_path`（结构化解析，拒空 kb_id） | 是 | `src/industrial_rag/api.py:552-559`（要求恰好两段且 `segments[1] == "query"`）；`_is_public_browser_request` 改为常量表匹配 `:562-586` |
| **S3** `_insecure_local_dev_enabled`（local_dev **且** `IRA_DEV_INSECURE` 真值） | 是 | `src/industrial_rag/api.py:589-605`；构造期急算 `:786`；lifespan 重算 `:661-663` |
| **S3** `_log_auth_mode`（INSECURE_LOCAL_DEV / FAIL_CLOSED / bearer 三态日志） | 是 | `src/industrial_rag/api.py:608-624`，调用点 `:664-667` |
| **S3** `_HEALTH_PATHS` 判定前移（fail-closed 下探针仍可达） | 是 | `src/industrial_rag/api.py:544-546`，中间件最先判定 `:814-815` |
| **S3** 无密钥返回 **401** 而非注入 admin | 是 | `src/industrial_rag/api.py:816-823`：`service_api_key is None and admin_api_key is None` → 未开本地开关即 `UNAUTHORIZED` 返回；仅在 `:824` 显式 opt-in 后调用 `local_development_actor()`。异常回退分支 `:749-761` 仅从环境读取密钥，无 admin 注入 |
| **S1** `_single_page_pdf`（PyMuPDF `insert_pdf` 裁单页） | 是 | `src/industrial_rag/routers/documents.py:177-190` |
| **S1** `_attachment_disposition`（RFC 5987 `filename*=utf-8''`） | 是 | `src/industrial_rag/routers/documents.py:193-194`，使用点 `:366` |
| **S1** `source-file` 按 `page` 裁单页（不再是整包） | 是 | 路由 `:333-340`（`page: int = Query(..., ge=1)`）、`:347` 范围校验、`:357` `asyncio.to_thread(_single_page_pdf, file_path, page)`；`:359-360` 失败告警、`:352` 文件缺失 404 |
| **S2** `_run_query_with_timeout`（硬超时） | 是 | `src/industrial_rag/services/query_application_service.py:43-77`（`asyncio.timeout` @ `:59`），调用点 `:226-233`（传入 `settings.qa_request_timeout_seconds` @ `:232`） |
| **S2** `qa_timeout` / `QA_TIMEOUT` → 504 | 是 | `src/industrial_rag/errors.py:39`（`qa_timeout = "QA_TIMEOUT"`）、`:72`（`AppErrorCode.qa_timeout: 504`）；抛出点 `query_application_service.py:71-77` |
| **S2** `qa_request_timeout_seconds` 默认 180 | 是 | `src/industrial_rag/config.py:89`（`float = 180.0`）、`:142-143` 非正数校验、`:278` 读 `QA_REQUEST_TIMEOUT_SECONDS`、`:391` 注入 Settings |
| **S4** `_read_upload_limited`（1 MB 分块、超限即 413） | 是 | `src/industrial_rag/routers/documents.py:143-163`（`:158-162` 累计判限）、`_too_large` `:166-174`（status 413）、`.size` 预检 `:152-154` |
| **S4** 上传/替换两处均改用限量读取 | 是 | `documents.py:207`（`upload_document`）、`:231`（`replace_document`） |
| 新增 `tests/conftest.py` | 存在 | 16 行；autouse fixture 设 `IRA_DEV_INSECURE=1` + `IRA_DEPLOYMENT_ENVIRONMENT=local_dev` |
| 新增 `tests/test_public_route_contract.py` | 存在 | 173 行，**33 个用例**（声明 33，一致） |
| 新增 `tests/test_query_timeout.py` | 存在 | 104 行，**6 个用例**（声明 6，一致） |
| 新增 `tests/test_upload_limit.py` | 存在 | 54 行，**4 个用例**（声明 4，一致） |
| 增强 `tests/test_document_source_api.py` | 是 | `+24 / −2`，含 8 个用例，全部通过 |
| 文档同步 | 是 | `.env.example`（新增 `IRA_DEV_INSECURE=` 与 fail-closed 说明）、`README.md:274`（fail-closed + 401 + 本地开关说明） |

**关键判断**：S0–S4 **代码层面全部真实落地且被测试锁定**，五项修复不是"报告声称"而是可在工作区源码中逐行核对的实体实现。

---

## 三、pytest 失败用例（共 2 个，同源）

```
tests/test_phase14a_parser_ab.py::test_identity_roundtrips_without_type_drift
tests/test_phase14a_parser_ab.py::test_saved_artifact_full_offline_replay_and_same_report_semantics
```

- **异常**：`FileNotFoundError: [Errno 2] No such file or directory: 'D:\基于Lightrag的工业手册问答系统\lightrag_industry_qa_portfolio\data\manuals\2196-ANSI-Manual-Chinese.pdf'`
- **触发链**：`scripts/phase14a_parser_ab.py:359 identity()` → `:93 verify_pdf()` → `:56 sha()` → `Path.read_bytes()`
- **根因**：`docs/phase-14a-pymupdf-vs-mineru-parser-ab.json` 中 `sources[].path` 记录的是**原始克隆目录的绝对路径**（指向 worktree 的兄弟目录 `lightrag_industry_qa_portfolio`，而非当前 worktree）。该目录存在，但 `data/manuals/` 下只剩 `README.md`，PDF 本体缺失（数据文件不入库）。
- **与本轮修复的关系**：**无关**。`tests/test_phase14a_parser_ab.py`、`scripts/phase14a_parser_ab.py`、`docs/phase-14a-*.json` 三个文件在工作区均为 clean（`git status --porcelain` 无输出），失败纯属**环境/数据缺失**，不是 S0–S4 引入的回归。
- **结论**：非回归失败，但因"数据清单硬编码外部绝对路径"，该用例在任何缺少该 PDF 的机器上都会失败 → 属可移植性缺陷（P2）。

---

## 四、风险清单

| # | 级别 | 风险 | 证据 / 说明 |
|---|---|---|---|
| 1 | **P0（阻断接管）** | **S0–S4 全部修复未提交**，只存在于工作区；`ac24600b` 中一个符号都没有 | `git show HEAD:src/industrial_rag/api.py \| grep -c "_PUBLIC_EXACT_ROUTES\|_log_auth_mode"` → **0**；`documents.py` 的 `_single_page_pdf/_read_upload_limited` → **0**；`query_application_service.py` 的 `_run_query_with_timeout` → **0**；`errors.py` 的 `qa_timeout` → **0**。而 HEAD 的提交信息是 "prepare demo release candidate checkpoint"，即"RC 检查点"上并无这些安全修复。任何 `checkout/reset/clone` 都会让修复消失 |
| 2 | **P0（阻断接管）** | **工作区混合两条互不相关的工作流，无法拆分提交** | 68 条 porcelain 记录 = 47 个已修改 + 21 个未跟踪条目（`-uall` 展开为 28 个文件），0 个已暂存，`1139 insertions / 260 deletions`。其中 S0–S4 相关仅 6 个文件（`api.py`、`errors.py`、`config.py`、`documents.py`、`query_application_service.py`、`test_document_source_api.py`，273 增 41 删），其余为 batch G1 / 评估中心 / 前端 LifecycleTimeline 等另一条线的工作。`git commit -a` 会把安全修复与无关功能改动压成一个不可区分的提交 |
| 3 | P1 | 修复报告「前端代码 未改动」与工作区事实不符 | 实测前端有 3 个已修改文件（`GenerationsView.vue`、`KnowledgeBasesView.vue`、`admin-views.test.ts`，+18/−6）+ 1 个新增组件 `frontend/src/components/admin/LifecycleTimeline.vue`。测试数（25/6）与基线一致，说明前端**测试是全绿的**，但"未改动"的表述在接管场景下会误导判断（会让人以为工作区只有后端改动） |
| 4 | P1 | 声明基线不可复现 | 「1290 passed / 0 failed」在本机无法复现：实测 1288 passed / 2 failed。差异恰好等于 §三 的 2 个数据缺失用例，说明报告的数字是在"PDF 存在"的机器上测得，未把环境前提写进报告 |
| 5 | P2 | `tests/conftest.py` autouse 全局打开不安全模式 | 该 fixture 对**所有**测试设 `IRA_DEV_INSECURE=1`，即默认执行路径是"无密钥=admin"。fail-closed 语义仅由 `test_public_route_contract.py:130-146` 等 3 个显式 `delenv` 用例守护（已确认 `:133` 有 `monkeypatch.delenv`，写法正确）。风险在于后续新增测试会在不自知的情况下依赖不安全模式，掩盖鉴权缺陷 |
| 6 | P2 | PyMuPDF `fitz` 旧 API 已弃用 | 实测 `PyMuPDF 1.28.2` 导入即告警：`The fitz API is deprecated and will be removed in future. Use import pymupdf instead.`；`documents.py:180` 使用 `import fitz`，升级后将断裂 |
| 7 | P2 | `tests/evaluation` 被 `--ignore` 跳过 | 门禁口径不含评估链路；`ragas` 未装时 3 个 `tests/evaluation/*` 会中断整套 `pytest`，因此门禁命令必须继续带 `--ignore`，否则数字不可比 |
| 8 | P3 | 环境已确认可用 | Python 3.11.9 (win32 x64)、httpx 0.28.1、PyMuPDF 1.28.2、git 2.55.0；`ruff`、`pytest`、`node/npm` 均可用，`frontend/node_modules` 已就绪。无依赖缺失导致的失败 |

---

## 五、结论

**CURRENT_STATE = YELLOW**

判定依据：

- **不是 RED**：S0–S4 五项安全修复**逐项真实落地**（§二 全部命中，含行号），且其专属 51 个用例全绿；`ruff` 与 `check_public_repo` 完全复现声明；前端 25/25 全绿。没有发现安全修复"假落地"或实现缺陷。
- **不是 GREEN**，三条硬理由：
  1. 声明的 pytest 基线（1290 passed / 0 failed）**不可复现**，实测 2 failed / 1288 passed；
  2. 这 2 个失败虽属环境数据缺失、非本轮回归，但**门禁本身此刻不是全绿**，且失败原因（清单硬编码外部绝对路径）是可移植性缺陷；
  3. **全部安全修复只存在于工作区**，`ac24600b` 中零落地，且工作区混合两条工作流（`git commit -a` 即产生不可区分提交）——这是接管阶段最高危的状态：代码是对的，但**没有任何一处被固化**。
- **转 GREEN 的最小动作**（建议顺序）：① 先按文件白名单单独提交 S0–S4（5 源文件 + 4 测试文件 + 2 文档），`git add` 精确到路径而非 `-a`；② 再单独提交 batch G1 / 前端那条线；③ 对 `phase14a` 两个用例改为相对路径或 `skipif(not pdf.exists())`，使门禁在无手册数据的机器上也确定可绿；④ 复跑 `pytest -q --ignore=tests/evaluation` 应得 `1290 passed, 23 skipped`。

---

IS_PASS: NO
