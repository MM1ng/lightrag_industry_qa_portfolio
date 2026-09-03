from __future__ import annotations

import json

import pytest
from scripts.run_phase13b_multi_query_ablation import (
    PHASE13A_MULTI_MISS_IDS,
    parse_variant_response,
    select_phase13a_multi_misses,
    validate_experiment_identity,
)


def test_parse_variant_response_accepts_bounded_variant_list() -> None:
    result = parse_variant_response('{"queries":["参数限制", "安装前置", "故障原因"]}')
    assert [item.query for item in result] == ["参数限制", "安装前置", "故障原因"]
    assert [item.variant_id for item in result] == ["variant_1", "variant_2", "variant_3"]


def test_parse_variant_response_rejects_more_than_three_variants() -> None:
    with pytest.raises(ValueError, match="2 to 3"):
        parse_variant_response(json.dumps({"queries": ["a"]}))


def test_select_phase13a_multi_misses_uses_saved_a2_flags() -> None:
    report = {
        "per_question": [
            {"id": "S014", "variants": {"A2_lightrag_bm25_rrf_reranker": {"complete_coverage_at_10": False}}},
            {"id": "D-V2-001", "variants": {"A2_lightrag_bm25_rrf_reranker": {"complete_coverage_at_10": True}}},
        ]
    }
    assert select_phase13a_multi_misses(report) == ["S014"]
    assert set(PHASE13A_MULTI_MISS_IDS) == {"S014", "S015", "S006", "S003", "S016", "S011"}


def test_validate_identity_rejects_mismatch() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        validate_experiment_identity(
            {"fingerprint": "wrong", "question_count": 24},
            expected_fingerprint="expected",
            expected_count=24,
        )
