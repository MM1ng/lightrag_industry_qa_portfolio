"""Read-only stage-by-stage localization for the D-V2-011 A2 trace drift."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation" / "retrieval_foundation"
QUESTION_ID = "D-V2-011"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _canonical_rows(canonical_question: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(canonical_question["variants"]["A2_lightrag_bm25_rrf_reranker"]["top_results"])


def _contribution_ranks(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(item["source"]): item.get("original_rank") for item in row.get("contributions", [])}


def localize(canonical_question: Mapping[str, Any], replay_trace: Mapping[str, Any]) -> dict[str, Any]:
    canonical_rows = _canonical_rows(canonical_question)
    canonical_top10 = [str(row["child_chunk_id"]) for row in canonical_rows]
    replay_top10 = list(replay_trace["final"]["top10_ids"])
    canonical_top5, replay_top5 = canonical_top10[:5], list(replay_trace["final"]["top5_ids"])
    fusion_by_id = {str(row["candidate_id"]): row for row in replay_trace["fusion_candidates"]}
    fusion_comparison = []
    for row in canonical_rows:
        candidate_id = str(row["child_chunk_id"])
        replay = fusion_by_id.get(candidate_id)
        fusion_comparison.append({
            "candidate_id": candidate_id,
            "canonical": {
                "fusion_rank": row.get("rank"),
                "fusion_score": row.get("rrf_score"),
                "source_ranks": _contribution_ranks(row),
            },
            "replay": replay,
            "matches": None if replay is None else {
                "fusion_rank": row.get("rank"), "fusion_score": row.get("rrf_score"), "source_ranks": _contribution_ranks(row)
            } == {"fusion_rank": replay.get("fusion_rank"), "fusion_score": replay.get("fusion_score"), "source_ranks": replay.get("source_ranks")},
        })
    final_diff = canonical_top10 != replay_top10
    canonical_has_complete_rerank = all(row.get("rerank_score") is not None for row in canonical_rows)
    observed_fusion_drift = any(row["matches"] is False for row in fusion_comparison)
    if observed_fusion_drift:
        classification, first_observable = "FUSION_DRIFT", "fusion_candidates"
    elif final_diff and not canonical_has_complete_rerank:
        classification, first_observable = "ARTIFACT_SCHEMA_MISMATCH", "final_ranking"
    elif final_diff:
        classification, first_observable = "RERANK_DRIFT", "rerank_output"
    else:
        classification, first_observable = "UNRESOLVED", "none"
    shared = [item for item in canonical_top10 if item in replay_top10]
    return {
        "question_id": QUESTION_ID,
        "query": {"canonical": canonical_question.get("question"), "replay": replay_trace.get("query"), "matches": canonical_question.get("question") == replay_trace.get("query")},
        "raw_retrieval": {"canonical": "unavailable: canonical artifact stores no raw candidate list", "replay_candidate_count": len(replay_trace["retrieval_candidates"]), "observed_final_candidate_source_ranks_match": all(row["matches"] for row in fusion_comparison)},
        "fusion": {"canonical": "partial: only final Top10 rows retain fusion metadata", "replay_candidate_count": len(replay_trace["fusion_candidates"]), "compared_final_candidates": fusion_comparison, "all_observed_metadata_matches": all(row["matches"] for row in fusion_comparison)},
        "rerank_input": {"canonical": "unavailable: no complete input pool or candidate fingerprint", "replay_candidate_count": len(replay_trace["rerank_candidates"]), "replay_candidate_fingerprint": fingerprint([str(row["candidate_id"]) for row in replay_trace["rerank_candidates"]])},
        "rerank": {
            "model": "qwen3-rerank",
            "request": {"canonical": "unavailable", "replay": "same A2 runtime path; 20 candidates"},
            "candidate_fingerprint": {"canonical": "unavailable", "replay": fingerprint([str(row["candidate_id"]) for row in replay_trace["rerank_candidates"]])},
            "score_difference": "unavailable: canonical A2 final rows do not retain rerank_score",
            "output_status": {"canonical": "partial: final Top10 only", "replay": "complete: 20 ranked candidates"},
        },
        "final": {"canonical_top5": canonical_top5, "canonical_top10": canonical_top10, "replay_top5": replay_top5, "replay_top10": replay_top10},
        "candidate_difference": {"added": [item for item in replay_top10 if item not in canonical_top10], "removed": [item for item in canonical_top10 if item not in replay_top10], "rank_changed": [{"candidate_id": item, "canonical_rank": canonical_top10.index(item) + 1, "replay_rank": replay_top10.index(item) + 1} for item in shared if canonical_top10.index(item) != replay_top10.index(item)]},
        "first_observable_divergence": first_observable,
        "classification": classification,
        "first_actual_divergence": "unavailable" if classification == "ARTIFACT_SCHEMA_MISMATCH" else first_observable,
    }


def render(result: Mapping[str, Any]) -> str:
    final, diff = result["final"], result["candidate_difference"]
    lines = [
        "# Phase 14B-2 — D-V2-011 Trace Drift Localization Audit",
        "",
        f"**Classification:** `{result['classification']}`",
        "",
        "## Query",
        "",
        f"- Match: `{result['query']['matches']}`",
        "",
        "## Canonical vs replay final ranking",
        "",
        f"- Canonical Top5: `{final['canonical_top5']}`",
        f"- Replay Top5: `{final['replay_top5']}`",
        f"- Canonical Top10: `{final['canonical_top10']}`",
        f"- Replay Top10: `{final['replay_top10']}`",
        f"- Added: `{diff['added']}`",
        f"- Removed: `{diff['removed']}`",
        f"- Rank changed: `{diff['rank_changed']}`",
        "",
        "## Stage localization",
        "",
        f"- Raw retrieval: {result['raw_retrieval']['canonical']}",
        f"- Fusion: `{result['fusion']['all_observed_metadata_matches']}` for all canonical final candidates observed in replay.",
        f"- Rerank input: {result['rerank_input']['canonical']}",
        f"- Rerank output: canonical score differences are `{result['rerank']['score_difference']}`.",
        f"- First observable divergence: `{result['first_observable_divergence']}`",
        f"- First actual divergence: `{result['first_actual_divergence']}`",
        "",
        "## Decision",
        "",
        "The capture is not suitable for root-cause attribution because the canonical artifact lacks the raw candidate pool, full fusion pool, rerank input fingerprint, and rerank scores. The observable final disagreement follows matching fusion metadata for every canonical final candidate, but that is insufficient to prove a reranker drift. This is a read-only schema-comparability finding; no retrieval optimization is authorized.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, default=EVALUATION / "formal_development_effectiveness_2026-09-03.json")
    parser.add_argument("--trace", type=Path, default=EVALUATION / "phase14b1_a2_trace_capture.json")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    canonical = read_json(args.canonical)
    question = next(row for row in canonical["per_question"] if row["id"] == QUESTION_ID)
    replay = read_json(args.trace)["question_traces"][QUESTION_ID]
    result = localize(question, replay)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render(result), encoding="utf-8")
    print(json.dumps({"classification": result["classification"], "first_observable": result["first_observable_divergence"]}))


if __name__ == "__main__":
    main()
