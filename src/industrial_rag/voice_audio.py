"""PCM WAV stitching for segmented speech synthesis.

Long answers are synthesized in several clips and must reach the browser as one
playable file. Concatenating the raw bytes would not work: every clip carries
its own RIFF header, and the sizes in those headers would describe a file that
is not there. Only uncompressed PCM (audio format 1) is stitchable without
decoding, and only when the format parameters match, so anything else is
reported as "not stitchable" and the caller falls back to a smaller answer
rather than shipping a corrupt download.
"""

from __future__ import annotations

from dataclasses import dataclass

_PCM_FORMAT = 1
_MIN_FMT_CHUNK = 16


@dataclass(frozen=True, slots=True)
class WavClip:
    """Parsed view of one PCM WAV clip."""

    audio_format: int
    channels: int
    sample_rate: int
    width: int
    prefix: bytes
    frames: bytes

    @property
    def signature(self) -> tuple[int, int, int, int]:
        return (self.audio_format, self.channels, self.sample_rate, self.width)


def _chunks(data: bytes) -> list[tuple[bytes, int, int]]:
    """Read the chunk headers up to and including ``data``.

    Scanning stops at ``data``: everything past it is audio samples, and
    interpreting their bytes as further chunk headers would make a random
    waveform decide whether this clip is parseable.
    """

    found: list[tuple[bytes, int, int]] = []
    position = 12
    while position + 8 <= len(data):
        fourcc = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        body_start = position + 8
        body_end = body_start + size
        if body_end > len(data):
            if fourcc != b"data":
                # A header before the audio promises bytes the payload lacks.
                return []
            # Uncompressed streamed WAV cannot know its length up front, so the
            # upstream declares data as 2147483547 for a 160 KB clip. Read to the
            # end of what actually arrived; the size is rewritten on the way out.
            found.append((fourcc, body_start, len(data)))
            break
        found.append((fourcc, body_start, body_end))
        if fourcc == b"data":
            break
        position = body_end + (size % 2)
    return found


def wav_clip(data: bytes) -> WavClip | None:
    """Parse one PCM WAV clip, or return None when it cannot be stitched."""

    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None

    chunks = _chunks(data)
    if not chunks:
        return None

    fmt_body_end: int | None = None
    fmt: tuple[int, int, int, int] | None = None
    frames: bytes | None = None
    for fourcc, body_start, body_end in chunks:
        if fourcc == b"fmt ":
            if body_end - body_start < _MIN_FMT_CHUNK:
                return None
            body = data[body_start:body_end]
            audio_format = int.from_bytes(body[0:2], "little")
            if audio_format != _PCM_FORMAT:
                return None
            channels = int.from_bytes(body[2:4], "little")
            sample_rate = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
            if channels < 1 or sample_rate < 1 or bits < 1:
                return None
            fmt = (audio_format, channels, sample_rate, (bits + 7) // 8)
            fmt_body_end = body_end
        elif fourcc == b"data":
            frames = data[body_start:body_end]

    if fmt is None or frames is None or fmt_body_end is None:
        return None
    frame_bytes = fmt[1] * fmt[3]
    # A partial last frame would shift every following clip half a frame out of
    # phase, which plays as noise rather than failing loudly.
    frames = frames[: len(frames) - len(frames) % frame_bytes]
    return WavClip(
        audio_format=fmt[0],
        channels=fmt[1],
        sample_rate=fmt[2],
        width=fmt[3],
        prefix=data[:fmt_body_end],
        frames=frames,
    )


def is_stitchable(clips: list[bytes]) -> bool:
    """Report whether every clip parses and they all share one PCM format."""

    if not clips:
        return False
    parsed = [wav_clip(clip) for clip in clips]
    if any(clip is None for clip in parsed):
        return False
    signatures = {clip.signature for clip in parsed if clip is not None}
    return len(signatures) == 1


def join_wav_clips(clips: list[bytes]) -> bytes | None:
    """Concatenate PCM clips into one WAV file, or return None if unsafe.

    The header of the first clip is reused as the prefix, which keeps any
    encoder metadata ahead of ``data``; only the frame payload and the two
    length fields are rewritten.
    """

    if not clips:
        return b""
    if len(clips) == 1:
        return clips[0]
    if not is_stitchable(clips):
        return None

    parsed = [wav_clip(clip) for clip in clips]
    if any(clip is None for clip in parsed):
        return None
    typed = [clip for clip in parsed if clip is not None]

    first = typed[0]
    frames = b"".join(clip.frames for clip in typed)
    data_chunk = b"data" + len(frames).to_bytes(4, "little") + frames
    prefix = first.prefix
    riff_size = len(prefix) + len(data_chunk) - 8
    return prefix[:4] + riff_size.to_bytes(4, "little") + prefix[8:] + data_chunk
