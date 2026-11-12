from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from industrial_rag.services.expanded_development_dataset import (
    DatasetContractError,
    audit_dataset,
    canonical_dataset_fingerprint,
    load_generation_snapshot,
    validate_dataset,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATION = ROOT / "evaluation/retrieval_foundation/dev_generation_v2"


def _snapshot():
    return load_generation_snapshot(GENERATION)


def _case(snapshot, *, question_id="D-V2-TEST", child_id=None):
    child = snapshot.children[child_id or next(iter(snapshot.children))]
    return {
        "question_id": question_id,
        "question": "该部件的安装要求是什么？",
        "split": "development",
        "source_document_id": child.document_id,
        "question_type": "installation",
        "difficulty": "EASY",
        "evidence_pattern": "single_evidence",
        "expected_child_chunk_ids": [child.chunk_id],
        "expected_parent_chunk_ids": [child.parent_chunk_id],
        "evidence": [
            {
                "child_chunk_id": child.chunk_id,
                "parent_chunk_id": child.parent_chunk_id,
                "text": child.content,
                "page_start": child.page_start,
                "page_end": child.page_end,
                "section_path": child.section_path,
                "location": f"第 {child.page_start} 页 / {child.section_title}",
                "necessary": True,
            }
        ],
    }


def test_load_generation_snapshot_exposes_all_child_parent_records():
    snapshot = _snapshot()
    assert snapshot.generation_id == "dev-v2-20260902"
    assert len(snapshot.children) == 453
    assert len(snapshot.parents) == 447
    assert all(child.parent_chunk_id in snapshot.parents for child in snapshot.children.values())


def test_validate_dataset_accepts_complete_case_and_rejects_missing_contract_fields():
    snapshot = _snapshot()
    case = _case(snapshot)
    assert validate_dataset([case], snapshot) == []

    invalid = copy.deepcopy(case)
    del invalid["difficulty"]
    with pytest.raises(DatasetContractError, match="difficulty"):
        validate_dataset([invalid], snapshot)


def test_validate_dataset_rejects_duplicate_ids_wrong_split_and_missing_evidence():
    snapshot = _snapshot()
    first = _case(snapshot, question_id="D-V2-001")
    duplicate = _case(snapshot, question_id="D-V2-001")
    with pytest.raises(DatasetContractError, match="duplicate"):
        validate_dataset([first, duplicate], snapshot)

    wrong_split = copy.deepcopy(first)
    wrong_split["split"] = "validation"
    with pytest.raises(DatasetContractError, match="development"):
        validate_dataset([wrong_split], snapshot)

    no_evidence = copy.deepcopy(first)
    no_evidence["evidence"] = []
    with pytest.raises(DatasetContractError, match="evidence"):
        validate_dataset([no_evidence], snapshot)


def test_validate_dataset_rejects_unknown_child_bad_parent_and_text_mismatch():
    snapshot = _snapshot()
    case = _case(snapshot)

    unknown = copy.deepcopy(case)
    unknown["expected_child_chunk_ids"] = ["not-in-generation"]
    with pytest.raises(DatasetContractError, match="child"):
        validate_dataset([unknown], snapshot)

    bad_parent = copy.deepcopy(case)
    bad_parent["expected_parent_chunk_ids"] = ["not-in-generation"]
    with pytest.raises(DatasetContractError, match="parent"):
        validate_dataset([bad_parent], snapshot)

    bad_text = copy.deepcopy(case)
    bad_text["evidence"][0]["text"] = "不是冻结快照中的证据"
    with pytest.raises(DatasetContractError, match="text"):
        validate_dataset([bad_text], snapshot)

    incomplete = copy.deepcopy(case)
    incomplete["evidence"] = []
    with pytest.raises(DatasetContractError, match="evidence"):
        validate_dataset([incomplete], snapshot)


def test_fingerprint_is_stable_under_json_key_order_and_audit_counts_coverage():
    snapshot = _snapshot()
    first = _case(snapshot, question_id="D-V2-001")
    reordered = json.loads(json.dumps(first, ensure_ascii=False, sort_keys=True))
    assert canonical_dataset_fingerprint([first]) == canonical_dataset_fingerprint([reordered])

    second = _case(snapshot, question_id="D-V2-002")
    second["question"] = "该部件安装要求是什么？"
    result = audit_dataset([first, second], snapshot, legacy_ids={"S014"})
    assert result["counts"]["total_questions"] == 2
    assert result["coverage"]["difficulty"]["EASY"] == 2
    assert result["coverage"]["evidence_pattern"]["single_evidence"] == 2
    assert result["duplicate_audit"]["question_duplicate_pairs"]


def test_frozen_expanded_dataset_has_legacy_traceability_and_effectiveness_gate():
    dataset_path = ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl"
    manifest_path = ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json"
    cases = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(cases) == 24
    assert {case["question_id"] for case in cases if case.get("legacy_source")} == {
        "S014", "S015", "S006", "S003", "S016", "S011"
    }
    assert manifest["final_status"] == "READY_FOR_EFFECTIVENESS_EVAL"
    assert manifest["guards"]["a0_a1_a2_not_run"] is True
