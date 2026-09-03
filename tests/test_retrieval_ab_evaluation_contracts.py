from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from industrial_rag.services.retrieval_ab_evaluation import (
    EvaluationBlocked,
    Variant,
    assert_development_only,
    audit_label_compatibility,
    build_variant_plan,
    map_expected_evidence,
)


def test_split_guard_requires_development_only_rows() -> None:
    with pytest.raises(ValueError, match="Development"):
        assert_development_only(
            [{"id": "D001", "split": "development"}, {"id": "V001", "split": "validation"}]
        )


def test_split_guard_rejects_missing_split_without_signed_manifest() -> None:
    with pytest.raises(ValueError, match="split"):
        assert_development_only([{"id": "S001"}])


def test_variant_plan_preserves_a0_baseline_contract() -> None:
    plan = build_variant_plan()
    assert plan[Variant.A0].sparse_enabled is False
    assert plan[Variant.A0].rrf_enabled is False
    assert plan[Variant.A0].reranker_enabled is False
    assert plan[Variant.A1].sparse_enabled is True
    assert plan[Variant.A1].rrf_enabled is True
    assert plan[Variant.A1].reranker_enabled is False
    assert plan[Variant.A2].sparse_enabled is True
    assert plan[Variant.A2].rrf_enabled is True
    assert plan[Variant.A2].reranker_enabled is True


def test_expected_labels_must_map_to_frozen_chunk_universe() -> None:
    cases = [{"id": "S001", "relevant_chunk_ids": ["gold-1"]}]
    mapping = {"gold-1": ["child-1"]}
    assert map_expected_evidence(cases, mapping, {"child-1"})[0]["relevant_chunk_ids"] == ["child-1"]
    with pytest.raises(EvaluationBlocked, match="unmapped"):
        map_expected_evidence(cases, {}, {"child-1"})


def test_label_audit_uses_source_location_not_retrieval_results() -> None:
    result = audit_label_compatibility(
        [{"id": "S001", "relevant_chunk_ids": ["old-1"]}],
        {"gold-1": ["old-1"]},
        {"old-1": {"document_name": "manual.pdf", "page_start": 2, "content": "evidence"}},
        [{"chunk_id": "new-1", "document_name": "manual.pdf", "page_start": 2, "page_end": 2, "content": "evidence"}],
    )
    assert result[0]["status"] == "EQUIVALENT"


def test_generation_contract_requires_light_rag_workspace(tmp_path: Path) -> None:
    from industrial_rag.parser_models import ChildChunk
    from industrial_rag.services.generation_artifacts import freeze_generation_child_chunks
    from industrial_rag.services.retrieval_ab_evaluation import FrozenGeneration

    child = ChildChunk(
        chunk_id="child-1",
        parent_chunk_id="parent-1",
        document_id="doc-1",
        document_name="manual.pdf",
        content="content",
        embedding_content="content",
    )
    document = SimpleNamespace(id="doc-1", version=1, file_hash="hash", original_file_name="manual.pdf")
    freeze_generation_child_chunks(tmp_path, generation_id="g-dev", document_children=[(document, child)])
    with pytest.raises(EvaluationBlocked, match="LightRAG workspace"):
        FrozenGeneration.load(tmp_path)


@pytest.mark.asyncio
async def test_runner_records_same_generation_and_reranker_fallback(tmp_path: Path) -> None:
    from industrial_rag.parser_models import ChildChunk
    from industrial_rag.services.generation_artifacts import freeze_generation_child_chunks
    from industrial_rag.services.lexical_retrieval import BM25Index
    from industrial_rag.services.retrieval_ab_evaluation import FrozenGeneration, run_ab_evaluation

    child = ChildChunk(
        chunk_id="child-1",
        parent_chunk_id="parent-1",
        document_id="doc-1",
        document_name="manual.pdf",
        content="2196-R 0.005",
        embedding_content="2196-R 0.005",
    )
    document = SimpleNamespace(id="doc-1", version=1, file_hash="hash", original_file_name="manual.pdf")
    freeze_generation_child_chunks(tmp_path, generation_id="g-dev", document_children=[(document, child)])
    (tmp_path / "industrial_rag_index.json").write_text("{}", encoding="utf-8")
    (tmp_path / "kv_store_text_chunks.json").write_text("{\"child-1\": {}}", encoding="utf-8")
    generation = FrozenGeneration.load(tmp_path)
    sparse = BM25Index.from_records([child.to_dict()])

    async def retrieve(_question: str, _top_k: int):
        return [{"child_chunk_id": "child-1", "score": 1.0}]

    report = await run_ab_evaluation(
        cases=[{"id": "S001", "split": "development", "question": "2196-R", "relevant_chunk_ids": ["child-1"]}],
        generation=generation,
        sparse_index=sparse,
        lightrag_retriever=retrieve,
        reranker_provider=None,
    )
    assert report["generation"]["child_manifest_hash"] == generation.child_manifest_hash
    assert report["question_ids"] == ["S001"]
    assert report["reranker"]["fallback_count"] == 1
    assert report["variant_configs"]["A0_lightrag"] == {
        "sparse_enabled": False,
        "rrf_enabled": False,
        "reranker_enabled": False,
    }
    assert report["per_question"][0]["delta_classifications"] == ["NO_MATERIAL_CHANGE"]
    assert report["trace_integrity"]["invalid_chunk_ids"] == 0
    assert report["final_status"] == "INCONCLUSIVE"
    assert report["downstream_qa_allowed"] is False


@pytest.mark.asyncio
async def test_dashscope_adapter_translates_frozen_candidates_to_runtime_contract() -> None:
    from industrial_rag.services.reranker_runtime_adapter import (
        DashScopeRuntimeAdapter,
    )

    class FakeProvider:
        model = "qwen3-rerank"

        async def rerank(self, query, candidates, top_n):
            assert query == "型号查询"
            assert [item["chunk_id"] for item in candidates] == ["child-1", "child-2"]
            assert [item["text"] for item in candidates] == ["2196-R", "2796"]
            return [
                type("Result", (), {"chunk_id": "child-2", "rerank_score": 0.9})(),
                type("Result", (), {"chunk_id": "child-1", "rerank_score": 0.2})(),
            ]

    adapter = DashScopeRuntimeAdapter(
        provider=FakeProvider(),
        chunk_records={
            "child-1": {"content": "2196-R"},
            "child-2": {"content": "2796"},
        },
    )
    result = await adapter(
        "型号查询",
        [
            {"child_chunk_id": "child-1", "rrf_score": 0.1},
            {"child_chunk_id": "child-2", "rrf_score": 0.2},
        ],
    )
    assert result == [
        ({"child_chunk_id": "child-2"}, 0.9),
        ({"child_chunk_id": "child-1"}, 0.2),
    ]


def test_ab_runner_direct_script_imports_evaluation_provider() -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_retrieval_foundation_dev_ab.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=Path(__file__).resolve().parents[1] / "tests",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--reranker-model" in result.stdout


def test_rerank_delta_classifies_recovery_and_regression() -> None:
    from industrial_rag.services.retrieval_ab_evaluation import classify_rerank_delta

    assert classify_rerank_delta(None, 5) == "RERANK_IMPROVEMENT"
    assert classify_rerank_delta(3, 1) == "RERANK_IMPROVEMENT"
    assert classify_rerank_delta(1, None) == "RERANK_REGRESSION"
    assert classify_rerank_delta(1, 4) == "RERANK_REGRESSION"
    assert classify_rerank_delta(2, 2) is None
