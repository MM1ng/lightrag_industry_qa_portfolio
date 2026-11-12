"""Read-only Phase 14C-0 audit for the historical canonical A2 artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.canonical_evaluation_artifact_v2 import (  # noqa: E402
    inspect_legacy_canonical_artifact,
)

EVALUATION = ROOT / "evaluation" / "retrieval_foundation"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(canonical_path: Path, contract_path: Path) -> dict[str, Any]:
    """Inspect v1 compatibility without writing to either input artifact."""
    before_hash = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
    canonical = _read_json(canonical_path)
    contract = _read_json(contract_path)
    legacy = inspect_legacy_canonical_artifact(canonical)
    after_hash = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
    authority_matches = before_hash == contract.get("authority_sha256")
    return {
        "phase": "Phase 14C-0",
        "status": "ARTIFACT_SCHEMA_READY" if legacy["compatible"] and authority_matches and before_hash == after_hash else "ARTIFACT_SCHEMA_BLOCKED",
        "historical_artifact": {"path": str(canonical_path.relative_to(ROOT)), "sha256_before": before_hash, "sha256_after": after_hash, "immutable": before_hash == after_hash},
        "identity_contract": {"path": str(contract_path.relative_to(ROOT)), "authority_sha256_matches": authority_matches, "dataset_fingerprint": contract.get("dataset_fingerprint"), "generation_id": contract.get("generation_id")},
        "legacy_compatibility": legacy,
        "v2_policy": "A v2 artifact may be emitted only by a controlled formal A2 evaluation that records every required trace stage and passes identity validation. The v1 authority is retained unchanged and cannot be retroactively promoted.",
    }


def render(result: dict[str, Any]) -> str:
    legacy = result["legacy_compatibility"]
    identity = result["identity_contract"]
    historical = result["historical_artifact"]
    missing = "\n".join(f"- `{field}`" for field in legacy["missing_v2_fields"])
    return f"""# Phase 14C-0 — Canonical A2 Artifact Schema v2

**Status:** `{result['status']}`

## Decision

The historical A2 artifact remains the immutable v1 authority. It is readable for historical metric reporting, but cannot prove a pipeline-stage divergence because it lacks the trace-complete v2 fields. Schema v2 is therefore defined for the next controlled canonical capture; it does not alter historical metrics or silently upgrade the v1 JSON.

## Historical compatibility

- Historical artifact: `{historical['path']}`
- SHA-256 before/after read-only audit: `{historical['sha256_before']}` / `{historical['sha256_after']}`
- Immutable: `{historical['immutable']}`
- Legacy readable: `{legacy['compatible']}`
- Trace complete: `{legacy['trace_complete']}`

## Identity gate

- Dataset fingerprint: `{identity['dataset_fingerprint']}`
- Generation: `{identity['generation_id']}`
- Historical authority hash matches its identity contract: `{identity['authority_sha256_matches']}`

## v2 required per-question trace

1. Query and query hash.
2. Raw LightRAG/BM25 retrieval candidates, local ranks, and raw scores.
3. Complete RRF fusion pool, ranks, scores, and contributor lineage.
4. Ordered rerank input plus deterministic candidate fingerprint.
5. Complete rerank output, scores, and output ranks.
6. Final Top5 and Top10 prefixes.
7. Runtime metadata including provider/model, latency, request status, and fallback flag.

The v2 validator rejects identity drift, missing rerank fingerprints, altered candidate identity, and final rankings which are not prefixes of the saved rerank output. Its offline replay delegates to the existing `recompute_trace_metrics` implementation, preserving Recall, MRR, Question Hit, and Complete semantics.

## Why v1 is not retroactively converted

The v1 artifact does not contain:

{missing}

Filling these fields from a later live capture would misrepresent a new external rerank execution as the historical canonical execution. The contract therefore blocks promotion instead of guessing or modifying the v1 artifact.

## Compatibility and replay

Legacy v1 remains readable and unchanged. A validated v2 artifact can independently recompute Recall@5/@10, MRR@5/@10, Question Hit@5/@10, and Complete@5/@10 entirely offline from its saved final rankings and frozen expected evidence.

## Scope boundary

This phase introduces only artifact observability and integrity contracts. It makes no retrieval, chunking, embedding, reranker, or evaluator-metric change.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, default=EVALUATION / "formal_development_effectiveness_2026-09-03.json")
    parser.add_argument("--contract", type=Path, default=EVALUATION / "a2_baseline_identity_contract.json")
    parser.add_argument("--output-json", type=Path, default=EVALUATION / "phase14c0_canonical_artifact_v2_audit.json")
    parser.add_argument("--output-md", type=Path, default=ROOT / "docs" / "phase-14c0-canonical-artifact-v2.md")
    args = parser.parse_args()
    result = audit(args.canonical, args.contract)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "immutable": result["historical_artifact"]["immutable"]}))


if __name__ == "__main__":
    main()
