"""Router tests using a fake provider; no network, no quota."""

from __future__ import annotations

import pytest
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


def test_voice_timeout_reaches_the_provider_client() -> None:
    """A setting nobody passes down is a setting that does not exist.

    The adapter used to hard-code 60 s, which was measured too short for a full
    600-char segment (88.7 s upstream), so the ceiling now comes from settings.
    """
    settings = Settings(
        api_key="offline-test-key",
        voice_enabled=True,
        voice_timeout_seconds=37.5,
    )
    app = create_app(settings=settings, runtime_factory=lambda _: NoRuntime())
    with TestClient(app):
        provider = app.state.voice_provider
        assert provider._client.timeout.read == 37.5


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


def test_voice_provider_build_failure_is_logged_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed provider build must leave a trace, not look like a disabled feature.

    Regression guard: with ``NO_PROXY`` containing the bracketed IPv6 literal
    ``[::1]``, httpx raised ``InvalidURL`` while *constructing* the client inside
    ``DashScopeVoiceProvider.__init__``. The lifespan swallowed it because
    ``local_dev`` tolerates partial startup, so the only symptom was 404 on every
    voice call with nothing in the log explaining it.
    """
    import logging

    def boom(**kwargs):
        raise RuntimeError("provider build failed")

    monkeypatch.setattr("industrial_rag.voice_provider.DashScopeVoiceProvider", boom)
    settings = Settings(
        api_key="offline-test-key",
        voice_enabled=True,
        deployment_environment="local_dev",
    )
    app = create_app(settings=settings, runtime_factory=lambda _: NoRuntime())
    with (
        caplog.at_level(logging.WARNING, logger="industrial_rag.api"),
        TestClient(app) as client,
    ):
        response = client.post("/v1/voice/speak", json={"text": "温度不超过85摄氏度。"})

    assert response.status_code == 404
    assert response.json()["code"] == "VOICE_DISABLED"
    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("startup incomplete" in rec.getMessage() for rec in warnings), warnings
    # The traceback has to ride along with the warning, or the operator is left
    # with "something was degraded" and no way to find out what.
    causes = [repr(rec.exc_info[1]) for rec in warnings if rec.exc_info and rec.exc_info[1]]
    assert any("provider build failed" in cause for cause in causes), causes


def pcm_clip(frames: int = 8) -> bytes:
    """A real mono PCM WAV, so stitching has something legitimate to join."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(bytes([(i * 5) % 233 for i in range(frames * 2)]))
    return buffer.getvalue()


class ClipProvider(FakeProvider):
    """Returns one distinct PCM clip per synthesized segment."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        super().__init__()
        self.fail_on = fail_on

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(("synthesize", text))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise VoiceProviderError("upstream down")
        return pcm_clip(frames=len(self.calls) * 4)


def make_segmented_app(
    provider: FakeProvider,
    *,
    max_chars: int,
    max_segments: int = 8,
    budget: float = 120.0,
):
    settings = Settings(
        api_key="offline-test-key",
        voice_enabled=True,
        deployment_environment="local_dev",
        voice_tts_max_chars=max_chars,
        voice_tts_max_segments=max_segments,
        voice_tts_budget_seconds=budget,
    )
    return create_app(
        settings=settings,
        runtime_factory=lambda _: NoRuntime(),
        voice_provider=provider,
    )


class TestSegmentedSpeak:
    """Long answers are spoken in full instead of being cut at the TTS limit."""

    def test_short_text_still_makes_one_call(self) -> None:
        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=600)
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": "轴承温度上限85摄氏度。"})
        assert response.status_code == 200
        assert len(provider.calls) == 1
        assert response.headers.get("X-Voice-Segments") == "1"
        assert "X-Voice-Truncated" not in response.headers

    def test_long_text_is_split_into_several_upstream_calls(self) -> None:
        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": text})
        assert response.status_code == 200
        assert len(provider.calls) > 1
        assert all(len(call[1]) <= 20 for call in provider.calls)

    def test_segments_are_concatenated_into_one_playable_clip(self) -> None:
        import io
        import wave

        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": text})

        assert response.headers["Content-Type"] == "audio/wav"
        with wave.open(io.BytesIO(response.content), "rb") as handle:
            total_frames = handle.getnframes()
        # Clip i carries (i+1)*4 frames, so the merged file is the triangular sum.
        expected = sum((i + 1) * 4 for i in range(len(provider.calls)))
        assert total_frames == expected

    def test_no_content_is_lost_when_the_text_fits_the_segment_budget(self) -> None:
        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            client.post("/v1/voice/speak", json={"text": text})
        assert "".join(call[1] for call in provider.calls) == text

    def test_segment_budget_is_capped_and_marked(self) -> None:
        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=20, max_segments=2)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": text})
        assert len(provider.calls) == 2
        assert response.headers["X-Voice-Segments"] == "2"
        assert response.headers["X-Voice-Truncated"] == "true"

    def test_citation_markers_never_reach_the_upstream(self) -> None:
        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度上限85摄氏度。[1]测量位置为轴承支架外侧。[2]"
        with TestClient(app) as client:
            client.post("/v1/voice/speak", json={"text": text})
        spoken = "".join(call[1] for call in provider.calls)
        assert "[1]" not in spoken
        assert "[2]" not in spoken

    def test_units_are_spoken_in_every_segment(self) -> None:
        provider = ClipProvider()
        app = make_segmented_app(provider, max_chars=20)
        text = "振动速度有效值不超过7.1mm/s。压力为0.6MPa。"
        with TestClient(app) as client:
            client.post("/v1/voice/speak", json={"text": text})
        spoken = "".join(call[1] for call in provider.calls)
        assert "mm/s" not in spoken
        assert "MPa" not in spoken
        assert "毫米每秒" in spoken and "兆帕" in spoken

    def test_non_pcm_clips_fall_back_instead_of_returning_a_broken_file(self) -> None:
        """The existing fake answers with b"RIFFfakeaudio": not a real WAV."""
        provider = FakeProvider()
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": text})
        assert response.status_code == 200
        assert response.content == b"RIFFfakeaudio"
        assert response.headers["X-Voice-Truncated"] == "true"
        assert len(provider.calls) > 1

    def test_failure_part_way_through_serves_what_was_synthesized(self) -> None:
        """Segments already paid for are delivered instead of thrown away."""
        provider = ClipProvider(fail_on=2)
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": text})
        assert response.status_code == 200
        assert response.content == pcm_clip(frames=4)
        assert response.headers["X-Voice-Segments"] == "1"
        assert response.headers["X-Voice-Truncated"] == "true"
        # Nothing after the failure is attempted.
        assert len(provider.calls) == 2

    def test_failure_on_the_first_segment_is_reported_as_unavailable(self) -> None:
        provider = ClipProvider(fail_on=1)
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": text})
        assert response.status_code == 503
        assert response.json()["code"] == "VOICE_UPSTREAM_UNAVAILABLE"

    def test_mid_list_failure_logs_which_segment_gave_up(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        provider = ClipProvider(fail_on=3)
        app = make_segmented_app(provider, max_chars=20)
        text = "轴承温度不应超过85摄氏度。" * 6
        with (
            caplog.at_level(logging.WARNING, logger="industrial_rag.voice"),
            TestClient(app) as client,
        ):
            client.post("/v1/voice/speak", json={"text": text})
        messages = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
        assert any("segment 3/" in message for message in messages), messages


class _ClockProvider(ClipProvider):
    """Charges a fake clock per segment so the request budget is testable offline.

    Real timing matters here — a 600-char segment measured 88.7 s upstream — but
    a test cannot wait for that, so the clock advances on demand instead.
    """

    def __init__(self, *, clock: list[float], cost: float) -> None:
        super().__init__()
        self.clock = clock
        self.cost = cost

    async def synthesize(self, text: str) -> bytes:
        self.clock[0] += self.cost
        return await super().synthesize(text)


class TestRequestBudget:
    """One request cannot hold a browser for an unbounded number of segments."""

    LONG_TEXT = "轴承温度不应超过85摄氏度。" * 6

    def _run(self, monkeypatch, *, clock: list[float], cost: float, budget: float):
        provider = _ClockProvider(clock=clock, cost=cost)
        monkeypatch.setattr("industrial_rag.routers.voice.monotonic", lambda: clock[0])
        app = make_segmented_app(provider, max_chars=20, budget=budget)
        with TestClient(app) as client:
            response = client.post("/v1/voice/speak", json={"text": self.LONG_TEXT})
        return provider, response

    def test_a_segment_that_cannot_fit_the_budget_is_never_started(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 90 s already spent, the next segment is projected at ~2 s, budget 50 s.
        provider, response = self._run(
            monkeypatch, clock=[0.0], cost=90.0, budget=50.0
        )
        assert response.status_code == 200
        assert len(provider.calls) == 1
        assert response.headers["X-Voice-Segments"] == "1"
        assert response.headers["X-Voice-Truncated"] == "true"

    def test_a_budget_that_fits_every_segment_costs_nothing_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, response = self._run(
            monkeypatch, clock=[0.0], cost=2.0, budget=600.0
        )
        assert response.status_code == 200
        assert len(provider.calls) > 1
        assert "X-Voice-Truncated" not in response.headers

    def test_the_first_segment_is_always_synthesized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A budget smaller than one call still answers something."""
        provider, response = self._run(
            monkeypatch, clock=[0.0], cost=5.0, budget=0.001
        )
        assert response.status_code == 200
        assert len(provider.calls) == 1

    def test_budget_exhaustion_is_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        clock = [0.0]
        provider = _ClockProvider(clock=clock, cost=90.0)
        monkeypatch.setattr("industrial_rag.routers.voice.monotonic", lambda: clock[0])
        app = make_segmented_app(provider, max_chars=20, budget=50.0)
        with (
            caplog.at_level(logging.INFO, logger="industrial_rag.voice"),
            TestClient(app) as client,
        ):
            client.post("/v1/voice/speak", json={"text": self.LONG_TEXT})
        notes = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.INFO]
        assert any("voice segment budget" in note for note in notes), notes
