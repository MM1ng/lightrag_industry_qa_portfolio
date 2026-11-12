"""DashScope adapter for speech recognition and synthesis.

The router depends on the ``VoiceProvider`` protocol, not on this class, so tests
can substitute a fake and never touch the network or the paid quota.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from industrial_rag.http_client import build_async_client

logger = logging.getLogger("industrial_rag.voice_provider")

_MULTIMODAL_PATH = "/services/aigc/multimodal-generation/generation"


class VoiceProviderError(RuntimeError):
    """Raised for any upstream failure the router should map to 503."""


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    emotion: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VoiceProvider(Protocol):
    """Minimal surface the router needs."""

    async def transcribe(self, audio: bytes, mime: str) -> TranscriptionResult: ...

    async def synthesize(self, text: str) -> bytes: ...


class DashScopeVoiceProvider:
    """Calls the DashScope multimodal endpoint for ASR and TTS."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        tts_model: str,
        voice: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._tts_model = tts_model
        self._voice = voice
        self._owns_client = client is None
        self._client = client or build_async_client(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, *, sse: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if sse:
            headers["X-DashScope-SSE"] = "enable"
        return headers

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._base_url + _MULTIMODAL_PATH
        try:
            response = await self._client.post(url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as error:
            raise VoiceProviderError(f"voice upstream timeout: {error}") from error
        except httpx.HTTPError as error:
            raise VoiceProviderError(f"voice upstream transport error: {error}") from error
        if response.status_code >= 400:
            raise VoiceProviderError(
                f"voice upstream HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise VoiceProviderError("voice upstream returned non-JSON") from error

    async def transcribe(self, audio: bytes, mime: str) -> TranscriptionResult:
        encoded = base64.b64encode(audio).decode("ascii")
        payload = {
            "model": self._model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "audio", "audio": f"data:{mime};base64,{encoded}"}
                        ],
                    }
                ]
            },
        }
        body = await self._post(payload)
        try:
            message = body["output"]["choices"][0]["message"]
            content = message["content"]
            text = content[0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise VoiceProviderError(
                "voice upstream response missing content text"
            ) from error
        annotations = message.get("annotations") or []
        language = annotations[0].get("language") if annotations else None
        emotion = annotations[0].get("emotion") if annotations else None
        return TranscriptionResult(
            text=str(text), language=language, emotion=emotion, raw=body
        )

    async def synthesize(self, text: str) -> bytes:
        payload = {
            "model": self._tts_model,
            "input": {"text": text, "voice": self._voice, "language_type": "Chinese"},
        }
        body = await self._post(payload)
        try:
            audio_url = body["output"]["audio"]["url"]
        except (KeyError, TypeError) as error:
            raise VoiceProviderError("voice upstream response missing audio url") from error
        try:
            download = await self._client.get(audio_url)
        except httpx.TimeoutException as error:
            raise VoiceProviderError(f"voice audio download timeout: {error}") from error
        except httpx.HTTPError as error:
            raise VoiceProviderError(f"voice audio download failed: {error}") from error
        if download.status_code >= 400:
            raise VoiceProviderError(
                f"voice audio download HTTP {download.status_code}"
            )
        return download.content
