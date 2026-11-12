from __future__ import annotations

from industrial_rag.services.lexical_retrieval import BM25Index, tokenize


def test_tokenizer_preserves_industrial_identifiers_decimals_and_chinese_terms() -> None:
    """Punctuation must not destroy exact manual identifiers or Chinese terminology."""
    tokens = tokenize(
        "2196-R、2796、370C、408A、NPSH、ANSI B15.1、ISO VG 68、0.005；离心泵机械密封"
    )

    assert tokens[:10] == (
        "2196-R",
        "2796",
        "370C",
        "408A",
        "NPSH",
        "ANSI",
        "B15.1",
        "ISO",
        "VG",
        "68",
    )
    assert "0.005" in tokens
    assert "离心泵机械密封" in tokens
    assert "机械密封" in tokens


def test_bm25_search_returns_ranked_canonical_child_chunk_ids() -> None:
    """Replacing a canonical child ID or dropping an exact token must change the result."""
    index = BM25Index.from_records(
        [
            {"chunk_id": "child-bearing", "content": "2196-R 泵采用机械密封，间隙为 0.005"},
            {"chunk_id": "child-other", "content": "2796 泵的维护说明"},
        ]
    )

    results = index.search("2196-R 机械密封 0.005", limit=2)

    assert [(item.child_chunk_id, item.rank) for item in results] == [("child-bearing", 1)]
    assert results[0].score > 0
