from scripts.phase14a_parser_ab import (
    canonical_parser_record,
    classify_evidence_match,
    exact_numeric_tokens,
    parser_artifact_fingerprint,
)


def test_normalized_record_schema_is_fixed():
    record = canonical_parser_record(
        document_id="doc-a", page_no=1, block_id="b", block_type="paragraph", text="x",
        bbox=None, reading_order=1, section_path=[], table_content=None,
        parser_name="test", parser_version="1",
    )
    assert list(record) == [
        "document_id", "page_no", "block_id", "block_type", "text", "bbox",
        "reading_order", "section_path", "table_content", "parser_name", "parser_version",
    ]


def test_numeric_exactness_is_not_repaired_or_rounded():
    assert "175" in exact_numeric_tokens("最高轴承温度 175°F")
    assert classify_evidence_match("最高温度 175°F", "最高温度 170°F", page_overlap=True)["numeric_exact"] is False


def test_artifact_fingerprint_is_deterministic_and_order_sensitive():
    records = [{"block_id": "a", "text": "x"}, {"block_id": "b", "text": "y"}]
    assert parser_artifact_fingerprint(records) == parser_artifact_fingerprint(records)
    assert parser_artifact_fingerprint(records) != parser_artifact_fingerprint(list(reversed(records)))
