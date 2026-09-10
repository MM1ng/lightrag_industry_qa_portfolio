"""Read-only administrator APIs for canonical validation visualization."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.db.session import get_session
from industrial_rag.errors import AppError
from industrial_rag.services.evaluation_center_service import EvaluationCenterError, EvaluationCenterService

router = APIRouter(prefix="/v1/admin/evaluation", tags=["evaluation"])


@router.get("/knowledge-bases")
async def list_evaluation_knowledge_bases(
    request: Request,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {"items": await EvaluationCenterService(session, _settings_artifact_dir(request)).list_kb_summaries()}


@router.get("/knowledge-bases/{kb_id}")
async def get_evaluation_dashboard(
    kb_id: str,
    request: Request,
    generation_id: str | None = Query(None, max_length=64),
    compare_generation_id: str | None = Query(None, max_length=64),
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    service = EvaluationCenterService(session, _settings_artifact_dir(request))
    try:
        return await service.get_dashboard(kb_id, generation_id, compare_generation_id)
    except EvaluationCenterError as error:
        raise AppError(error.code, error.message, status_code=404) from error


def _settings_artifact_dir(request: Request):
    resolved = getattr(request.app.state, "resolved_settings", None)
    if resolved is not None:
        return resolved.validation_artifact_dir
    return _artifact_dir()


def _artifact_dir():
    # Keep the read-only dashboard independent of model credentials.  The API
    # may be started for diagnostics before a full Settings object is resolved.
    import os
    from pathlib import Path
    from industrial_rag.config import PROJECT_ROOT

    raw = os.environ.get("VALIDATION_ARTIFACT_DIR")
    path = Path(raw) if raw else PROJECT_ROOT / "artifacts" / "validation-runs"
    return path if path.is_absolute() else PROJECT_ROOT / path
