"""Capture an independent, strict trace from the real canonical A2 runner."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_formal_retrieval_effectiveness import _run, preflight  # noqa: E402

EVALUATION = ROOT / "evaluation" / "retrieval_foundation"
CANONICAL_A2 = EVALUATION / "formal_development_effectiveness_2026-09-03.json"
IDENTITY_CONTRACT = EVALUATION / "a2_baseline_identity_contract.json"
MISSING_SOURCE = EVALUATION / "phase13b_multi_query_ablation_2026-09-03.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def assert_independent_output(output: Path, canonical: Path) -> None:
    if output.resolve() == canonical.resolve():
        raise ValueError("trace capture output must not overwrite the canonical artifact")


class TraceAccumulator:
    """Transforms observer events into a portable, replayable trace artifact."""

    def __init__(self, chunk_text_by_id: Mapping[str, str]) -> None:
        self._chunk_text_by_id = dict(chunk_text_by_id)
        self.traces: dict[str, dict[str, Any]] = {}

    def observe(self, event: Mapping[str, Any]) -> None:
        question_id = str(event["question_id"])
        if event["event"] == "pre_rerank":
            self.traces[question_id] = {
                "question_id": question_id,
                "query": str(event["question"]),
                "query_hash": sha256_text(str(event["question"])),
                "actual_call_path": [
                    "run_formal_retrieval_effectiveness._run",
                    "retrieval_ab_evaluation.run_ab_evaluation",
                    "LightRAG retriever + BM25Index.search",
                    "reciprocal_rank_fusion",
                    "RerankerRuntime.rerank",
                ],
                "retrieval_candidates": self._retrieval_rows(event),
                "fusion_candidates": self._fusion_rows(event),
                "rerank_candidates": self._unavailable_rerank_rows(event),
                "final": {"top5_ids": [], "top10_ids": []},
                "stage_failures": [],
            }
            return
        if event["event"] != "post_rerank" or question_id not in self.traces:
            raise ValueError(f"unexpected trace observer event: {event.get('event')}")
        trace = self.traces[question_id]
        trace["rerank_candidates"] = self._rerank_rows(event)
        final_ids = [str(row["child_chunk_id"]) for row in event["final_candidates"]]
        trace["final"] = {"top5_ids": final_ids[:5], "top10_ids": final_ids[:10]}
        if event["rerank_status"] != "success":
            trace["stage_failures"].append(
                {"stage": "rerank", "status": "unavailable", "reason": event["rerank_failure_reason"]}
            )

    def mark_rerank_unavailable(self, reason: str) -> None:
        if not self.traces:
            return
        trace = list(self.traces.values())[-1]
        for candidate in trace["rerank_candidates"]:
            candidate["status"] = "unavailable"
        trace["stage_failures"].append({"stage": "rerank", "status": "unavailable", "reason": reason})

    def _retrieval_rows(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source, source_rows in (("dense", event["dense_candidates"]), ("sparse", event["sparse_candidates"])):
            for row in source_rows:
                child_id = str(row["child_chunk_id"])
                rows.append({
                    "candidate_id": child_id,
                    "chunk_id": child_id,
                    "source": source,
                    "rank": row.get("rank"),
                    "score": row.get("score"),
                    "text_hash": sha256_text(self._chunk_text_by_id[child_id]),
                })
        return rows

    @staticmethod
    def _fusion_rows(event: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": str(row["child_chunk_id"]),
                "fusion_rank": row.get("rank"),
                "fusion_score": row.get("rrf_score"),
                "source_ranks": {
                    str(item["source"]): item.get("original_rank")
                    for item in row.get("contributions", [])
                },
            }
            for row in event["fusion_candidates"]
        ]

    @staticmethod
    def _unavailable_rerank_rows(event: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": str(row["child_chunk_id"]),
                "input_rank": row.get("rank"),
                "output_rank": None,
                "rerank_score": None,
                "model": "qwen3-rerank",
                "status": "unavailable",
            }
            for row in event["fusion_candidates"]
        ]

    @staticmethod
    def _rerank_rows(event: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": str(row["child_chunk_id"]),
                "input_rank": row.get("rank"),
                "output_rank": row.get("rerank_rank"),
                "rerank_score": row.get("rerank_score"),
                "model": "qwen3-rerank",
                "status": "success" if event["rerank_status"] == "success" else "unavailable",
            }
            for row in event["rerank_candidates"]
        ]


def final_alignment(
    traces: Mapping[str, Mapping[str, Any]],
    canonical: Mapping[str, list[str]],
    *,
    final_key: str = "top10_ids",
) -> list[str]:
    return [
        question_id
        for question_id, expected_ids in canonical.items()
        if list(traces.get(question_id, {}).get("final", {}).get(final_key, [])) != expected_ids
    ]


def classify_missing_evidence(row: Mapping[str, Any]) -> str:
    raw, fusion, rerank = row.get("raw_hit"), row.get("fusion_hit"), row.get("rerank_hit")
    if "unavailable" in {raw, fusion, rerank}:
        return "UNRESOLVED"
    if raw is False:
        return "CANDIDATE_RECALL_FAILURE"
    if fusion is False:
        return "FUSION_LOSS"
    if rerank is False:
        return "RERANKER_LOSS"
    if rerank is True and row.get("final_top10") is False:
        return "TOPK_SELECTION_LOSS"
    return "UNRESOLVED"


def _chunk_text_by_id(generation_path: Path) -> dict[str, str]:
    return {
        str(row["chunk_id"]): str(row["content"])
        for line in (generation_path / "retrieval" / "child_chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line
        for row in [json.loads(line)]
    }


def _canonical_rankings(canonical: dict[str, Any], limit: int) -> dict[str, list[str]]:
    return {
        row["id"]: [item["child_chunk_id"] for item in row["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"][:limit]]
        for row in canonical["per_question"]
    }


def _historical_missing() -> dict[str, list[str]]:
    source = read_json(MISSING_SOURCE)
    return {
        row["question_id"]: list(row["a2_missing_gold"])
        for row in source["phase13a_six_miss_recovery"]["questions"]
    }


def missing_evidence_analysis(
    traces: Mapping[str, Mapping[str, Any]], missing: Mapping[str, list[str]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for question_id, evidence_ids in missing.items():
        trace = traces.get(question_id)
        for evidence_id in evidence_ids:
            if trace is None:
                row = {"question_id": question_id, "gold_evidence_id": evidence_id, "raw_hit": "unavailable", "raw_rank": None, "fusion_hit": "unavailable", "fusion_rank": None, "rerank_hit": "unavailable", "rerank_rank": None, "final_top10": "unavailable", "final_top5": "unavailable"}
            else:
                raw = [item for item in trace["retrieval_candidates"] if item["candidate_id"] == evidence_id]
                fusion = next((item for item in trace["fusion_candidates"] if item["candidate_id"] == evidence_id), None)
                rerank = next((item for item in trace["rerank_candidates"] if item["candidate_id"] == evidence_id), None)
                rerank_status = rerank["status"] if rerank else "success"
                row = {
                    "question_id": question_id,
                    "gold_evidence_id": evidence_id,
                    "raw_hit": bool(raw),
                    "raw_rank": min((item["rank"] for item in raw), default=None),
                    "fusion_hit": fusion is not None,
                    "fusion_rank": fusion["fusion_rank"] if fusion else None,
                    "rerank_hit": bool(rerank) if rerank_status == "success" else "unavailable",
                    "rerank_rank": rerank["output_rank"] if rerank_status == "success" and rerank else None,
                    "final_top10": evidence_id in trace["final"]["top10_ids"],
                    "final_top5": evidence_id in trace["final"]["top5_ids"],
                }
            row["primary_cause"] = classify_missing_evidence(row)
            rows.append(row)
    return rows


def verify_identity(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], Any, dict[str, Any]]:
    cases, generation, preflight_report = preflight(args.dataset, args.manifest, args.mapping, args.generation)
    contract, canonical = read_json(args.contract), read_json(args.canonical)
    checks = {
        "authority_sha256": hashlib.sha256(args.canonical.read_bytes()).hexdigest() == contract["authority_sha256"],
        "dataset_fingerprint": preflight_report["dataset_fingerprint"] == contract["dataset_fingerprint"] == canonical["dataset_identity"]["fingerprint"],
        "generation": generation.generation_id == contract["generation_id"] == canonical["generation_identity"]["generation_id"],
        "question_ids": [case["question_id"] for case in cases] == canonical["question_ids"] == preflight_report["manifest"]["question_ids"],
        "document_fingerprint": generation.corpus_fingerprint == contract["generation_identity"]["corpus_fingerprint"],
        "chunk_fingerprint": generation.child_manifest_hash == contract["generation_identity"]["child_manifest_hash"],
        "index_fingerprint": preflight_report["manifest"]["lexical_index_fingerprint"] == contract["generation_identity"]["lexical_index_fingerprint"],
        "gold_mapping": preflight_report["checks"]["evidence_mapping_complete"] and preflight_report["checks"]["mapping_children_exist"],
    }
    return {"identity_match": all(checks.values()), "checks": checks}, cases, generation, canonical


def render(report: Mapping[str, Any]) -> str:
    lines = ["# Phase 14B-1 — Canonical A2 Retrieval Trace Capture", "", f"**Status:** `{report['status']}`", "", "## 1. Executive Summary", "", report["summary"], "", "## 2. Identity Verification", "", f"- Identity match: `{report['identity']['identity_match']}`", *[f"- {key}: `{value}`" for key, value in report["identity"]["checks"].items()], ""]
    if report["status"] == "A2_TRACE_CAPTURE_READY":
        funnel = report["missing_evidence_audit"]["funnel"]
        lines += ["## 3. Retrieval Funnel", "", "| Stage | Evidence / 21 |", "|---|---:|"]
        for key in ("raw_retrieval", "fusion", "rerank", "final_top10", "final_top5"):
            lines.append(f"| {key} | {funnel[key]}/21 |")
        lines += ["", "## 4. Missing Evidence Analysis", ""]
        for question_id, item in report["missing_evidence_audit"]["questions"].items():
            lines.append(f"- `{question_id}`: {item}")
        lines += ["", "## 5. Root Cause Distribution", ""]
        lines += [f"- `{cause}`: {count}" for cause, count in report["missing_evidence_audit"]["root_causes"].items()]
    else:
        lines += ["## 3. Alignment Gate", "", f"- Top5 mismatches: `{report['alignment']['top5_mismatches']}`", f"- Top10 mismatches: `{report['alignment']['top10_mismatches']}`", f"- Runtime error: `{report.get('runtime_error')}`", "", "Root-cause analysis was not performed because the final-ranking alignment gate did not pass."]
    return "\n".join(lines) + "\n"


async def capture(args: argparse.Namespace) -> dict[str, Any]:
    identity, _cases, generation, canonical = verify_identity(args)
    if not identity["identity_match"]:
        return {"status": "BLOCKED_IDENTITY", "identity": identity, "summary": "Frozen A2 identity contract mismatch; no pipeline was run.", "alignment": {"top5_mismatches": [], "top10_mismatches": []}, "question_traces": {}}
    accumulator = TraceAccumulator(_chunk_text_by_id(generation.workspace))
    runtime_error: str | None = None
    try:
        await _run(args, trace_observer=accumulator.observe)
    except Exception as error:  # strict runtime errors are recorded, never replaced.
        runtime_error = f"{type(error).__name__}:{error}"
        accumulator.mark_rerank_unavailable(runtime_error)
    top5_mismatches = final_alignment(
        accumulator.traces, _canonical_rankings(canonical, 5), final_key="top5_ids"
    )
    top10_mismatches = final_alignment(accumulator.traces, _canonical_rankings(canonical, 10))
    alignment = {"top5_mismatches": top5_mismatches, "top10_mismatches": top10_mismatches}
    if runtime_error or top5_mismatches or top10_mismatches or len(accumulator.traces) != 24:
        return {"status": "TRACE_CAPTURE_BLOCKED", "identity": identity, "runtime_error": runtime_error, "alignment": alignment, "summary": "Capture used the real A2 pipeline but did not satisfy the per-question final Top5/Top10 alignment gate. No root-cause analysis was performed.", "question_traces": accumulator.traces, "canonical_artifact": str(args.canonical), "canonical_metrics_overwritten": False}
    evidence = missing_evidence_analysis(accumulator.traces, _historical_missing())
    causes = Counter(row["primary_cause"] for row in evidence)
    audit = {
        "evidence": evidence,
        "funnel": {
            "raw_retrieval": sum(row["raw_hit"] is True for row in evidence),
            "fusion": sum(row["fusion_hit"] is True for row in evidence),
            "rerank": sum(row["rerank_hit"] is True for row in evidence),
            "final_top10": sum(row["final_top10"] is True for row in evidence),
            "final_top5": sum(row["final_top5"] is True for row in evidence),
        },
        "questions": {question_id: [row for row in evidence if row["question_id"] == question_id] for question_id in _historical_missing()},
        "root_causes": {cause: causes.get(cause, 0) for cause in ("CANDIDATE_RECALL_FAILURE", "FUSION_LOSS", "RERANKER_LOSS", "TOPK_SELECTION_LOSS", "UNRESOLVED")},
    }
    return {"status": "A2_TRACE_CAPTURE_READY", "identity": identity, "alignment": alignment, "summary": "Independent capture reused the formal A2 pipeline, preserved canonical metrics as read-only authority, and aligned every final Top5/Top10 ranking.", "question_traces": accumulator.traces, "missing_evidence_audit": audit, "canonical_artifact": str(args.canonical), "canonical_metrics_overwritten": False}


def replay(captured: Mapping[str, Any], canonical: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the alignment gate from a saved capture without provider calls."""
    report = dict(captured)
    traces = report["question_traces"]
    alignment = {
        "top5_mismatches": final_alignment(traces, _canonical_rankings(canonical, 5), final_key="top5_ids"),
        "top10_mismatches": final_alignment(traces, _canonical_rankings(canonical, 10)),
    }
    report["alignment"] = alignment
    if report.get("runtime_error") or alignment["top5_mismatches"] or alignment["top10_mismatches"]:
        report["status"] = "TRACE_CAPTURE_BLOCKED"
        report["summary"] = "Replayed capture did not satisfy the per-question final Top5/Top10 alignment gate. No root-cause analysis was performed."
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, default=EVALUATION / "dev_generation_v2")
    parser.add_argument("--dataset", type=Path, default=EVALUATION / "retrieval_foundation_dev_v2.jsonl")
    parser.add_argument("--manifest", type=Path, default=EVALUATION / "retrieval_foundation_dev_v2_manifest.json")
    parser.add_argument("--mapping", type=Path, default=EVALUATION / "retrieval_foundation_dev_v2_evidence_mapping.json")
    parser.add_argument("--canonical", type=Path, default=CANONICAL_A2)
    parser.add_argument("--contract", type=Path, default=IDENTITY_CONTRACT)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--replay-input", type=Path)
    args = parser.parse_args()
    assert_independent_output(args.output_json, args.canonical)
    report = (
        replay(read_json(args.replay_input), read_json(args.canonical))
        if args.replay_input
        else asyncio.run(capture(args))
    )
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "trace_questions": len(report["question_traces"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
