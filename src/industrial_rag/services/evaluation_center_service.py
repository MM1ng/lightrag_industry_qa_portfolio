"""Read-only canonical validation data for the administrator evaluation center."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import (
    KnowledgeBase,
    ValidationRun,
    ValidationRunStatus,
    VectorIndexGeneration,
)


class EvaluationCenterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EvaluationCenterService:
    def __init__(self, session: AsyncSession, artifact_dir: Path) -> None:
        self.session = session
        self.artifact_dir = artifact_dir.resolve()

    async def list_kb_summaries(self) -> list[dict[str, Any]]:
        result = await self.session.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()))
        rows = []
        for kb in result.scalars().all():
            generations = await self._generations(kb.id)
            rows.append({"id": kb.id, "name": kb.name, "active_generation_id": kb.active_vector_generation_id, "generation_count": len(generations)})
        return rows

    async def get_dashboard(self, kb_id: str, generation_id: str | None = None, compare_generation_id: str | None = None) -> dict[str, Any]:
        kb = await self.session.get(KnowledgeBase, kb_id)
        if kb is None:
            raise EvaluationCenterError("knowledge_base_not_found", "知识库不存在。")
        generations = await self._generations(kb_id)
        active = next((g for g in generations if g.id == (generation_id or kb.active_vector_generation_id)), None)
        if active is None and generations:
            active = generations[0]
        if active is None:
            return self._empty(kb)
        current = await self._load_latest(kb_id, active)
        previous = None
        comparison_id = compare_generation_id
        if comparison_id:
            compared = next((g for g in generations if g.id == comparison_id), None)
            if compared:
                previous = await self._load_latest(kb_id, compared)
        else:
            for candidate in generations:
                if candidate.id != active.id:
                    previous = await self._load_latest(kb_id, candidate)
                    if previous:
                        comparison_id = candidate.id
                        break
        comparison = self._compare(current, previous)
        return {
            "knowledge_base": {"id": kb.id, "name": kb.name, "active_generation_id": kb.active_vector_generation_id},
            "active_generation": self._generation_payload(active),
            "comparison_generation": self._generation_payload(next((g for g in generations if g.id == comparison_id), None)),
            "current": current,
            "comparison": comparison,
            "available_generations": [self._generation_payload(g) for g in generations],
        }

    async def _generations(self, kb_id: str) -> list[VectorIndexGeneration]:
        result = await self.session.execute(select(VectorIndexGeneration).where(VectorIndexGeneration.knowledge_base_id == kb_id).order_by(VectorIndexGeneration.created_at.desc()))
        return list(result.scalars().all())

    async def _load_latest(self, kb_id: str, generation: VectorIndexGeneration) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(ValidationRun).where(
                ValidationRun.knowledge_base_id == kb_id,
                ValidationRun.generation_id == generation.id,
                ValidationRun.status.in_([ValidationRunStatus.passed, ValidationRunStatus.failed, ValidationRunStatus.blocked]),
                ValidationRun.finished_at.is_not(None),
            ).order_by(ValidationRun.finished_at.desc())
        )
        run = result.scalars().first()
        candidates = []
        if run and run.result_artifact_path:
            candidates.append(Path(run.result_artifact_path))
        candidates.extend(sorted((self.artifact_dir / kb_id / generation.id).glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if (self.artifact_dir / kb_id / generation.id).is_dir() else [])
        for path in candidates:
            payload = self._read_jsonl(path)
            if payload is None:
                continue
            status = run.status.value if run else "available"
            return self._artifact_payload(payload, path, run, status)
        return None

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]] | None:
        try:
            resolved = path.resolve()
            resolved.relative_to(self.artifact_dir)
            rows = [json.loads(line) for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        return rows if all(isinstance(row, dict) for row in rows) else None

    def _artifact_payload(self, rows: list[dict[str, Any]], path: Path, run: ValidationRun | None, status: str) -> dict[str, Any]:
        header = rows[0] if rows and rows[0].get("schema") == "g1-validation-v1" else None
        summary = header.get("summary", {}) if header else {}
        policy = summary.get("policy_snapshot", {})
        labels = {q["id"]: {"question": q["question"], "expects_evidence": q["expects_evidence"]}
                  for q in policy.get("questions", [])} if header else self._question_labels()
        rows = rows[1:] if header else [r for r in rows if r.get("id") or r.get("question_id")]
        rows = [{**row, **labels.get(str(row.get("id") or row.get("question_id")), {})} for row in rows]
        positive = [r for r in rows if r.get("expects_evidence")]
        negative = [r for r in rows if not r.get("expects_evidence")]
        positive_hit = sum(bool(r.get("citation_match")) for r in positive)
        negative_passed = sum(bool(r.get("negative_case_passed")) for r in negative)
        trace_complete = sum(bool(r.get("trace_complete")) for r in positive)
        metrics = {
            "question_count": len(rows),
            "positive_match_count": positive_hit,
            "positive_question_count": len(positive),
            "negative_passed_count": negative_passed,
            "negative_question_count": len(negative),
            "citation_gate": bool(positive) and positive_hit == len(positive),
            "trace_gate": bool(positive) and trace_complete == len(positive),
            "negative_unsupported_gate": bool(negative) and negative_passed == len(negative),
            "latency_ms": round(sum(float(r.get("latency_ms") or 0) for r in rows) / len(rows)) if rows else 0,
        }
        if header and summary.get("outcome") == "BLOCKED":
            metrics.update({"citation_gate": None, "trace_gate": None, "negative_unsupported_gate": None})
        return {
            "status": status,
            "measurement_status": summary.get("run", {}).get("measurement_status", "MEASURED" if rows else "NOT_MEASURED"),
            "validation_policy_fingerprint": summary.get("validation_policy_fingerprint"),
            "effective_questions_sha256": policy.get("effective_questions_sha256"),
            "passed": run.passed if run else None,
            "validation_run_id": run.id if run else None,
            "finished_at": run.finished_at.isoformat() if run and run.finished_at else datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None,
            "source": {"artifact_name": path.name, "schema": "g1-validation-v1" if header else "legacy-canonical-jsonl", "path_available": True},
            "metrics": metrics,
            "gates": {"citation": metrics["citation_gate"], "trace": metrics["trace_gate"], "negative_unsupported": metrics["negative_unsupported_gate"]},
            "questions": rows,
        }

    @staticmethod
    def _question_labels() -> dict[str, dict[str, Any]]:
        path = Path(__file__).resolve().parents[3] / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return {
            str(row.get("id")): {
                "question": row.get("question"),
                "expects_evidence": row.get("expects_evidence"),
                "expected_behavior": "需要有 citation 证据" if row.get("expects_evidence") else "应拒答且不生成 citation",
            }
            for row in rows
            if isinstance(row, dict) and row.get("id")
        }

    def _compare(self, current: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any]:
        if not current or not previous:
            return {"status": "not_comparable", "reason": "缺少两侧完整的正式 validation artifact。", "metrics": {}, "questions": []}
        current_ids = [r.get("id") or r.get("question_id") for r in current["questions"]]
        previous_ids = [r.get("id") or r.get("question_id") for r in previous["questions"]]
        if (current_ids != previous_ids
            or current.get("effective_questions_sha256") != previous.get("effective_questions_sha256")
            or current.get("measurement_status") == "NOT_MEASURED"
            or previous.get("measurement_status") == "NOT_MEASURED"):
            return {"status": "not_comparable", "reason": "两侧 canonical question identity 不一致。", "metrics": {}, "questions": []}
        metric_delta = {key: current["metrics"].get(key, 0) - previous["metrics"].get(key, 0) for key in current["metrics"] if isinstance(current["metrics"].get(key), (int, float)) and isinstance(previous["metrics"].get(key), (int, float))}
        rows = []
        for before, after in zip(previous["questions"], current["questions"], strict=True):
            changed = any(before.get(key) != after.get(key) for key in ("answer_status", "citation_match", "negative_case_passed", "trace_complete", "citation_trace"))
            rows.append({"id": after.get("id") or after.get("question_id"), "before": before, "after": after, "changed": changed})
        return {"status": "comparable", "reason": None, "metrics": metric_delta, "questions": rows}

    @staticmethod
    def _generation_payload(generation: VectorIndexGeneration | None) -> dict[str, Any] | None:
        if generation is None:
            return None
        return {"id": generation.id, "generation": generation.generation, "status": generation.status.value, "backend": generation.backend, "created_at": generation.created_at.isoformat() if generation.created_at else None, "activated_at": generation.activated_at.isoformat() if generation.activated_at else None}

    @staticmethod
    def _empty(kb: KnowledgeBase) -> dict[str, Any]:
        return {"knowledge_base": {"id": kb.id, "name": kb.name, "active_generation_id": kb.active_vector_generation_id}, "active_generation": None, "comparison_generation": None, "current": None, "comparison": {"status": "not_comparable", "reason": "暂无正式评估结果。", "metrics": {}, "questions": []}, "available_generations": []}
