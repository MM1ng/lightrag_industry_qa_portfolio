import copy
import hashlib
import json
from pathlib import Path

import pytest
from scripts.phase14a_parser_ab import (
    DATASET,
    GENERATION,
    ROOT,
    canonical_parser_record,
    classify_evidence_match,
    compare_evidence,
    evaluate,
    exact_numeric_tokens,
    load,
    normalize_text,
    parser_artifact_fingerprint,
    resolve_manifest_sources,
    table_cells,
    validate_records,
    verify_pdf,
)


def _source_search_roots() -> list[Path]:
    """Directories searched for the protected source manuals.

    The project tree never contains the PDFs; they live wherever the operator
    keeps the demo corpus. ``IRA_SOURCE_MANUALS_ROOT`` points at it explicitly,
    and ``IRA_KB_DATA_ROOT`` is honoured because the upload pipeline stores the
    manuals under that root.
    """
    import os

    roots = [ROOT]
    for env_name in ("IRA_SOURCE_MANUALS_ROOT", "IRA_KB_DATA_ROOT"):
        value = os.environ.get(env_name, "").strip()
        if value:
            roots.append(Path(value))
    return roots


def _protected_source_manuals_present() -> bool:
    """Return whether every manifest-referenced source manual can be located.

    The original PDFs are protected assets: only ``data/manuals/README.md`` is
    distributed. The manifest records absolute paths from the machine that built
    the generation, so those paths are checked first and then resolved by content
    hash. The tests execute for real whenever the PDFs are reachable, instead of
    silently skipping because of a stale path.
    """
    try:
        sources = load(GENERATION / "source_manifest.json")
    except (OSError, ValueError, KeyError):
        return False
    if not sources:
        return False
    resolved = resolve_manifest_sources(sources, search_roots=_source_search_roots())
    return len(resolved) == len(sources)


requires_source_manuals = pytest.mark.skipif(
    not _protected_source_manuals_present(),
    reason="protected source manuals are not distributed",
)

# The development dataset is excluded from the public repository (see
# .gitignore: evaluation/retrieval_foundation/*), so tests that read it must
# skip instead of failing when it is absent.
requires_development_dataset = pytest.mark.skipif(
    not DATASET.is_file(),
    reason="development dataset is not distributed",
)


def record(text, page=1, doc="d"):
    return canonical_parser_record(document_id=doc, page_no=page, block_id=f"b{page}",
                                   text=text, reading_order=page, parser_name="fixture")


def case():
    return {"question_id": "q", "source_document_id": "d", "question_type": "parameter",
            "evidence_pattern": "single_evidence", "expected_child_chunk_ids": ["e"],
            "evidence": [{"child_chunk_id": "e", "text": "最大间隙≤0.1 mm", "page_start": 1,
                          "page_end": 2, "section_path": []}]}


def test_schema_and_normalization_do_not_invent_metadata():
    r = record("x")
    assert r["bbox"] is None and r["section_path"] is None and r["parser_version"] is None
    assert normalize_text("a\r\n  b") == "a\n  b"
    assert normalize_text("≤0.1 mm") != normalize_text("0.1 mm")


@pytest.mark.parametrize("wrong", ["0.1 mm", "≤0.1 cm", "≤0.11 mm", "≥0.1 mm"])
def test_numeric_expression_exactness(wrong):
    assert not classify_evidence_match("≤0.1 mm", wrong, page_overlap=True)["numeric_exact"]


def test_numeric_multiplicity_range_temperature_and_units():
    assert exact_numeric_tokens("≤0.1 mm; ≤0.1 mm") == ["≤0.1mm", "≤0.1mm"]
    assert exact_numeric_tokens("-60°F 至 350°F；±0.2 MPa；10%；20 min") == [
        "-60°F至350°F", "±0.2MPa", "10%", "20min"]
    assert not classify_evidence_match("1 mm；1 mm", "1 mm", page_overlap=True)["numeric_exact"]


def test_adjacent_numeric_cells_never_collapse_to_new_number():
    assert exact_numeric_tokens("100\t1\t泵体") == ["100", "1"]
    assert exact_numeric_tokens("175° F") == ["175°F"]
    assert not classify_evidence_match("100\t1\t泵体", "1001\t泵体", page_overlap=True)["numeric_exact"]


def test_unrelated_number_cannot_supply_aligned_evidence():
    r = evaluate([case()], [record("最小间隙≤0.1 mm。最大间隙0.2 mm")], "fixture")[0]
    assert r["status"] != "FULL"
    assert not r["numeric_exact"]


def test_denominator_cross_document_and_inclusive_page_range():
    c = case()
    assert evaluate([c], [], "fixture")[0]["status"] == "MISSING"
    assert evaluate([c], [record(c["evidence"][0]["text"], doc="other")], "fixture")[0]["status"] == "MISSING"
    assert evaluate([c], [record(c["evidence"][0]["text"], page=3)], "fixture")[0]["status"] == "MISSING"
    assert evaluate([c], [record(c["evidence"][0]["text"], page=2)], "fixture")[0]["status"] == "FULL"
    assert len(evaluate([c], [], "fixture")) == 1


@requires_development_dataset
def test_frozen_denominators_and_no_gold_mutation():
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]
    before = copy.deepcopy(cases)
    rows = evaluate(cases, [], "fixture")
    assert len(rows) == 50
    assert len({r["gold_evidence_id"] for r in rows}) == 45
    assert len({r["question_id"] for r in rows if "multi" in r["groups"]}) == 8
    assert all(r["status"] == "MISSING" for r in rows)
    assert cases == before


def test_reordered_text_not_full_and_mere_shared_characters_not_full():
    assert classify_evidence_match("先断电，再拆卸。", "先拆卸，再断电。", page_overlap=True)["status"] != "FULL"
    assert classify_evidence_match("开始注油", "油开始注", page_overlap=True)["status"] != "FULL"


@pytest.mark.parametrize("a,b,expected", [
    ("FULL", "PARTIAL", "PYMUPDF_BETTER"), ("PARTIAL", "FULL", "MINERU_BETTER"),
    ("FULL", "FULL", "EQUIVALENT"), ("MISSING", "MISSING", "BOTH_BAD"),
])
def test_comparison(a, b, expected):
    assert compare_evidence({"status": a, "numeric_exact": None},
                            {"status": b, "numeric_exact": None})[0] == expected


def test_table_cells_retain_rowspan_colspan_and_html_entities():
    rows = table_cells('<table><tr><td rowspan="2">A</td><td colspan="2">&lt;0.1</td></tr>'
                       '<tr><td>B</td><td>C</td></tr></table>')
    assert rows[0][0] == {"text": "A", "rowspan": 2, "colspan": 1}
    assert rows[0][1]["text"] == "<0.1" and rows[0][1]["colspan"] == 2


def test_pdf_hash_fails_closed(tmp_path):
    path = tmp_path / "test.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="IDENTITY"):
        verify_pdf(path, hashlib.sha256(b"different").hexdigest(), 1)


def test_page_identity_and_duplicate_block_fail_closed():
    with pytest.raises(ValueError, match="page"):
        validate_records([record("x", 3)], {"d": 2})
    with pytest.raises(ValueError, match="block"):
        validate_records([record("x"), record("x")], {"d": 2})


def test_fingerprint_stable_order_sensitive():
    rows = [record("a", 1), record("b", 2)]
    assert parser_artifact_fingerprint(rows) == parser_artifact_fingerprint(copy.deepcopy(rows))
    assert parser_artifact_fingerprint(rows) != parser_artifact_fingerprint(rows[::-1])


@requires_source_manuals
def test_identity_roundtrips_without_type_drift():
    from scripts.phase14a_parser_ab import identity

    info, _, _, _ = identity()
    assert json.loads(json.dumps(info)) == info


def test_source_table_contract_catches_span_loss():
    from scripts.phase14a_parser_report import normalized_cells

    correct = [[{"text": "heading", "rowspan": 1, "colspan": 2}]]
    broken = [[{"text": "heading", "rowspan": 1, "colspan": 1},
               {"text": "", "rowspan": 1, "colspan": 1}]]
    assert normalized_cells(correct) != normalized_cells(broken)


def test_order_check_keeps_missing_step_in_denominator():
    from scripts.phase14a_parser_report import ordered_check

    check = ordered_check("1.断电 3.排空", ["1.断电", "2.冷却", "3.排空"])
    assert check["passed"] is False
    assert len(check["anchor_offsets"]) == 3 and check["anchor_offsets"][1] is None


@requires_source_manuals
def test_saved_artifact_full_offline_replay_and_same_report_semantics():
    from scripts.phase14a_parser_ab import ARTIFACT, compute, identity, load
    from scripts.phase14a_parser_report import gate, render, structure_checks

    artifact = load(ARTIFACT)
    info, cases, _, missing = identity()
    assert info == artifact["identity"]
    result = compute(cases, artifact["normalized_records"], missing)
    assert result == artifact["results"]
    structure = structure_checks(artifact["normalized_records"], artifact["structure_reference"])
    assert structure == artifact["structure_results"]
    assert gate(result, structure) == artifact["promotion_gate"]
    assert len(result["evidence_diff"]) == 50
    assert all(x["total"] == 21 for x in result["historical21"].values())
    assert all(x["table_total"] == 3 for x in structure.values())
    for name, scores in result["overall"].items():
        assert f"overall/{name} | {scores['full']}/{scores['total']}" in render(artifact)


def test_valid_pdf_same_bytes_page_count_contract(tmp_path):
    import pymupdf

    path = tmp_path / "one.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    verify_pdf(path, digest, 1)
    with pytest.raises(ValueError, match="page count"):
        verify_pdf(path, digest, 2)


def test_fractional_inch_unit_cannot_disappear():
    assert exact_numeric_tokens('填料尺寸 7/16"') == ['7/16"']
    assert not classify_evidence_match('填料尺寸 7/16"', '填料尺寸 7/16', page_overlap=True)["numeric_exact"]


def test_compound_velocity_unit_cannot_disappear():
    assert exact_numeric_tokens("振动值≤7 mm/s") == ["≤7mm/s"]
    assert not classify_evidence_match("振动值≤7 mm/s", "振动值≤7 mm", page_overlap=True)["numeric_exact"]


def test_manifest_path_is_resolved_by_content_hash(tmp_path):
    """A manifest that records a stale absolute path must still resolve by sha256.

    The committed manifest stores machine-specific absolute paths, so a fresh
    clone or a different checkout location made every identity test skip. The
    hash is content-addressed and therefore portable; resolution must fall back
    to it before giving up.
    """
    from scripts.phase14a_parser_ab import resolve_manifest_sources

    payload = b"%PDF-1.4 portable-content"
    digest = hashlib.sha256(payload).hexdigest()
    manual = tmp_path / "somewhere" / "manual.pdf"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(payload)

    sources = [
        {
            "file_name": "manual.pdf",
            "path": str(tmp_path / "does-not-exist" / "manual.pdf"),
            "size_bytes": len(payload),
            "sha256": digest,
            "readable": True,
        }
    ]
    resolved = resolve_manifest_sources(sources, search_roots=[tmp_path])
    assert resolved["manual.pdf"] == manual


def test_manifest_resolution_keeps_original_when_hash_unknown(tmp_path):
    """No matching file on disk must leave the entry unresolved, not invent one."""
    from scripts.phase14a_parser_ab import resolve_manifest_sources

    sources = [
        {
            "file_name": "missing.pdf",
            "path": str(tmp_path / "missing.pdf"),
            "size_bytes": 4,
            "sha256": hashlib.sha256(b"other").hexdigest(),
            "readable": True,
        }
    ]
    resolved = resolve_manifest_sources(sources, search_roots=[tmp_path])
    assert resolved == {}



def test_replay_rejects_unscored_block_tampering():
    from scripts.phase14a_parser_ab import ARTIFACT, load, validate_bundle_fingerprints

    artifact = load(ARTIFACT)
    artifact["normalized_records"]["mineru"][0]["text"] += " tampered unscored cover block"
    with pytest.raises(ValueError, match="fingerprint"):
        validate_bundle_fingerprints(artifact)
