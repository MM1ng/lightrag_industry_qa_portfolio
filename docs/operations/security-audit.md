# Permission, audit, and operations notes

## Permission model

The API distinguishes three execution situations:

- Public browser query access is limited to read-only query and source lookup paths.
- A service Bearer token authenticates service-to-service access.
- An admin Bearer token is required for document mutations, update jobs, Generation actions, evaluation dashboards, feedback metrics, and other management diagnostics.

Admin APIs must return 401 when credentials are missing/invalid and 403 when a valid service actor attempts an admin-only operation. Tokens are compared with constant-time digest comparison, and actor logs use a short hash-derived actor ID rather than token material.

The local no-key development actor is a deliberate local-only convenience. It must not be enabled in a public deployment; set service/admin credentials before exposing the API.

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
