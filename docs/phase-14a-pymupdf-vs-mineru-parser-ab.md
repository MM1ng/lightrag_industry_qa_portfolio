# Phase 14A — PyMuPDF vs MinerU Parser A/B

- Status: `PARSER_AB_BLOCKED`
- Scope: frozen Development V2 only; no retrieval, index, gold, or QA changes.
- Frozen generation: `dev-v2-20260902`
- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`
- PDF identities were verified before the run.

The existing MinerU v4 client successfully submitted the first frozen PDF and
reached a completed task, but the result ZIP download from the returned CDN/OSS
URL failed with `httpx.ConnectError`. Therefore no MinerU parser output was
available for a valid comparison. No parser metrics are reported and no
retrieval or production artifact was modified. Re-run this parser-only phase
after the MinerU result-download network path is reachable.
