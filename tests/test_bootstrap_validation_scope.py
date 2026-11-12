"""Bootstrap-aware canonical validation scope tests."""

from pathlib import Path

from industrial_rag.services.golden_set_policy import (
    GoldenSetPolicy,
    scope_policy_for_documents,
)


def _policy() -> GoldenSetPolicy:
    return GoldenSetPolicy(
        version="test",
        source_path=Path("golden.jsonl"),
        source_sha256="hash",
        questions=(
            {"id": "A", "expected_citations": [{"source_file": "a.pdf"}]},
            {"id": "B", "expected_citations": [{"source_file": "b.pdf"}]},
            {
                "id": "AB",
                "expected_citations": [
                    {"source_file": "a.pdf"},
                    {"source_file": "b.pdf"},
                ],
            },
            {"id": "N", "expects_evidence": False, "expected_citations": []},
        ),
    )


def test_empty_kb_bootstrap_scopes_to_candidate_documents_and_negatives() -> None:
    scoped = scope_policy_for_documents(_policy(), {"a.pdf"}, bootstrap=True)

    assert [question["id"] for question in scoped.questions] == ["A", "N"]


def test_active_generation_keeps_full_scope_for_a_plus_b_candidate() -> None:
    scoped = scope_policy_for_documents(_policy(), {"a.pdf", "b.pdf"}, bootstrap=False)

    assert [question["id"] for question in scoped.questions] == ["A", "B", "AB", "N"]


def test_missing_active_document_cannot_be_hidden_by_bootstrap_scope() -> None:
    scoped = scope_policy_for_documents(_policy(), {"b.pdf"}, bootstrap=False)

    assert [question["id"] for question in scoped.questions] == ["A", "B", "AB", "N"]
