# Agent Development Rules

## Loop Engineering

For bounded engineering goals with explicit acceptance criteria and deterministic verification, use this cycle:

`inspect current state → identify unsatisfied acceptance criteria → choose the smallest coherent next step → implement → deterministic verification → persist concise state → repeat or exit`

Completion requires deterministic evidence such as executable tests, static checks, Git diffs, database invariants, filesystem invariants, or other deterministic evidence; agent self-report is not evidence.

Supported exits are `SUCCESS`, `BLOCKED`, `NO_PROGRESS`, `BUDGET_LIMIT`, and `PHASE_BOUNDARY`. Exit `NO_PROGRESS` after two consecutive iterations that do not reduce unsatisfied acceptance criteria. Do not retry indefinitely. After `SUCCESS`, stop; never automatically enter the next project phase.

When applicable, Superpowers may guide practice within one iteration (for example brainstorming, planning, TDD, debugging, or verification). Loop Engineering owns the goal, acceptance criteria, iteration boundary, progress state, stopping, and phase boundary. Do not run competing autonomous outer loops.

Loop Engineering governs Codex development workflow only. It does not authorize or require product runtime loops, including Worker Pollers, background workers, Recovery Sweepers, retry schedulers, async runtime dispatch, or FastAPI lifespan runtime. Those require explicit phase-scoped requirements.
