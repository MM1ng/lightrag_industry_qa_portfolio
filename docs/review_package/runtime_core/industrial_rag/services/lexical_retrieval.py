"""Dependency-free lexical primitives for frozen ChildChunk snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_LEXICAL_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*|[\u3400-\u9fff]+")
LEXICAL_INDEX_SCHEMA_VERSION = 1
_MAX_CJK_SUBTERM_LENGTH = 8
_LEXICAL_INDEX_FIELDS = {
    "schema_version",
    "generation_id",
    "child_manifest_hash",
    "child_count",
    "document_lengths",
    "postings",
    "artifact_hash",
}


@dataclass(frozen=True, slots=True)
class LexicalSearchResult:
    child_chunk_id: str
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class LexicalIndexArtifact:
    """Serializable postings and statistics, without duplicated ChildChunk metadata."""

    generation_id: str
    child_manifest_hash: str
    child_count: int
    document_lengths: dict[str, int]
    postings: dict[str, tuple[tuple[str, int], ...]]
    artifact_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": LEXICAL_INDEX_SCHEMA_VERSION,
            "generation_id": self.generation_id,
            "child_manifest_hash": self.child_manifest_hash,
            "child_count": self.child_count,
            "document_lengths": dict(sorted(self.document_lengths.items())),
            "postings": {
                term: [
                    {"child_chunk_id": child_chunk_id, "term_frequency": term_frequency}
                    for child_chunk_id, term_frequency in entries
                ]
                for term, entries in sorted(self.postings.items())
            },
            "artifact_hash": self.artifact_hash,
        }


def tokenize(text: str) -> tuple[str, ...]:
    """Return deterministic lexical tokens without discarding manual identifiers.

    CJK runs remain intact and also contribute contiguous subterms, allowing a
    term such as ``机械密封`` to match when it appears inside a longer sentence.
    """
    tokens: list[str] = []
    for match in _LEXICAL_TOKEN.finditer(text):
        token = match.group(0)
        tokens.append(token)
        if _contains_cjk(token):
            tokens.extend(_cjk_subterms(token))
    return tuple(tokens)


class BM25Index:
    """Small in-process BM25 index keyed exclusively by canonical child IDs."""

    def __init__(
        self,
        *,
        postings: Mapping[str, Mapping[str, int]],
        document_lengths: Mapping[str, int],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._postings = {term: dict(items) for term, items in postings.items()}
        self._document_lengths = dict(document_lengths)
        self._k1 = k1
        self._b = b
        self._average_document_length = (
            sum(self._document_lengths.values()) / len(self._document_lengths)
            if self._document_lengths
            else 0.0
        )

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, object]]) -> BM25Index:
        postings: dict[str, dict[str, int]] = defaultdict(dict)
        document_lengths: dict[str, int] = {}
        for record in records:
            child_chunk_id = str(record.get("chunk_id") or "").strip()
            if not child_chunk_id:
                raise ValueError("lexical records require chunk_id")
            if child_chunk_id in document_lengths:
                raise ValueError(f"duplicate child_chunk_id in lexical index: {child_chunk_id}")
            text = str(record.get("content") or record.get("embedding_content") or "")
            frequencies = Counter(tokenize(text))
            document_lengths[child_chunk_id] = sum(frequencies.values())
            for term, frequency in frequencies.items():
                postings[term][child_chunk_id] = frequency
        return cls(postings=postings, document_lengths=document_lengths)

    @classmethod
    def from_artifact(cls, artifact: LexicalIndexArtifact) -> BM25Index:
        return cls(
            postings={
                term: {child_chunk_id: frequency for child_chunk_id, frequency in entries}
                for term, entries in artifact.postings.items()
            },
            document_lengths=artifact.document_lengths,
        )

    def search(self, query: str, *, limit: int = 10) -> tuple[LexicalSearchResult, ...]:
        if limit <= 0 or not self._document_lengths:
            return ()
        scores: dict[str, float] = defaultdict(float)
        document_count = len(self._document_lengths)
        for term in dict.fromkeys(tokenize(query)):
            postings = self._postings.get(term)
            if not postings:
                continue
            document_frequency = len(postings)
            inverse_document_frequency = math.log(
                1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            for child_chunk_id, term_frequency in postings.items():
                document_length = self._document_lengths[child_chunk_id]
                normalization = self._k1 * (
                    1.0 - self._b + self._b * document_length / self._average_document_length
                )
                scores[child_chunk_id] += inverse_document_frequency * (
                    term_frequency * (self._k1 + 1.0) / (term_frequency + normalization)
                )
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return tuple(
            LexicalSearchResult(child_chunk_id=child_chunk_id, score=score, rank=rank)
            for rank, (child_chunk_id, score) in enumerate(ranked, 1)
        )


def _contains_cjk(token: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in token)


def _cjk_subterms(token: str) -> tuple[str, ...]:
    return tuple(
        token[start:end]
        for start in range(len(token))
        for end in range(start + 2, min(len(token), start + _MAX_CJK_SUBTERM_LENGTH) + 1)
        if end - start < len(token)
    )


def build_lexical_index(
    records: Iterable[Mapping[str, object]], *, generation_id: str, child_manifest_hash: str
) -> LexicalIndexArtifact:
    """Build deterministic postings for the immutable canonical child snapshot."""
    if not generation_id or not child_manifest_hash:
        raise ValueError("lexical index requires generation_id and child_manifest_hash")
    index = BM25Index.from_records(records)
    postings = {
        term: tuple(sorted(entries.items())) for term, entries in sorted(index._postings.items())
    }
    body = {
        "schema_version": LEXICAL_INDEX_SCHEMA_VERSION,
        "generation_id": generation_id,
        "child_manifest_hash": child_manifest_hash,
        "child_count": len(index._document_lengths),
        "document_lengths": dict(sorted(index._document_lengths.items())),
        "postings": {
            term: [
                {"child_chunk_id": child_chunk_id, "term_frequency": term_frequency}
                for child_chunk_id, term_frequency in entries
            ]
            for term, entries in postings.items()
        },
    }
    return LexicalIndexArtifact(
        generation_id=generation_id,
        child_manifest_hash=child_manifest_hash,
        child_count=len(index._document_lengths),
        document_lengths=dict(sorted(index._document_lengths.items())),
        postings=postings,
        artifact_hash=_hash_payload(body),
    )


def lexical_index_bytes(artifact: LexicalIndexArtifact) -> bytes:
    return _canonical_json_bytes(artifact.to_dict()) + b"\n"


def load_lexical_index(payload: bytes) -> LexicalIndexArtifact:
    """Parse and validate the lexical artifact's self-contained hash contract."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("lexical index is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError("lexical index must be an object")
    try:
        if set(value) != _LEXICAL_INDEX_FIELDS:
            raise ValueError
        schema_version = int(value["schema_version"])
        child_count = int(value["child_count"])
        document_lengths_raw = value["document_lengths"]
        postings_raw = value["postings"]
        artifact_hash = str(value["artifact_hash"])
        if (
            schema_version != LEXICAL_INDEX_SCHEMA_VERSION
            or child_count < 0
            or not isinstance(document_lengths_raw, Mapping)
            or not isinstance(postings_raw, Mapping)
        ):
            raise ValueError
        document_lengths = {
            str(child_chunk_id): int(length)
            for child_chunk_id, length in document_lengths_raw.items()
        }
        if any(
            not child_chunk_id or length < 0 for child_chunk_id, length in document_lengths.items()
        ):
            raise ValueError
        postings: dict[str, tuple[tuple[str, int], ...]] = {}
        for term, entries_raw in postings_raw.items():
            if not isinstance(term, str) or not term or not isinstance(entries_raw, list):
                raise ValueError
            entries: list[tuple[str, int]] = []
            for entry in entries_raw:
                if not isinstance(entry, Mapping):
                    raise ValueError
                child_chunk_id = str(entry["child_chunk_id"])
                frequency = int(entry["term_frequency"])
                if not child_chunk_id or frequency <= 0:
                    raise ValueError
                entries.append((child_chunk_id, frequency))
            if entries != sorted(entries) or len({entry[0] for entry in entries}) != len(entries):
                raise ValueError
            postings[term] = tuple(entries)
        body = {key: item for key, item in value.items() if key != "artifact_hash"}
        if _hash_payload(body) != artifact_hash:
            raise ValueError("lexical index artifact hash does not match")
        artifact = LexicalIndexArtifact(
            generation_id=str(value["generation_id"]),
            child_manifest_hash=str(value["child_manifest_hash"]),
            child_count=child_count,
            document_lengths=document_lengths,
            postings=postings,
            artifact_hash=artifact_hash,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("lexical index is invalid") from error
    return artifact


def validate_lexical_index(
    artifact: LexicalIndexArtifact,
    records: Iterable[Mapping[str, object]],
    *,
    generation_id: str,
    child_manifest_hash: str,
) -> None:
    """Verify every posting and length against the frozen canonical snapshot."""
    if artifact.generation_id != generation_id:
        raise ValueError("lexical index generation id does not match")
    if artifact.child_manifest_hash != child_manifest_hash:
        raise ValueError("lexical index child manifest hash does not match")
    expected = build_lexical_index(
        records, generation_id=generation_id, child_manifest_hash=child_manifest_hash
    )
    if artifact.child_count != expected.child_count:
        raise ValueError("lexical index child count does not match snapshot")
    if artifact.document_lengths != expected.document_lengths:
        raise ValueError("lexical index document lengths do not match snapshot")
    if artifact.postings != expected.postings:
        raise ValueError("lexical index postings do not match snapshot")


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _hash_payload(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


__all__ = [
    "LEXICAL_INDEX_SCHEMA_VERSION",
    "BM25Index",
    "LexicalIndexArtifact",
    "LexicalSearchResult",
    "build_lexical_index",
    "lexical_index_bytes",
    "load_lexical_index",
    "tokenize",
    "validate_lexical_index",
]
