"""Router tests using a fake provider; no network, no quota."""

from __future__ import annotations

from fastapi.testclient import TestClient
from industrial_rag.api import create_app
from industrial_rag.config import Settings
from industrial_rag.voice_provider import TranscriptionResult, VoiceProviderError


class FakeProvider:
    def __init__(self, *, text: str = "泵的轴承温度上限是多少？", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[tuple] = []

    async def transcribe(self, audio: bytes, mime: str) -> TranscriptionResult:
        self.calls.append(("transcribe", len(audio), mime))
        if self.fail:
            raise VoiceProviderError("upstream down")
        return TranscriptionResult(text=self.text, language="zh")

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(("synthesize", text))
        if self.fail:
            raise VoiceProviderError("upstream down")
        return b"RIFFfakeaudio"


class NoRuntime:
    """Placeholder runtime: these tests never run a query."""

    def close(self) -> None:
        return None


def make_app(
    *,
    enabled: bool = True,
    provider: FakeProvider | None = None,
    rate_limit: int = 10,
):
    # Settings uses slots=True, so dataclasses.replace() does not work; the
    # project convention is to construct a fresh instance with explicit fields.
    # The voice routes are public, so no admin credential is needed here.
    settings = Settings(
        api_key="offline-test-key",
        voice_enabled=enabled,
        voice_rate_limit_per_minute=rate_limit,
        deployment_environment="local_dev",
    )
    return create_app(
        settings=settings,
        runtime_factory=lambda _: NoRuntime(),
        voice_provider=provider or FakeProvider(),
    )


def wav_bytes() -> bytes:
    return b"RIFF" + b"\x00" * 40 + b"WAVEfmt "


def post_audio(client: TestClient, *, duration_ms: str = "1500"):
    return client.post(
        "/v1/voice/transcribe",
        files={"file": ("a.webm", wav_bytes(), "audio/webm")},
        data={"duration_ms": duration_ms},
    )


def test_transcribe_returns_text() -> None:
    app = make_app()
    with TestClient(app) as client:
        response = post_audio(client)
    assert response.status_code == 200
    assert response.json()["text"] == "泵的轴承温度上限是多少？"


def test_transcribe_disabled_returns_404() -> None:
    app = make_app(enabled=False)
    with TestClient(app) as client:
        response = post_audio(client)
    assert response.status_code == 404
    assert response.json()["code"] == "VOICE_DISABLED"


def test_transcribe_rejects_unrecognised_audio() -> None:
    app = make_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/voice/transcribe",
            files={"file": ("a.bin", b"\xde\xad\xbe\xef" * 20, "audio/webm")},
            data={"duration_ms": "1500"},
        )
    assert response.status_code == 415
    assert response.json()["code"] == "VOICE_AUDIO_UNRECOGNISED"


def test_transcribe_rejects_too_short_recording() -> None:
    app = make_app()
    with TestClient(app) as client:
        response = post_audio(client, duration_ms="200")
    assert response.status_code == 422
    assert response.json()["code"] == "VOICE_AUDIO_TOO_SHORT"


def test_transcribe_marks_low_confidence() -> None:
    app = make_app(provider=FakeProvider(text="啊。"))
    with TestClient(app) as client:
        response = post_audio(client)
    body = response.json()
    assert response.status_code == 200
    assert body["low_confidence"] is True


def test_transcribe_upstream_failure_is_503() -> None:
    app = make_app(provider=FakeProvider(fail=True))
    with TestClient(app) as client:
        response = post_audio(client)
    assert response.status_code == 503
    assert response.json()["code"] == "VOICE_UPSTREAM_UNAVAILABLE"


def test_speak_returns_audio_stream() -> None:
    app = make_app()
    with TestClient(app) as client:
        response = client.post("/v1/voice/speak", json={"text": "温度 85 摄氏度"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/")
    assert response.content == b"RIFFfakeaudio"


def test_speak_disabled_returns_404() -> None:
    app = make_app(enabled=False)
    with TestClient(app) as client:
        response = client.post("/v1/voice/speak", json={"text": "x"})
    assert response.status_code == 404


def test_speak_rejects_empty_text() -> None:
    app = make_app()
    with TestClient(app) as client:
        response = client.post("/v1/voice/speak", json={"text": "   "})
    assert response.status_code == 422


def test_speak_truncates_long_text() -> None:
    provider = FakeProvider()
    app = make_app(provider=provider)
    with TestClient(app) as client:
        response = client.post("/v1/voice/speak", json={"text": "字" * 900})
    assert response.status_code == 200
    assert response.headers.get("x-voice-truncated") == "true"
    sent = provider.calls[-1][1]
    assert len(sent) <= 600


def test_speak_upstream_failure_is_503() -> None:
    app = make_app(provider=FakeProvider(fail=True))
    with TestClient(app) as client:
        response = client.post("/v1/voice/speak", json={"text": "x"})
    assert response.status_code == 503


def test_disabled_speak_returns_404_not_422() -> None:
    """The feature switch must answer first, before body validation.

    Regression guard: with a bare ``payload: dict`` annotation FastAPI treated
    the body as a query parameter and answered 422 before the handler could
    report VOICE_DISABLED. A disabled deployment must look "not there" (404),
    not "your request is malformed" (422).
    """
    app = make_app(enabled=False)
    with TestClient(app) as client:
        for body in ({"text": "x"}, {}, {"text": ""}):
            response = client.post("/v1/voice/speak", json=body)
            assert response.status_code == 404, body
            assert response.json()["code"] == "VOICE_DISABLED"


def test_enabling_voice_builds_a_real_provider_at_startup() -> None:
    """Startup must construct the DashScope provider when voice is enabled.

    Regression guard: the first draft read ``settings.dashscope_api_key``, a
    field that does not exist (the real one is ``api_key``). Tests that inject a
    provider never reached that branch, so the crash only appeared on a real
    start-up with IRA_VOICE_ENABLED=true.
    """
    settings = Settings(api_key="offline-test-key", voice_enabled=True)
    app = create_app(settings=settings, runtime_factory=lambda _: NoRuntime())
    with TestClient(app) as client:
        provider = app.state.voice_provider
        assert provider is not None
        assert provider.__class__.__name__ == "DashScopeVoiceProvider"
        # The route is reachable; the upstream call itself is out of scope here.
        assert client.post("/v1/voice/speak", json={"text": "x"}).status_code in {502, 503}


class TestNativeBaseUrl:
    """The speech API is not on the OpenAI-compatible path used for chat.

    Regression guard: reusing ``llm_base_url`` verbatim made every voice call
    return upstream 404, because ``/compatible-mode/v1`` has no
    multimodal-generation route.
    """

    def test_replaces_compatible_mode_path(self) -> None:
        from industrial_rag.api import _dashscope_native_base_url

        settings = Settings(
            api_key="k",
            llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert _dashscope_native_base_url(settings) == "https://dashscope.aliyuncs.com/api/v1"

    def test_passes_through_native_path(self) -> None:
        from industrial_rag.api import _dashscope_native_base_url

        settings = Settings(api_key="k", llm_base_url="https://example.invalid/api/v1")
        assert _dashscope_native_base_url(settings) == "https://example.invalid/api/v1"

    def test_defaults_when_unset(self) -> None:
        from industrial_rag.api import _dashscope_native_base_url

        settings = Settings(api_key="k", llm_base_url="")
        assert _dashscope_native_base_url(settings) == "https://dashscope.aliyuncs.com/api/v1"
