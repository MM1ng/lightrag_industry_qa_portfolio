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

# Sentence endings that are safe to cut on. A full stop is deliberately absent:
# these manuals are full of measurements such as 0.6MPa and 3.5mm, and cutting
# on "." would split numbers in half.
_SENTENCE_ENDS = "。！？；!?;"

_MIME_BY_KIND = {
    "wav": "audio/wav",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
}


def clean_for_speech(text: str, *, max_chars: int | None = 600) -> str:
    """Turn an API answer into text suitable for text-to-speech.

    Removes citation markers and markdown, expands units into their spoken
    Chinese form, and collapses whitespace.

    ``max_chars`` keeps the original truncating behaviour for callers that pass
    a number. The speak endpoint now leaves it unset and segments the full text
    instead: answers were median 82 and at most 424 characters in the observed
    corpus, so truncation never actually fired — it was a silent cut waiting
    for a longer answer.
    """

    if not text:
        return ""
    cleaned = _CITATION.sub("", text)
    cleaned = _MARKDOWN.sub("", cleaned)
    for symbol, spoken in _UNIT_SPEECH:
        cleaned = cleaned.replace(symbol, spoken)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if max_chars is None:
        return cleaned
    return cleaned[:max_chars]


def _split_sentences(text: str) -> list[str]:
    """Split on sentence endings, keeping each terminator with its own sentence."""

    sentences: list[str] = []
    current: list[str] = []
    for char in text:
        current.append(char)
        if char in _SENTENCE_ENDS:
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
    tail = "".join(current).strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_for_speech(
    text: str,
    *,
    max_chars: int = 600,
    max_segments: int = 8,
) -> list[str]:
    """Cut cleaned speech text into chunks the TTS endpoint will accept.

    Sentences are packed up to ``max_chars`` so a short answer still produces
    one call; a single sentence longer than the limit is wrapped hard rather
    than dropped. ``max_segments`` bounds the billed work per request, so an
    unexpectedly long answer costs a known maximum instead of an open-ended one.
    """

    if not text or max_chars < 1:
        return []

    segments: list[str] = []
    current = ""
    for sentence in _split_sentences(text):
        if len(sentence) > max_chars:
            if current:
                segments.append(current)
                current = ""
            segments.extend(
                sentence[start : start + max_chars]
                for start in range(0, len(sentence), max_chars)
            )
            continue
        if not current:
            current = sentence
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            segments.append(current)
            current = sentence
    if current:
        segments.append(current)
    return segments[:max_segments]


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
