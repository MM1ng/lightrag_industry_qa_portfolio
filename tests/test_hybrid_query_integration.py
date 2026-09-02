from industrial_rag.lightrag_service import _fused_evidence_payload
from industrial_rag.runtime_chunk_hydration import ChunkRegistry
from industrial_rag.services.rrf_fusion import reciprocal_rank_fusion


def _registry() -> ChunkRegistry:
    return ChunkRegistry.from_records(
        [
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "document_name": "manual.pdf",
                "page_start": 4,
                "content": "型号 2196-R 的 NPSH 参数为 0.005。",
                "section_path": ["参数"],
            },
            {
                "chunk_id": "c2",
                "document_id": "d1",
                "document_name": "manual.pdf",
                "page_start": 5,
                "content": "ANSI B15.1 安全要求。",
                "section_path": ["安全"],
            },
        ],
        source="generation/retrieval/child_chunks.jsonl",
    )


def test_fused_payload_preserves_duplicate_identity_and_source_provenance():
    fused = reciprocal_rank_fusion(
        {
            "lightrag": [{"child_chunk_id": "c1", "score": 0.91}, {"child_chunk_id": "c2", "score": 0.4}],
            "sparse": [{"child_chunk_id": "c1", "score": 3.2}],
        },
        k=60,
    )

    payload = _fused_evidence_payload(fused, _registry())
    rows = payload["data"]["chunks"]

    assert [row["chunk_id"] for row in rows] == ["c1", "c2"]
    assert rows[0]["retrieval_source"] == "rrf"
    assert rows[0]["dense_rank"] == 1
    assert rows[0]["sparse_rank"] == 1
    assert rows[0]["rrf_rank"] == 1
    assert "manual.pdf" in rows[0]["file_path"]


def test_fused_payload_skips_unknown_ids_instead_of_rehydrating_mutable_sources():
    fused = reciprocal_rank_fusion({"sparse": [{"child_chunk_id": "missing", "score": 1.0}]})
    assert _fused_evidence_payload(fused, _registry())["data"]["chunks"] == []
