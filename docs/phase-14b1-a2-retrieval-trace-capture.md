# Phase 14B-1 — Canonical A2 Retrieval Trace Capture

**Status:** `TRACE_CAPTURE_BLOCKED`

## 1. Executive Summary

Replayed capture did not satisfy the per-question final Top5/Top10 alignment gate. No root-cause analysis was performed.

## 2. Identity Verification

- Identity match: `True`
- authority_sha256: `True`
- dataset_fingerprint: `True`
- generation: `True`
- question_ids: `True`
- document_fingerprint: `True`
- chunk_fingerprint: `True`
- index_fingerprint: `True`
- gold_mapping: `True`

## 3. Alignment Gate

- Top5 mismatches: `['D-V2-011']`
- Top10 mismatches: `['D-V2-011']`
- Runtime error: `None`

Root-cause analysis was not performed because the final-ranking alignment gate did not pass.
