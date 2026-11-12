# Deployment and clean-environment guide

本项目定位为实习公司内部使用的工业手册问答与知识库运维工具。仓库中的配置、数据和凭据均按脱敏后的受控验证环境组织，不代表真实公司知识库可以直接公开。正式内部部署前仍需替换 SQLite、接入公司身份认证和密钥管理、配置 TLS、外部监控及容量评估；下面的流程用于内部环境部署和受控验证。

## Prerequisites

- Python 3.11
- Node.js 20+ and npm
- Docker Desktop running
- An OpenAI-compatible provider key
- Qdrant when `VECTOR_BACKEND=qdrant`

## Clean setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,evaluation]"
Copy-Item .env.example .env
npm --prefix frontend ci
```

Only fill the target internal environment's `.env` values. Never commit `.env`, `.env.local_staging`, provider keys, PDF manuals, runtime databases, logs, or generated indexes.

## Start and verify

```powershell
.\scripts\start_workbench.ps1 -NoBrowser
Invoke-RestMethod http://127.0.0.1:8000/readyz
Invoke-WebRequest http://127.0.0.1:5173/chat -UseBasicParsing
```

The expected readiness response is `{"status":"ready"}` and the frontend should return HTTP 200. The standard script starts Qdrant, API, and Vue for the internal tool; it does not publish an external service.

## Quality gate

```powershell
python scripts/check_public_repo.py
python -m ruff check .
python -m pytest -q
npm --prefix frontend run test -- --run
npm --prefix frontend run build
```

## Graph availability

Graph reads are scoped to a knowledge base: `GET /v1/knowledge-bases/{kb_id}/graph/{overview,neighborhood,native}`.
The file is looked up inside that knowledge base's active generation workspace
(`<workspace>/<backend>-<generation>/graph_chunk_entity_relation.graphml`).

Two consequences worth knowing before a demo:

- A knowledge base whose workspace has no GraphML returns `404 graph_not_found`. The workbench then shows "当前知识库没有可用的知识图谱" instead of an empty canvas.
- Qdrant-backed knowledge bases keep entities and relations in the Qdrant
  collections (`..._entities`, `..._relationships`); they only produce a GraphML
  if the ingestion path exports one. Reading the graph straight from Qdrant is
  not implemented yet.

Previously the routes were application-scoped and served whichever GraphML sat
in the process `working_dir`, which could belong to no knowledge base at all.

## Production boundary
This repository does not claim that the real company environment has already been deployed or has a production SLO. Before internal production use, establish an approved deployment topology, PostgreSQL/backup policy, Qdrant capacity, provider quotas, company authentication, secret rotation, alerting, and a load test against production-equivalent infrastructure.
