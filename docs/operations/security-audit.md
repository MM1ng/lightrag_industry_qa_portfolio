# Permission, audit, and operations notes

## Permission model

The API distinguishes three execution situations:

- Public browser query access is limited to read-only query and source lookup paths.
- A service Bearer token authenticates service-to-service access.
- An admin Bearer token is required for document mutations, update jobs, Generation actions, evaluation dashboards, feedback metrics, and other management diagnostics.

Admin APIs must return 401 when credentials are missing/invalid and 403 when a valid service actor attempts an admin-only operation. Tokens are compared with constant-time digest comparison, and actor logs use a short hash-derived actor ID rather than token material.

The local no-key development actor is a deliberate local-only convenience. It must not be enabled in a public deployment; set service/admin credentials before exposing the API.

### Bearer token minimum length

`SERVICE_API_KEY` and `ADMIN_API_KEY` must be at least **32 characters** (about 256 bits when generated with `secrets.token_urlsafe(32)`).

- `IRA_DEPLOYMENT_ENVIRONMENT` in `local_staging` / `staging` / `production`: a shorter key makes `Settings` raise `ValueError` and the process refuses to start.
- `IRA_DEPLOYMENT_ENVIRONMENT=local_dev`: a shorter key only logs a warning, so local iteration keeps working.
- An unset (`None`) key never fails the length check; absence is handled fail-closed by the authentication middleware, which returns 401.

Generate a compliant key with:

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

**Migration note.** Existing deployments must regenerate both keys before upgrading. The checked-in `.env.local_staging` still holds legacy short values (service key 26 characters, admin key 6 characters), so starting the workbench in `local_staging` will now fail with `SERVICE_API_KEY 长度必须不少于 32 个字符`. Replace the values, then re-run `.\scripts\check_env.ps1 -EnvFile .env.local_staging`.

## Audit fields

Every request should be diagnosable with:

- `request_id`
- `trace_id`
- authenticated actor role and redacted actor ID
- HTTP status and sanitized error code
- Knowledge Base and Generation identity where applicable
- UpdateJob / validation / Promote identifiers for lifecycle operations

Logs must not contain API keys, Authorization headers, full uploaded manuals, unrestricted model prompts, or raw provider responses. Feedback writes are best-effort and must not change the answer already returned to a user.

## Operational runbook

1. Check `/health`, `/healthz`, and `/readyz`.
2. Check API, frontend, Docker/Qdrant process state and recent redacted logs.
3. Confirm the Knowledge Base Active Generation pointer and validation status.
4. For update failures, inspect UpdateJob stage, error code and retry budget; do not manually edit lifecycle rows.
5. For a Candidate, validate its immutable evidence before Promote. Never point the API at a building or failed Generation.
6. Preserve the current Active Generation while investigating. Rollback is an explicit authorized change and requires a verified target.

## Public-release checklist

- [ ] `.env`, local staging env, tokens and keys are absent.
- [ ] Runtime DB, Qdrant collections, logs, virtual environments and generated artifacts are absent.
- [ ] Original manuals are excluded or distributed only with documented permission.
- [ ] `python scripts/check_public_repo.py` passes.
- [ ] CI passes from a fresh checkout.
- [ ] Internal-tool limitations and the boundary between the sanitized repository and the real company environment are visible in README.
