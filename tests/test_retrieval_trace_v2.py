from industrial_rag.retrieval_trace import RetrievalTraceItem


def test_trace_item_exposes_sparse_rrf_and_selection_explanation_fields():
    item = RetrievalTraceItem(
        initial_rank=2,
        initial_score=0.4,
        retrieval_source="lightrag",
        document_id="doc-1",
        document_name="manual.pdf",
        page_number=3,
        chunk_id="c1",
        sparse_rank=1,
        sparse_score=8.5,
        rrf_rank=1,
        rrf_score=0.032,
        selected=True,
        rejected_reason=None,
    )

    payload = item.to_payload()

    assert payload["sparse_rank"] == 1
    assert payload["sparse_score"] == 8.5
    assert payload["rrf_rank"] == 1
    assert payload["rrf_score"] == 0.032
    assert payload["selected"] is True
    assert payload["rejected_reason"] is None
