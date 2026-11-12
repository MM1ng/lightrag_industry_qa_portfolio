"""Tests for the DashScope voice adapter using a stubbed HTTP transport."""

from __future__ import annotations

import httpx
import pytest
from industrial_rag.voice_provider import (
    DashScopeVoiceProvider,
    TranscriptionResult,
    VoiceProviderError,
)


def make_provider(handler) -> DashScopeVoiceProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return DashScopeVoiceProvider(
        api_key="test-key",
        base_url="https://example.invalid/api/v1",
        model="qwen3-asr-flash",
        tts_model="qwen3-tts-flash",
        voice="Cherry",
        client=client,
    )


@pytest.mark.asyncio
async def test_transcribe_parses_text_and_language() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "output": {"choices": [{"message": {"content": [{"text": "泵的温度是多少？"}]},
                                    "finish_reason": "stop"}]},
            "usage": {"audio_tokens": 10},
        })

    provider = make_provider(handler)
    result = await provider.transcribe(b"RIFFdata", "audio/wav")
    assert isinstance(result, TranscriptionResult)
    assert result.text == "泵的温度是多少？"


@pytest.mark.asyncio
async def test_transcribe_sends_expected_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "output": {"choices": [{"message": {"content": [{"text": "x"}]}}]}})

    provider = make_provider(handler)
    await provider.transcribe(b"RIFFabc", "audio/wav")
    assert captured["model"] == "qwen3-asr-flash"
    audio = captured["input"]["messages"][0]["content"][0]["audio"]
    assert audio.startswith("data:audio/wav;base64,")


@pytest.mark.asyncio
async def test_transcribe_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "Throttling"})

    provider = make_provider(handler)
    with pytest.raises(VoiceProviderError, match="429"):
        await provider.transcribe(b"RIFFdata", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_raises_on_missing_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {}})

    provider = make_provider(handler)
    with pytest.raises(VoiceProviderError, match="missing"):
        await provider.transcribe(b"RIFFdata", "audio/wav")


@pytest.mark.asyncio
async def test_synthesize_returns_audio_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "multimodal-generation" in url:
            return httpx.Response(200, json={
                "output": {"audio": {"url": "https://cdn.invalid/a.wav"}}})
        return httpx.Response(200, content=b"RIFFreal-audio")

    provider = make_provider(handler)
    audio = await provider.synthesize("温度 85 摄氏度")
    assert audio == b"RIFFreal-audio"


@pytest.mark.asyncio
async def test_synthesize_raises_when_url_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"audio": {}}})

    provider = make_provider(handler)
    with pytest.raises(VoiceProviderError, match="audio url"):
        await provider.synthesize("x")


@pytest.mark.asyncio
async def test_timeout_becomes_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    provider = make_provider(handler)
    with pytest.raises(VoiceProviderError, match="timeout"):
        await provider.transcribe(b"RIFFdata", "audio/wav")
