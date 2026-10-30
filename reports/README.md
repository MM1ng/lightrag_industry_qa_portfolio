# Engineering evidence

These reports are the audit trail behind the numbers in the top-level README.
They are written during the work, not after it, and every claim in them carries a
file path, a command or a measurement.

| Report | Date | What it establishes |
|---|---|---|
| [`audit-report-2026-10-23.md`](audit-report-2026-10-23.md) | 2026-10-23 | Security and stability audit of the retrieval foundation: 5 high-severity findings (H1–H5), 6 medium (M1–M8), plus low-severity notes. Each finding carries file:line evidence. |
| [`fix-report-2026-10-23.md`](fix-report-2026-10-23.md) | 2026-10-23 | The fixes for H1–H5, with the failure each one prevents. Establishes why the anonymous surface is shaped the way it is. |
| [`takeover-gate-check-2026-10-29.md`](takeover-gate-check-2026-10-29.md) | 2026-10-29 | Independently re-ran the quality gate before any new work: `2 failed, 1288 passed` plus 3 collection errors that aborted the run. This is the "before" state. |
| [`iteration-1-plan-2026-10-29.md`](iteration-1-plan-2026-10-29.md) | 2026-10-29 | Read-only re-verification of every audit finding against the current code, with measurements (6 waiting tasks hold 6 of 15 connections; a graph overview blocks the loop for ~230 ms). Turns the audit into an ordered task list with explicit decisions. |
| [`iteration-1-qa-verification-2026-10-29.md`](iteration-1-qa-verification-2026-10-29.md) | 2026-10-29 | Round 1 independent verification. Every claim tested against a control group: the same script reads `pool.checkedout()=6` on the old code and `=2` on the new one. Verdict: YELLOW, two defects raised. |
| [`iteration-1-qa-verification-2026-10-29-round2.md`](iteration-1-qa-verification-2026-10-29-round2.md) | 2026-10-29 | Round 2: the two defects are closed with the same measurement, and the iterative gate is green (`1331 passed, 28 skipped`). |
| [`iteration-1-acceptance-checklist-2026-10-29.md`](iteration-1-acceptance-checklist-2026-10-29.md) | 2026-10-29 | The four-layer acceptance procedure anyone can run: environment, gate, per-fix evidence, operations smoke. Includes the blind spots the checklist cannot cover. |
| [`iteration-1-acceptance-run-2026-10-29.md`](iteration-1-acceptance-run-2026-10-29.md) | 2026-10-29 | Executing that checklist. 22 of 23 items passed; the failing one (a forged `X-Forwarded-For` bypassing the rate limit) is documented with the control experiment that proves the cause was the ASGI server, not the application code. |

## Not in this repository

Historical bundles from earlier phases (`acceptance_environment_20261014`,
`delivery_closeout_20261014`, `rag_anything_assessment_20261012`, …) are tens of
megabytes of logs, screenshots and generated artifacts. They stay in the local
working copy. Smoke-test logs and pid files are generated output and are ignored
by `.gitignore` on purpose.
