# External-service failure and degradation policy

The system must fail closed for evidence and fail clearly for infrastructure. It must never invent an answer, citation, page, or Generation when an upstream service is unavailable.

| Failure | API behavior | User behavior | Operator action |
| --- | --- | --- | --- |
| API process unavailable | No response | Frontend shows service unavailable and retry | Check process, logs, `/healthz`, `/readyz` |
| Database unavailable | `/healthz` degraded; `/readyz` not ready | Query disabled or readiness error | Restore DB/connectivity; do not promote generations |
| Qdrant unavailable | `/readyz` not ready for Qdrant backend | Query disabled with retryable error | Check Docker/container, URL, collection and generation identity |
| Active Generation not ready | `GENERATION_NOT_READY` / `INDEX_NOT_READY` | Explain that the current manual is not ready | Inspect Active pointer and generation artifacts |
| Provider timeout | `QA_TIMEOUT` or retryable timeout | Keep question, show retry action; no fabricated answer | Check provider latency/quota and request ID |
| Provider transport/upstream failure | `UPSTREAM_UNAVAILABLE` | Explain temporary unavailability; no unsupported answer | Check provider status, quota and redacted logs |
| Provider model fallback exhausted | Error is returned after bounded attempts | Retryable failure, not a guessed answer | Inspect fallback model configuration and attempt count |
| Evidence/grounding failure | `insufficient_evidence` or `partial_answer` | Show only supported claims and citations | Review trace and source snapshot |
| Citation source unavailable | Citation excerpt remains visible; source open reports unavailable | Do not replace page/document with guessed data | Check document version and source file access |

## Guardrails

- Retry budgets and timeouts remain bounded; retries must not turn into an unbounded request loop.
- A fallback model may produce a response only if the same evidence and grounding contract passes.
- No fallback may cross Knowledge Base or Generation boundaries.
- `/readyz` is a deployment gate, not a promise that every provider request will succeed.
- Alert on repeated 5xx, `QA_TIMEOUT`, `UPSTREAM_UNAVAILABLE`, `GENERATION_NOT_READY`, and validation failures.
- Preserve request ID and trace ID in redacted logs; never log bearer tokens, provider keys, full prompts, or full manual pages.
