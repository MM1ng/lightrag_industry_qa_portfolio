"""Tests for speech-text cleaning and audio sniffing."""

from __future__ import annotations

import pytest
from industrial_rag.voice_text import (
    clean_for_speech,
    is_low_confidence,
    sniff_audio_kind,
)


class TestCleanForSpeech:
    def test_strips_citation_markers(self) -> None:
        assert clean_for_speech("温度不超过85°C。[1]") == "温度不超过85摄氏度。"

    def test_strips_multiple_citation_markers(self) -> None:
        text = "确保轴承温度不超过85°C（在轴承支架外测量）。[1]对于直连结构，按铭牌要求。[2]"
        assert "[1]" not in clean_for_speech(text)
        assert "[2]" not in clean_for_speech(text)

    def test_converts_degree_celsius(self) -> None:
        assert "摄氏度" in clean_for_speech("温度 85°C")
        assert "°C" not in clean_for_speech("温度 85°C")

    def test_converts_millimetre(self) -> None:
        assert "毫米" in clean_for_speech("振动 ≤7 mm/s")

    def test_strips_markdown_emphasis(self) -> None:
        assert clean_for_speech("**重要**：先停机") == "重要：先停机"

    def test_collapses_whitespace(self) -> None:
        assert clean_for_speech("温度   85\n\n摄氏度") == "温度 85 摄氏度"

    def test_truncates_at_max_chars(self) -> None:
        text = "字" * 700
        result = clean_for_speech(text, max_chars=600)
        assert len(result) <= 600
        assert text.startswith(result)

    def test_empty_input_returns_empty(self) -> None:
        assert clean_for_speech("") == ""
        assert clean_for_speech("   ") == ""

    def test_only_citations_returns_empty(self) -> None:
        assert clean_for_speech("[1][2]") == ""


class TestIsLowConfidence:
    def test_two_char_noise_is_low_confidence(self) -> None:
        assert is_low_confidence("啊。") is True

    def test_single_char_is_low_confidence(self) -> None:
        assert is_low_confidence("嗯") is True

    def test_empty_is_low_confidence(self) -> None:
        assert is_low_confidence("") is True
        assert is_low_confidence("   ") is True

    def test_normal_question_is_not_low_confidence(self) -> None:
        assert is_low_confidence("泵的轴承温度上限是多少？") is False

    def test_three_chars_is_not_low_confidence(self) -> None:
        assert is_low_confidence("你好吗") is False


class TestSniffAudioKind:
    def test_detects_wav(self) -> None:
        assert sniff_audio_kind(b"RIFF\x00\x00\x00\x00WAVE") == "wav"

    def test_detects_webm(self) -> None:
        assert sniff_audio_kind(b"\x1a\x45\xdf\xa3rest") == "webm"

    def test_detects_ogg(self) -> None:
        assert sniff_audio_kind(b"OggS\x00\x02") == "ogg"

    def test_detects_mp4(self) -> None:
        assert sniff_audio_kind(b"\x00\x00\x00\x20ftypM4A ") == "mp4"

    def test_random_bytes_returns_none(self) -> None:
        assert sniff_audio_kind(b"\xde\xad\xbe\xef" * 10) is None

    def test_empty_returns_none(self) -> None:
        assert sniff_audio_kind(b"") is None

    def test_too_short_returns_none(self) -> None:
        assert sniff_audio_kind(b"RI") is None

    @pytest.mark.parametrize("kind,mime", [
        ("wav", "audio/wav"),
        ("webm", "audio/webm"),
        ("ogg", "audio/ogg"),
        ("mp4", "audio/mp4"),
    ])
    def test_kind_maps_to_mime(self, kind: str, mime: str) -> None:
        from industrial_rag.voice_text import mime_for_kind
        assert mime_for_kind(kind) == mime
