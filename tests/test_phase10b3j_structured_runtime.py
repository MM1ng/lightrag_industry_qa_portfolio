from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from industrial_rag.citation_formatter import Citation, encode_source_ref
from industrial_rag.config import Settings
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, LightRAGService


class StructuredBackend:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.generate_calls: list[dict[str, Any]] = []
        self.query_calls = 0

    async def initialize_storages(self) -> None:
        return None

    async def finalize_storages(self) -> None:
        return None

    async def ainsert(self, input: list[str], **kwargs: object) -> str:
        return "track"

    async def get_track_status(self, track_id: str) -> dict[str, str]:
        return {track_id: "processed"}

    async def aquery_data(self, query: str, param: object) -> dict[str, object]:
        self.query_calls += 1
        source = encode_source_ref(Citation("manual.pdf", 9, "child-1"))
        return {
            "status": "success",
            "data": {
                "entities": [],
                "relationships": [],
                "chunks": [
                    {"content": "轴承架油位应保持在观察窗中间位置。", "file_path": source}
                ],
                "references": [{"file_path": source}],
            },
        }

    async def generate(
        self, question: str, context: str, system_prompt: str, **kwargs: object
    ) -> str:
        self.generate_calls.append(
            {"question": question, "context": context, "system_prompt": system_prompt, **kwargs}
        )
        return self.reply


def _settings(tmp_path: Path, *, enabled: bool) -> Settings:
    return Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-only-key",
            "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "LLM_MODEL": "qwen-plus-2025-07-28",
            "LIGHTRAG_WORKING_DIR": str(tmp_path / "storage"),
            "QDRANT_GENERATION": "g1",
            "QA_STRUCTURED_CITATION_OUTPUT_ENABLED": str(enabled).lower(),
        }
    )


@pytest.mark.asyncio
async def test_valid_structured_output_calls_backend_once_and_maps_child(tmp_path: Path) -> None:
    backend = StructuredBackend(
        '{"status":"success","answer_points":[{"text":"油位应在观察窗中间。",'
        '"source_ids":["S1"]}],"unresolved_requirement_ids":[]}'
    )
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert len(backend.generate_calls) == 1
    assert backend.generate_calls[0]["response_format"] == {"type": "json_object"}
    assert "请只输出合法JSON" in backend.generate_calls[0]["system_prompt"]
    assert result.answer_points[0].evidence_ids == ("E1",)
    assert result.citations == (Citation("manual.pdf", 9, "child-1"),)
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_output_valid is True
    assert result.retrieval_trace.backend_generate_call_count == 1
    assert backend.query_calls == 1


@pytest.mark.asyncio
async def test_valid_structured_output_preserves_all_selected_context_citations(tmp_path: Path) -> None:
    class MultiContextBackend(StructuredBackend):
        async def aquery_data(self, query: str, param: object) -> dict[str, object]:
            self.query_calls += 1
            first = encode_source_ref(Citation("manual.pdf", 9, "child-1"))
            second = encode_source_ref(Citation("manual.pdf", 10, "child-2"))
            return {
                "status": "success",
                "data": {
                    "entities": [],
                    "relationships": [],
                    "chunks": [
                        {"content": "轴承架油位应在观察窗中间位置。", "file_path": first},
                        {"content": "轴承架油位需要定期检查。", "file_path": second},
                    ],
                    "references": [],
                },
            }

    backend = MultiContextBackend(
        '{"status":"success","answer_points":[{"text":"综合结论。",'
        '"source_ids":["S1"]}],"unresolved_requirement_ids":[]}'
    )
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert result.citations == (
        Citation("manual.pdf", 9, "child-1"),
        Citation("manual.pdf", 10, "child-2"),
    )


@pytest.mark.asyncio
async def test_invalid_source_uses_j0_postprocessing_without_second_generation(tmp_path: Path) -> None:
    backend = StructuredBackend(
        '{"status":"success","answer_points":[{"text":"保留文本。",'
        '"source_ids":["S9"]}],"unresolved_requirement_ids":[]}'
    )
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert result.answer == "保留文本。"
    assert len(backend.generate_calls) == 1
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_citation_fallback is True
    assert (
        result.retrieval_trace.structured_citation_fallback_mode
        == "fallback_to_j0_postprocessing"
    )


@pytest.mark.asyncio
async def test_unparseable_output_is_safe_without_second_generation(tmp_path: Path) -> None:
    backend = StructuredBackend("not json")
    service = LightRAGService(_settings(tmp_path, enabled=True), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.answer_status == "insufficient_evidence"
    assert len(backend.generate_calls) == 1
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_citation_fallback is True
    assert (
        result.retrieval_trace.structured_citation_fallback_mode
        == "safe_failure_no_second_generation"
    )


@pytest.mark.asyncio
async def test_flag_off_keeps_existing_non_json_prompt_and_result_shape(tmp_path: Path) -> None:
    backend = StructuredBackend("原J0回答。")
    service = LightRAGService(_settings(tmp_path, enabled=False), backend=backend)
    await service.initialize()

    result = await service.query("轴承架油位应保持在什么位置？")

    assert len(backend.generate_calls) == 1
    assert "response_format" not in backend.generate_calls[0]
    assert "请只输出合法JSON" not in backend.generate_calls[0]["system_prompt"]
    assert result.answer == "原J0回答。"
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.structured_citation_flag is False


def _packing_prompt() -> str:
    from industrial_rag.evidence_answer_schema import EvidenceRef
    from industrial_rag.lightrag_service import _structured_citation_system_prompt
    from industrial_rag.structured_citation_output import (
        RequirementRegistry,
        SourceRegistry,
    )

    refs = (
        EvidenceRef(
            evidence_id="E1",
            chunk_id="c1",
            generation_id="g1",
            document_name="manual.pdf",
            citation_id="cite_1",
            context_role="primary",
            text=(
                "编织填料作为所有泵的标准设备提供。安装压盖螺栓的螺母时，仅用手拧紧。"
                "在启动时调节压盖螺栓的螺母，达到每分钟滴漏 40-65 滴。"
                "若填料函装填不当导致过度滴漏，需要重新装填填料函。"
            ),
            is_child=True,
        ),
    )
    return _structured_citation_system_prompt(
        SourceRegistry.from_evidence(refs), RequirementRegistry.from_requirements(())
    )


def test_structured_prompt_requires_answering_from_partial_evidence() -> None:
    """Regression: the strict prompt made the model return zero answer points.

    Measured before the fix: the same prompt returned an empty answer_points
    list in 4 of 5 calls for a question the evidence clearly answered.
    """

    prompt = _packing_prompt()

    assert "必须输出答案点" in prompt
    assert "零件表" in prompt or "故障排查表" in prompt


def test_structured_prompt_still_allows_refusal_for_unrelated_sources() -> None:
    """Loosening must stay bounded: refusal remains the fallback, not the default."""

    prompt = _packing_prompt()

    assert "完全无关" in prompt
    assert "insufficient_evidence" in prompt


def test_unsupported_assertion_audit_records_invented_identifier() -> None:
    """Observation only: the point is recorded, not dropped."""

    from industrial_rag.answer_grounding import AnswerPoint
    from industrial_rag.citation_formatter import Citation
    from industrial_rag.evidence_policy import EvidenceCandidate
    from industrial_rag.lightrag_service import _unsupported_assertion_audit

    evidence = "对于直连结构，轴承温度不得超过电机允许值（具体按照电机铭牌要求，在电机外壳测量）。"
    candidates = (EvidenceCandidate(Citation("m.pdf", 45, "c1"), evidence, 1),)
    grounded = type(
        "G",
        (),
        {
            "answer_points": (
                AnswerPoint(
                    "P1",
                    "对于直连结构（API 682 标准），轴承温度不得超过电机允许值。",
                    ("E1",),
                    "supported",
                ),
            )
        },
    )()

    audit = _unsupported_assertion_audit(grounded, candidates)

    assert audit, "invented identifier must be recorded"
    assert set(audit[0]["unverified_identifiers"]) == {"682", "api"}


def test_unsupported_assertion_audit_ignores_faithful_restatement() -> None:
    from industrial_rag.answer_grounding import AnswerPoint
    from industrial_rag.citation_formatter import Citation
    from industrial_rag.evidence_policy import EvidenceCandidate
    from industrial_rag.lightrag_service import _unsupported_assertion_audit

    evidence = "对于直连结构，轴承温度不得超过电机允许值（具体按照电机铭牌要求，在电机外壳测量）。"
    candidates = (EvidenceCandidate(Citation("m.pdf", 45, "c1"), evidence, 1),)
    grounded = type(
        "G",
        (),
        {
            "answer_points": (
                AnswerPoint(
                    "P1",
                    "对于直连结构，轴承温度不得超过电机允许值。",
                    ("E1",),
                    "supported",
                ),
            )
        },
    )()

    assert _unsupported_assertion_audit(grounded, candidates) == ()


def test_unsupported_assertion_audit_accepts_identifier_present_in_any_candidate() -> None:
    """One supporting chunk is enough; only identifiers absent everywhere flag."""

    from industrial_rag.answer_grounding import AnswerPoint
    from industrial_rag.citation_formatter import Citation
    from industrial_rag.evidence_policy import EvidenceCandidate
    from industrial_rag.lightrag_service import _unsupported_assertion_audit

    candidates = (
        EvidenceCandidate(Citation("m.pdf", 45, "c1"), "轴承温度不得超过电机允许值。", 1),
        EvidenceCandidate(Citation("m.pdf", 46, "c2"), "参见 API 682 标准的相关要求。", 2),
    )
    grounded = type(
        "G",
        (),
        {
            "answer_points": (
                AnswerPoint("P1", "按 API 682 标准，轴承温度不得超过电机允许值。", ("E1",), "supported"),
            )
        },
    )()

    assert _unsupported_assertion_audit(grounded, candidates) == ()
