"""Deterministic text and audio helpers for the voice endpoints.

Everything here is a pure function so it can be tested without network or audio
hardware. The cleaning rules matter because raw answers carry citation markers
(``[1]``) that a TTS engine would read out loud as "square bracket one".
"""

from __future__ import annotations

import re

# Citation markers produced by the structured citation path.
_CITATION = re.compile(r"\[\d+\]")
# Markdown emphasis and code fences never belong in spoken text.
_MARKDOWN = re.compile(r"[*_`#>]+")
# Spoken forms for the units that actually appear in these manuals.
_UNIT_SPEECH = (
    ("°C", "摄氏度"),
    ("℃", "摄氏度"),
    ("°F", "华氏度"),
    ("mm/s", "毫米每秒"),
    ("m³/h", "立方米每小时"),
    ("mm", "毫米"),
    ("MPa", "兆帕"),
    ("kPa", "千帕"),
    ("rpm", "转每分钟"),
)
_WHITESPACE = re.compile(r"\s+")

_MIME_BY_KIND = {
    "wav": "audio/wav",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
}


def clean_for_speech(text: str, *, max_chars: int = 600) -> str:
    """Turn an API answer into text suitable for text-to-speech.

    Removes citation markers and markdown, expands units into their spoken
    Chinese form, collapses whitespace, and truncates to ``max_chars`` because
    the TTS model rejects longer input.
    """

    if not text:
        return ""
    cleaned = _CITATION.sub("", text)
    cleaned = _MARKDOWN.sub("", cleaned)
    for symbol, spoken in _UNIT_SPEECH:
        cleaned = cleaned.replace(symbol, spoken)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned[:max_chars]


def is_low_confidence(text: str) -> bool:
    """Report whether an ASR result is too short to be a real question.

    The ASR model does not reject unusable audio: sending random bytes returns a
    short hallucinated phrase such as "啊。". Anything with two or fewer
    non-whitespace characters is treated as noise so the UI can ask the user to
    retry instead of pasting it into the input box.
    """

    return len(text.strip()) <= 2


def sniff_audio_kind(data: bytes) -> str | None:
    """Identify the container from its magic bytes.

    The upstream service ignores the declared MIME type, so the server must
    decide for itself whether the payload is plausibly audio before spending a
    billable ASR call on it.
    """

    if len(data) < 4:
        return None
    if data.startswith(b"RIFF"):
        return "wav"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if data.startswith(b"OggS"):
        return "ogg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    return None


def mime_for_kind(kind: str) -> str:
    """Map a sniffed container kind to the MIME type sent upstream."""

    return _MIME_BY_KIND.get(kind, "audio/wav")
