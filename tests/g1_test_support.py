"""Explicit offline stubs for lifecycle tests, not acceptance-quality evidence."""

from dataclasses import replace

from industrial_rag.services.golden_set_policy import load_canonical_policy


def passing_records(generation_id, policy=None):
    policy = policy or load_canonical_policy()
    return [
        {
            "id": q["id"],
            "status_code": 200,
            "expects_evidence": q["expects_evidence"],
            "answer_status": "success" if q["expects_evidence"] else "insufficient_evidence",
            "citation_trace": [
                {
                    "document_id": "offline-doc",
                    "chunk_id": "offline-chunk",
                    "generation_id": generation_id,
                }
            ]
            if q["expects_evidence"]
            else [],
            "trace_complete": True,
            "citation_match": True,
            "negative_case_passed": True,
            "actual_generation_id": generation_id,
        }
        for q in policy.questions
    ]


def stub_lifecycle_policy(monkeypatch):
    """Isolate lifecycle/CAS assertions from corpus approval using a named stub.

    G1 integration tests exercise the real admission resolver separately.
    """
    from industrial_rag.services import validation_policy

    original = validation_policy.resolve_validation_policy

    async def resolve(*args, **kwargs):
        resolved = await original(*args, **kwargs)
        return replace(
            resolved,
            snapshot={
                **resolved.snapshot,
                "applicability": "APPLICABLE",
                "admission": "ADMITTED",
                "reasons": [],
                "positive_count": 18,
                "negative_count": 2,
            },
        )

    monkeypatch.setattr(validation_policy, "resolve_validation_policy", resolve)
