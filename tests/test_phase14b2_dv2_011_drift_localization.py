from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_phase14b2_dv2_011_drift_localization.py"
SPEC = importlib.util.spec_from_file_location("phase14b2_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_final_difference_with_missing_canonical_rerank_trace_is_schema_mismatch() -> None:
    canonical = {
        "question": "q",
        "variants": {"A2_lightrag_bm25_rrf_reranker": {"top_results": [
            {"child_chunk_id": "c1", "rank": 1, "rrf_score": 0.02, "contributions": [], "rerank_score": None},
        ]}},
    }
    replay = {
        "query": "q",
        "retrieval_candidates": [],
        "fusion_candidates": [{"candidate_id": "c1", "fusion_rank": 1, "fusion_score": 0.02, "source_ranks": {}}],
        "rerank_candidates": [{"candidate_id": "c2", "input_rank": 2, "output_rank": 1, "rerank_score": 0.9, "model": "qwen3-rerank", "status": "success"}],
        "final": {"top5_ids": ["c2"], "top10_ids": ["c2"]},
    }
    result = audit.localize(canonical, replay)
    assert result["classification"] == "ARTIFACT_SCHEMA_MISMATCH"
    assert result["first_observable_divergence"] == "final_ranking"
    assert result["candidate_difference"] == {"added": ["c2"], "removed": ["c1"], "rank_changed": []}
    assert result["rerank"]["candidate_fingerprint"]["canonical"] == "unavailable"


def test_same_final_order_has_no_observable_divergence() -> None:
    canonical = {"question": "q", "variants": {"A2_lightrag_bm25_rrf_reranker": {"top_results": [{"child_chunk_id": "c1", "rank": 1, "rrf_score": 0.02, "contributions": [], "rerank_score": None}]}}}
    replay = {"query": "q", "retrieval_candidates": [], "fusion_candidates": [], "rerank_candidates": [], "final": {"top5_ids": ["c1"], "top10_ids": ["c1"]}}
    assert audit.localize(canonical, replay)["classification"] == "UNRESOLVED"
