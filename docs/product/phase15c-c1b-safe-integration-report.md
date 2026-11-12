# Phase15-C1B Safe Integration Report

## Implemented

- Activated an additive Alembic revision `c15c1b0a4e5f`. It adds nullable execution-contract columns and constraints without classifying or modifying Phase15-B rows.
- Added a typed `ClaimedExecutionContext` projection to `ClaimedUpdateJob` and a service entry point that consumes an already-held claim after checking the persisted job identity and current KB lease. It never claims a second time.
- Added deterministic parsed-artifact staging paths under `parsed/attempts/<job>/<attempt>/<document>/staging`.
- The claim-consumption candidate path writes changed-document parse output to its attempt path and accepts it only with `complete.json`; partial attempt output is rejected.

## Tested

- `pytest tests/test_phase15c_c1_contract_models.py -q`: 35 passed, 1 skipped.
- `pytest tests/test_phase15b_unified_document_lifecycle.py -q`: 26 passed.
- `ruff check src tests`: passed.
- Migration test upgrades a Phase15-B database to head and verifies legacy `ready` jobs retain NULL execution status.

## Still Deferred

- No Worker/Poller loop, recovery sweeper, retry dispatch, runtime HTTP behavior, or frontend behavior was added.
- Full persistent input-snapshot capture for all five operations and repository-level KB-lease fencing for every candidate/checkpoint/success/failure/document-metadata write require the next bounded C1B follow-up. This change provides the typed ownership entry point and parsed-artifact isolation only; it does not claim those remaining writes are fenced.
- The legacy synchronous path remains compatible and continues to use `parsed/documents/<document>/current`; only the claimed-context path uses per-attempt staging.
