"""Audit Development labels against a newly built Frozen Generation V2."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.generation_artifacts import generation_artifact_evidence
from industrial_rag.services.retrieval_ab_evaluation import (
    EvaluationBlocked,
    FrozenGeneration,
    audit_label_compatibility,
    load_development_cases,
)

def _mapping(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["gold_chunk_id"]): [str(value) for value in item.get("mapped_child_ids", [])]
        for item in raw.get("entries", [])
        if item.get("mapped")
    }


def _historical_chunks() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    root = ROOT / "evaluation/experiments/parser_backend/P0"
    for path in root.glob("*/child_chunks.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                result[str(row["chunk_id"])] = row
    return result


def audit(generation_path: Path, dataset: Path, dataset_manifest: Path, mapping_path: Path) -> dict[str, object]:
    generation = FrozenGeneration.load(generation_path)
    evidence = generation_artifact_evidence(generation.workspace)
    cases = load_development_cases(dataset, dataset_manifest)
    mapping = _mapping(mapping_path)
    expanded: list[dict[str, object]] = []
    for case in cases:
        item = dict(case)
        historical_ids: list[str] = []
        for gold_id in case.get("relevant_chunk_ids", ()):
            historical_ids.extend(mapping.get(str(gold_id), ()))
        if not historical_ids:
            raise EvaluationBlocked(f"missing historical mapping for {case['id']}")
        item["relevant_chunk_ids"] = historical_ids
        expanded.append(item)
    audits = audit_label_compatibility(expanded, mapping, _historical_chunks(), evidence.records)
    statuses = {str(item["status"]) for item in audits}
    return {
        "status": "READY_FOR_AB" if statuses <= {"EXACT", "EQUIVALENT"} else "BLOCKED_LABEL_MAPPING",
        "generation_id": generation.generation_id,
        "child_manifest_hash": generation.child_manifest_hash,
        "question_ids": [str(case["id"]) for case in cases],
        "label_audits": list(audits),
        "validation_or_holdout_accessed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_cases.jsonl")
    parser.add_argument("--dataset-manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/development_dataset_manifest.json")
    parser.add_argument("--evidence-mapping", type=Path, default=ROOT / "evaluation/experiments/parser_backend/fixed_model/comparison/evidence_mapping_p0.json")
    args = parser.parse_args()
    try:
        report = audit(args.generation, args.dataset, args.dataset_manifest, args.evidence_mapping)
    except (EvaluationBlocked, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Development Label Compatibility Audit", "", f"**Status:** `{report['status']}`", "", "| Question | Status | Confidence | Reason |", "|---|---|---:|---|"]
    lines.extend(f"| {item['question_id']} | {item['status']} | {item['confidence']:.2f} | {item['reason']} |" for item in report["label_audits"])
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
