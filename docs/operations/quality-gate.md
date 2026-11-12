# Quality gate and current verification record

This document separates reproducible repository checks from checks that require
protected runtime data, external services, or a frozen evaluation environment.
It is intentionally not a production-readiness claim.

## Reproducible checks

Run from the repository root in Python 3.11:

```powershell
python -m pip install -e ".[dev,evaluation]"
python -m ruff check .
python -m pytest -q
```

Frontend checks:

```powershell
cd frontend
npm ci
npm run test -- --run
npm run build
```

The sanitized-repository preflight is:

```powershell
python scripts/check_public_repo.py
```

It scans tracked and non-ignored candidate files for runtime databases,
virtual environments, generated artifacts, private keys, and high-confidence
API-key patterns. It does not replace a human copyright and licensing review.

## Verification record for the current candidate

| Check | Result | Interpretation |
|---|---:|---|
| Public repository preflight | PASS, 1,446 files | No detected secret/runtime-path violation in the candidate set |
| Ruff | PASS | Static Python quality gate is green |
| Frontend Vitest | PASS, 25 tests | Includes Generation lifecycle timeline coverage |
| Frontend production build | PASS | `vue-tsc --noEmit` and Vite build completed |
| Python full clean-environment run | 1,244 passed, 23 skipped, 1 warning | Green; skipped cases are explicit protected-data, real-provider, or opt-in E2E boundaries |
| HTTP health concurrency baseline | PASS | 50/50 successful at concurrency 10; P95 25.56 ms and 1,040.92 requests/s on the local run |

## Deliberate boundaries

The 23 skipped cases are reported by pytest with reasons. They cover protected
manuals or MinerU raw payloads that are not distributed, saved historical
replay artifacts, and real DashScope/Qdrant or MinerU calls that require an
explicit opt-in environment variable. A green clean test run therefore proves
the repository's offline contracts; it does not claim that every external
provider or production-sized corpus has been exercised.

The evaluation extra pins the tested LangChain 0.3-compatible set used by
`ragas==0.3.9`. If a future dependency update changes this set, the
Ragas import/API contract test must fail in CI before evaluation artifacts are
trusted.
