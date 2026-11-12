"""Speech input and spoken-answer endpoints.

Both endpoints are anonymous (like ``POST /query``) because the workbench has no
browser token; abuse is controlled by ``BUCKET_VOICE`` instead. Audio is never
written to disk: it lives in request memory and is released when the request
finishes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from industrial_rag.errors import AppError
from industrial_rag.voice_provider import VoiceProvider, VoiceProviderError
from industrial_rag.voice_text import (
    clean_for_speech,
    is_low_confidence,
    mime_for_kind,
    sniff_audio_kind,
)

logger = logging.getLogger("industrial_rag.voice")

router = APIRouter(prefix="/v1/voice", tags=["voice"])

# A recording shorter than this is almost always an accidental tap.
_MIN_DURATION_MS = 500
_UPLOAD_CHUNK_SIZE = 256 * 1024


def _settings(request: Request) -> Any:
    return request.app.state.resolved_settings


def _ensure_enabled(request: Request) -> VoiceProvider:
    """Return the provider, or refuse when the feature is off or unconfigured."""

    settings = _settings(request)
    if not getattr(settings, "voice_enabled", False):
        raise AppError("VOICE_DISABLED", "语音功能未启用。", status_code=404)
    provider = getattr(request.app.state, "voice_provider", None)
    if provider is None:
        raise AppError("VOICE_DISABLED", "语音功能未启用。", status_code=404)
    return provider


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Read incrementally so one request cannot exhaust process memory."""

    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise AppError(
                "VOICE_AUDIO_TOO_LARGE",
                f"录音超过 {max_bytes // (1024 * 1024)} MB 上限。",
                status_code=413,
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/transcribe")
async def transcribe(
    request: Request,
    file: UploadFile,
    duration_ms: int = Form(0),
) -> JSONResponse:
    """Transcribe one recorded clip.

    The recognised text is returned for the user to review; it is never sent on
    to the answer pipeline automatically, because misheard industrial terms are
    expensive to correct downstream.
    """

    provider = _ensure_enabled(request)
    settings = _settings(request)

    if duration_ms and duration_ms < _MIN_DURATION_MS:
        raise AppError(
            "VOICE_AUDIO_TOO_SHORT", "说话时间太短，请重试。", status_code=422
        )

    max_seconds = int(getattr(settings, "voice_max_audio_seconds", 60))
    if duration_ms and duration_ms > max_seconds * 1000:
        raise AppError(
            "VOICE_AUDIO_TOO_LARGE",
            f"录音超过 {max_seconds} 秒上限。",
            status_code=413,
        )

    audio = await _read_limited(
        file, int(getattr(settings, "voice_max_audio_bytes", 5_242_880))
    )
    if not audio:
        raise AppError("VOICE_AUDIO_UNRECOGNISED", "未收到音频数据。", status_code=415)

    kind = sniff_audio_kind(audio)
    if kind is None:
        raise AppError(
            "VOICE_AUDIO_UNRECOGNISED",
            "无法识别音频格式，请重新录音。",
            status_code=415,
        )

    try:
        result = await provider.transcribe(audio, mime_for_kind(kind))
    except VoiceProviderError as error:
        logger.warning("voice transcribe failed: %s", error)
        raise AppError(
            "VOICE_UPSTREAM_UNAVAILABLE",
            "语音服务暂不可用，请稍后重试。",
            status_code=503,
        ) from error

    text = (result.text or "").strip()
    low = is_low_confidence(text)
    # Log the length, never the recognised text: it may contain site details.
    logger.info(
        "voice transcribe ok: %d bytes, %d chars, low_confidence=%s",
        len(audio),
        len(text),
        low,
    )
    return JSONResponse(
        {"text": text, "language": result.language, "low_confidence": low}
    )


@router.post("/speak")
async def speak(request: Request, payload: dict) -> Response:
    """Synthesize the answer text and stream the audio back.

    The upstream returns a temporary URL; the audio is downloaded here so the
    browser never receives an OSS link that expires and cannot be access
    controlled.
    """

    provider = _ensure_enabled(request)
    settings = _settings(request)

    raw_text = str(payload.get("text") or "").strip()
    if not raw_text:
        raise AppError("VOICE_EMPTY_TEXT", "没有可朗读的文本。", status_code=422)

    max_chars = int(getattr(settings, "voice_tts_max_chars", 600))
    spoken = clean_for_speech(raw_text, max_chars=max_chars)
    if not spoken:
        raise AppError(
            "VOICE_EMPTY_TEXT", "清理后没有可朗读的内容。", status_code=422
        )
    truncated = len(spoken) >= max_chars

    try:
        audio = await provider.synthesize(spoken)
    except VoiceProviderError as error:
        logger.warning("voice synthesize failed: %s", error)
        raise AppError(
            "VOICE_UPSTREAM_UNAVAILABLE",
            "语音合成暂不可用，请稍后重试。",
            status_code=503,
        ) from error

    headers = {"Cache-Control": "no-store"}
    if truncated:
        headers["X-Voice-Truncated"] = "true"
    return Response(content=audio, media_type="audio/wav", headers=headers)
