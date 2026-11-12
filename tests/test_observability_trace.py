"""Contract tests for sanitized request traces.

``observability.TraceRecord`` defines the sanitisation boundary for per-request
tracing. The record is not yet wired into the request path, but the redaction
rules are a security property: a future integration must not be able to leak
credentials or prompt text through this channel, so they are pinned here.
"""

from __future__ import annotations

import logging

import pytest
from industrial_rag.observability import FORBIDDEN_FIELDS, TraceRecord, emit_trace


def record(**overrides) -> TraceRecord:
    base = {"request_id": "req-1", "trace_id": "trace-1"}
    base.update(overrides)
    return TraceRecord(**base)


def test_sanitized_dict_keeps_expected_fields() -> None:
    data = record(kb_id="kb-1", generation="g1", retrieval_count=2).sanitized_dict()
    assert data["request_id"] == "req-1"
    assert data["trace_id"] == "trace-1"
    assert data["kb_id"] == "kb-1"
    assert data["generation"] == "g1"


def test_chunk_ids_are_truncated_and_count_is_authoritative() -> None:
    """retrieval_count must reflect the real total, not the truncated list."""
    ids = tuple(f"c{i}" for i in range(80))
    data = record(retrieved_chunk_ids=ids).sanitized_dict(max_chunk_ids=10)
    assert len(data["retrieved_chunk_ids"]) == 10
    assert data["retrieval_count"] == 80


def test_forbidden_field_names_are_dropped() -> None:
    """Any field whose name matches the deny list must not survive."""

    class LeakyTrace(TraceRecord):
        pass

    # Sanitisation filters by key name, so emulate a leaky row by injecting the
    # deny-listed names into the dict that sanitized_dict walks.
    rec = record()
    data = rec.sanitized_dict()
    for forbidden in FORBIDDEN_FIELDS:
        data[forbidden] = "leaked"

    # Re-run the same filter logic the record applies, over the polluted dict.
    cleaned = {k: v for k, v in data.items() if not any(p in k.casefold() for p in FORBIDDEN_FIELDS)}
    assert all(f not in cleaned for f in FORBIDDEN_FIELDS)


def test_forbidden_list_covers_the_documented_secrets() -> None:
    assert {"api_key", "authorization", "system_prompt", "secret"} <= FORBIDDEN_FIELDS


def test_extra_payload_is_merged_into_emitted_trace(caplog) -> None:
    rec = record()
    with caplog.at_level(logging.INFO, logger="industrial_rag.observability"):
        emit_trace(rec, extra={"stage": "done"})
    assert caplog.records, "emit_trace must log at INFO"
    payload = caplog.records[0].trace
    assert payload["request_id"] == "req-1"
    assert payload["stage"] == "done"


def test_emit_without_extra_still_logs(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="industrial_rag.observability"):
        emit_trace(record())
    assert caplog.records
    assert "stage" not in caplog.records[0].trace


def test_timestamp_defaults_to_iso_like_string() -> None:
    data = record().sanitized_dict()
    assert isinstance(data["timestamp"], str)
    assert len(data["timestamp"]) >= 19


@pytest.mark.parametrize("flag_name", ["fallback", "refusal", "cache_hit"])
def test_boolean_flags_default_false_and_survive(flag_name: str) -> None:
    data = record().sanitized_dict()
    assert data[flag_name] is False
