"""Tests for WAV clip stitching used by segmented speech synthesis.

The answers this system produces are short — median 82 characters, observed
maximum 424 — but nothing guarantees it. Once an answer crosses the TTS input
limit the endpoint has two honest options: cut the text, or synthesize several
clips and hand back one playable file. Stitching is only safe when the clips
really are PCM WAVs with identical format parameters, so the rules below are
asserted explicitly; anything else must come back as "not stitchable" instead
of a corrupt download.
"""

from __future__ import annotations

import io
import wave

import pytest
from industrial_rag.voice_audio import is_stitchable, join_wav_clips, wav_clip


def pcm_clip(
    *,
    frames: int = 40,
    channels: int = 1,
    sample_rate: int = 24000,
    width: int = 2,
    amplitude: int = 3000,
) -> bytes:
    """One short sine-ish PCM clip, written through the stdlib so the bytes are real."""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes([(i * 7) % 251 for i in range(frames * channels * width)]))
    return buffer.getvalue()


class TestHappyPath:
    def test_single_clip_passes_through(self) -> None:
        clip = pcm_clip()
        assert join_wav_clips([clip]) == clip

    def test_two_clips_concatenate_their_frames(self) -> None:
        first, second = pcm_clip(frames=30), pcm_clip(frames=50)
        merged = join_wav_clips([first, second])
        with wave.open(io.BytesIO(merged), "rb") as handle:
            assert handle.getnframes() == 80
            assert handle.getnchannels() == 1
            assert handle.getframerate() == 24000

    def test_order_is_preserved(self) -> None:
        first, second = pcm_clip(frames=10), pcm_clip(frames=10)
        merged = join_wav_clips([first, second])
        a = _frames(first)
        b = _frames(second)
        assert _frames(merged) == a + b

    def test_empty_input_returns_empty_bytes(self) -> None:
        assert join_wav_clips([]) == b""

    def test_header_sizes_match_the_payload(self) -> None:
        merged = join_wav_clips([pcm_clip(frames=20), pcm_clip(frames=25)])
        assert merged[:4] == b"RIFF"
        assert merged[8:12] == b"WAVE"
        riff_size = int.from_bytes(merged[4:8], "little")
        assert riff_size == len(merged) - 8
        data_start = merged.find(b"data")
        data_size = int.from_bytes(merged[data_start + 4 : data_start + 8], "little")
        assert data_size == len(_frames(merged))

    def test_many_clips(self) -> None:
        clips = [pcm_clip(frames=8) for _ in range(7)]
        merged = join_wav_clips(clips)
        with wave.open(io.BytesIO(merged), "rb") as handle:
            assert handle.getnframes() == 56


class TestStreamedHeaders:
    """Reproduce what the upstream actually sends, because hand-made headers do not.

    ``qwen3-tts-flash`` does not know the length while it streams, so every clip
    it returns declares ``data`` as 2147483547 bytes (2147483583 for RIFF) no
    matter how short the audio really is. A parser that trusts that number
    rejects every real clip, which silently disables stitching — the fixtures
    below are built to that exact shape, measured from the downloaded files.
    """

    def test_placeholder_data_size_is_parsed_against_the_real_payload(self) -> None:
        clip = streamed(pcm_clip(frames=40))
        parsed = wav_clip(clip)
        assert parsed is not None
        assert len(parsed.frames) == 40 * 2

    def test_placeholder_clips_are_stitchable(self) -> None:
        assert is_stitchable([streamed(pcm_clip()), streamed(pcm_clip())]) is True

    def test_joined_output_carries_real_sizes_not_placeholders(self) -> None:
        first, second = streamed(pcm_clip(frames=30)), streamed(pcm_clip(frames=25))
        merged = join_wav_clips([first, second])
        assert merged is not None
        assert int.from_bytes(merged[4:8], "little") == len(merged) - 8
        assert int.from_bytes(merged[40:44], "little") == len(_frames(merged))
        with wave.open(io.BytesIO(merged), "rb") as handle:
            assert handle.getnframes() == 55

    def test_playable_by_the_stdlib_reader(self) -> None:
        merged = join_wav_clips([streamed(pcm_clip(frames=20)) for _ in range(3)])
        with wave.open(io.BytesIO(merged), "rb") as handle:
            assert handle.readframes(handle.getnframes())

    def test_a_lying_fmt_chunk_is_still_refused(self) -> None:
        """Only ``data`` may run to the end of the buffer; a short fmt is corruption."""
        clip = bytearray(pcm_clip())
        clip[20:24] = (16000).to_bytes(4, "little")  # fmt claims 16000 bytes
        assert wav_clip(bytes(clip)) is None

    def test_declared_data_smaller_than_payload_is_respected(self) -> None:
        clip = bytearray(pcm_clip(frames=40))
        clip[40:44] = (40).to_bytes(4, "little")  # only 40 bytes of the 80
        parsed = wav_clip(bytes(clip))
        assert parsed is not None
        assert len(parsed.frames) == 40


class TestFrameAlignment:
    def test_a_partial_trailing_frame_is_dropped(self) -> None:
        """Odd bytes would shift every following clip half a frame out of phase."""
        clip = bytearray(pcm_clip(frames=10))
        clip[40:44] = (21).to_bytes(4, "little")
        parsed = wav_clip(bytes(clip))
        assert parsed is not None
        assert len(parsed.frames) == 20

    def test_joined_frames_stay_whole(self) -> None:
        odd = bytearray(pcm_clip(frames=10))
        odd[40:44] = (21).to_bytes(4, "little")
        merged = join_wav_clips([bytes(odd), pcm_clip(frames=10)])
        assert merged is not None
        assert len(_frames(merged)) % 2 == 0


class TestRefusals:
    def test_non_wav_bytes_are_not_stitchable(self) -> None:
        clip = b"\x1a\x45\xdf\xa3" + b"0" * 40
        assert not is_stitchable([clip])
        assert join_wav_clips([clip, clip]) is None

    def test_a_single_clip_passes_through_without_parsing(self) -> None:
        """Nothing is being joined, so there is nothing to validate away."""
        clip = b"\x1a\x45\xdf\xa3" + b"0" * 40
        assert join_wav_clips([clip]) == clip

    def test_mixed_sample_rates_are_refused(self) -> None:
        clips = [pcm_clip(sample_rate=24000), pcm_clip(sample_rate=16000)]
        assert not is_stitchable(clips)
        assert join_wav_clips(clips) is None

    def test_mixed_channel_counts_are_refused(self) -> None:
        clips = [pcm_clip(channels=1), pcm_clip(channels=2)]
        assert join_wav_clips(clips) is None

    def test_mixed_sample_widths_are_refused(self) -> None:
        clips = [pcm_clip(width=2), pcm_clip(width=1)]
        assert join_wav_clips(clips) is None

    def test_truncated_clip_is_refused(self) -> None:
        clip = pcm_clip(frames=40)
        assert join_wav_clips([clip, clip[:20]]) is None

    def test_compressed_format_is_refused(self) -> None:
        """Only PCM (audio format 1) can be concatenated without decoding."""
        clip = bytearray(pcm_clip())
        body = _chunk_start(bytes(clip), b"fmt ") + 8
        clip[body : body + 2] = (3).to_bytes(2, "little")  # IEEE float, not PCM
        assert _fmt_tag(bytes(clip)) == 3
        assert is_stitchable([bytes(clip), bytes(clip)]) is False
        assert join_wav_clips([bytes(clip), bytes(clip)]) is None

    def test_empty_clip_list_entry_is_refused(self) -> None:
        assert join_wav_clips([pcm_clip(), b""]) is None


class TestIsStitchable:
    @pytest.mark.parametrize("clip", [b"", b"RIFF", b"RIFF\x00\x00\x00\x00WAVE"])
    def test_ragged_inputs_do_not_raise(self, clip: bytes) -> None:
        assert is_stitchable([clip]) is False

    def test_true_for_uniform_pcm_clips(self) -> None:
        assert is_stitchable([pcm_clip(), pcm_clip()]) is True


def streamed(clip: bytes) -> bytes:
    """Rewrite a clip's size fields the way the streaming upstream does."""

    patched = bytearray(clip)
    patched[4:8] = (2147483583).to_bytes(4, "little")
    patched[40:44] = (2147483547).to_bytes(4, "little")
    return bytes(patched)


def _frames(payload: bytes) -> bytes:
    with wave.open(io.BytesIO(payload), "rb") as handle:
        return handle.readframes(handle.getnframes())


def _chunk_start(payload: bytes, fourcc: bytes) -> int:
    """Offset of a chunk's fourcc; its size field is +4 and its body +8."""
    start = payload.find(fourcc)
    assert start != -1, fourcc
    return start


def _fmt_tag(payload: bytes) -> int:
    """Read the audio format field back out, so a patched clip is provably patched."""
    body = _chunk_start(payload, b"fmt ") + 8
    return int.from_bytes(payload[body : body + 2], "little")


def test_wav_clip_helper_produces_parseable_headers() -> None:
    """The exported parser must agree with the stdlib reader on our own output."""
    clip = pcm_clip(frames=12)
    parsed = wav_clip(clip)
    assert parsed is not None
    assert parsed.channels == 1
    assert parsed.sample_rate == 24000
    assert parsed.width == 2
    assert len(parsed.frames) == 12 * 1 * 2
    assert parsed.audio_format == 1
