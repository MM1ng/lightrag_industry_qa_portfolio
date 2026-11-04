"""Deterministic trust policy for structured LightRAG evidence."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from industrial_rag.citation_formatter import Citation, collect_citations

DOCUMENT_ALIASES = {
    "2196-ANSI-Manual-Chinese.pdf": frozenset({"2196", "summit", "2196-ansi-manual-chinese.pdf"}),
    "t1739cn.pdf": frozenset({"desmi", "t1739", "t1739cn.pdf"}),
}

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-_/.][a-z0-9]+)*|[\u3400-\u9fff]+")
_CJK_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]+")
# Structural particles only.  Content-forming characters (对/中/能/可/应/要) are
# deliberately excluded so domain nouns such as 对中, 性能, 可靠, 应用, 要求
# survive segmentation; splitting inside them would erase the very terms the
# evidence gate needs to match.
_CJK_BOUNDARY_PATTERN = re.compile(
    "[的是了吗呢吧啊着地和与及或并在干从向把被将当其也都还而又再很更最已未这那所]+"
)
# Chapter index words are filler in ordinary prose but are exactly the terms an
# operator searches with ("安装步骤", "拆卸注意").  Treating them as generic
# filler made installation/disassembly questions unmatchable, so they stay
# matchable while the remaining instructional fillers remain filtered.
_CJK_INDEX_TERMS = frozenset({"安装", "拆卸", "检修", "启动", "停机"})
# Answer-shape words describe how an answer should look, not what it is about.
# Left inside a CJK run they produce junk terms such as 装步 / 安装步, which
# then never match manual prose and starve the generator of evidence.
_CJK_SCAFFOLD_PATTERN = re.compile(
    r"(?:注意事项|注意什么|怎么办|排查|步骤|注意|事项|什么|方法|要求|说明|情况|过程|流程|时候|原因)+"
)
# Nouns naming the corpus itself rather than the question's subject.
_CJK_TOPIC_WORDS = frozenset({"泵", "水泵", "泵组", "设备", "机组", "系统", "手册"})
_SOURCE_HEADER_PATTERN = re.compile(r"\[\[INDUSTRIAL_RAG_SOURCE\b[^\]]*\]\]")
_PROVENANCE_LINE_PATTERN = re.compile(r"(?m)^\[来源：[^\r\n]*\][ \t]*\r?\n?")
_MAX_SELECTED = 3
_CJK_NGRAM_LENGTHS = (2, 3, 4)
_GENERIC_CJK_PHRASE_PATTERN = re.compile(
    # Longest alternative first: with 怎么 before 怎么办 the regex consumed only
    # 怎么 and left a stray 办 behind, which then became its own token.
    r"(?:具体操作步骤|设备维护周期|具体操作|操作步骤|请说明|如何进行|怎么办|请问|如何|怎么|进行)+"
)
_CONDITION_NORMALIZATIONS = {
    "过高": "高",
    "高": "高",
    "过低": "低",
    "低": "低",
}
_CONDITION_PATTERN = re.compile(
    "|".join(re.escape(term) for term in sorted(_CONDITION_NORMALIZATIONS, key=len, reverse=True))
)
_CONDITION_ACTION_PATTERN = re.compile(r"降低|提高")
_RESPONSE_CUE_PATTERN = re.compile(
    r"怎么办|怎么|如何|排查|诊断|处理|原因|检查|停机|更换|维修|维护|应|需要|建议|立即"
)
_CONDITION_CONTEXT_PATTERN = re.compile(
    r"发生|发现|出现|存在|异常|故障|损坏|失效|导致|造成|超过|低于|过高|过低|后|时"
)
_ACTIONABLE_EVIDENCE_PATTERN = re.compile(
    r"检查|测量|监测|确保|验证|清洗|更换|调整|修复|关闭|停机|观察|提供"
)
_NON_SUBSTANTIVE_CJK_BIGRAMS = frozenset(
    {
        "检查",
        "更换",
        "操作",
        "步骤",
        "维护",
        "保养",
        "安装",
        "运行",
        "停机",
        "处理",
        "确定",
        "使用",
        "进行",
        "需要",
        "应该",
        "说明",
        "要求",
        "方法",
        "怎么",
        "如何",
        "请问",
        "办法",
        "什么",
        "哪些",
        "设备",
        "系统",
        "情况",
        "问题",
        "原因",
        "解决",
        "防止",
        "发现",
        "立即",
        "通过",
        "手册",
        "册中",
        "型号",
        "品牌",
        "必须",
        "购买",
        "指定",
        "哪个",
        "两份",
        "设置",
        "配置",
    }
)
# Chapter index terms stay matchable; see _CJK_INDEX_TERMS.
_GENERIC_CJK_BIGRAMS = frozenset(_NON_SUBSTANTIVE_CJK_BIGRAMS - _CJK_INDEX_TERMS)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "how",
        "is",
        "or",
        "please",
        "the",
        "what",
        "which",
        "了",
        "什么",
        "哪些",
        "哪个",
        "吗",
        "呢",
        "如何",
        "是否",
        "的",
        "为什么",
        "怎么",
        "请问",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    citation: Citation
    text: str
    rank: int


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    allowed: bool
    routed_document: str | None
    selected: tuple[EvidenceCandidate, ...]


def select_evidence(question: str, payload: object, *, limit: int = 3, diversify: bool = False) -> EvidenceDecision:
    """Return traceable candidates that meet deterministic routing and overlap gates."""
    question_tokens = _tokens(question)
    question_conditions = _conditions(question)
    matched_documents = _matched_documents(question_tokens)
    routed_document = next(iter(matched_documents)) if len(matched_documents) == 1 else None
    candidates = _extract_candidates(payload)
    if matched_documents:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.citation.source_file in matched_documents
        ]
    if _RESPONSE_CUE_PATTERN.search(question):
        actionable = [
            candidate
            for candidate in candidates
            if _ACTIONABLE_EVIDENCE_PATTERN.search(candidate.text)
        ]
        if actionable:
            candidates = actionable
    scored = [
        (
            _overlap(question, question_tokens, question_conditions, candidate.text),
            candidate,
        )
        for candidate in candidates
    ]
    # _overlap already encodes the substantive two-character condition rule
    # (score 2 for a concrete fault term inside a troubleshooting context), so a
    # score of 2 or more admits without a second subject check.
    admitted = [
        (
            _admits(question, _shared_content_terms(question_tokens, candidate.text))
            if _conditions_agree(question_conditions, candidate.text)
            else False
        )
        or score >= 2
        for score, candidate in scored
    ]
    triples = [
        (score, ok, candidate) for (score, candidate), ok in zip(scored, admitted, strict=True)
    ]
    ranked = sorted(triples, key=lambda item: (-item[0], item[2].rank))
    if not diversify:
        selected = tuple(
            candidate for _score, admits, candidate in ranked if admits
        )[: min(max(limit, 0), _MAX_SELECTED)]
    else:
        selected_list: list[EvidenceCandidate] = []
        covered_tokens: set[str] = set()
        cap = min(max(limit, 0), _MAX_SELECTED)
        for _score, admits, candidate in ranked:
            if not admits or len(selected_list) >= cap:
                continue
            candidate_tokens = set(_tokens(candidate.text))
            # Prefer complementary evidence over duplicate chunks.  Original
            # LightRAG rank remains the deterministic tie-breaker.
            adds_coverage = bool(candidate_tokens - covered_tokens)
            if selected_list and not adds_coverage:
                continue
            selected_list.append(candidate)
            covered_tokens.update(candidate_tokens)
        selected = tuple(selected_list)
    if not selected:
        return EvidenceDecision(False, None, ())
    return EvidenceDecision(True, routed_document, selected)


def select_partial_evidence(
    question: str, payload: object, *, limit: int = 2, minimum_overlap: int = 1
) -> EvidenceDecision:
    """Select a narrow fallback set for partial answers when the strict gate fails."""
    question_tokens = _tokens(question)
    matched_documents = _matched_documents(question_tokens)
    candidates = _extract_candidates(payload)
    if matched_documents:
        candidates = [candidate for candidate in candidates if candidate.citation.source_file in matched_documents]
    scored = [
        (_overlap(question, question_tokens, _conditions(question), candidate.text), candidate)
        for candidate in candidates
    ]
    selected = tuple(
        candidate
        for overlap, candidate in sorted(scored, key=lambda item: (-item[0], item[1].rank))
        if overlap >= minimum_overlap
    )[:limit]
    return EvidenceDecision(bool(selected), next(iter(matched_documents)) if len(matched_documents) == 1 else None, selected)


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("阀门", "截断装置")
    tokens: set[str] = set()
    for token in _TOKEN_PATTERN.findall(normalized):
        if _CJK_TOKEN_PATTERN.fullmatch(token):
            tokens.update(_cjk_terms(token))
            continue
        if token in _STOPWORDS:
            continue
        tokens.add(token)
    return frozenset(tokens)


def _matched_documents(tokens: frozenset[str]) -> frozenset[str]:
    filename_matches = frozenset(
        document for document in DOCUMENT_ALIASES if document.casefold() in tokens
    )
    if filename_matches:
        return filename_matches
    return frozenset(document for document, aliases in DOCUMENT_ALIASES.items() if tokens & aliases)


def _extract_candidates(payload: object) -> list[EvidenceCandidate]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return []
    data = payload["data"]
    candidates: list[EvidenceCandidate] = []
    identity_indexes: dict[tuple[str, int, str], int] = {}
    for field in ("references", "chunks"):
        values = data.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            citations = collect_citations({"data": {"references": [], "chunks": [value]}})
            if not citations:
                continue
            citation = citations[0]
            candidate_text = _candidate_text(value.get("content"))
            identity = (citation.source_file, citation.page_number, citation.chunk_id)
            existing_index = identity_indexes.get(identity)
            if existing_index is None:
                identity_indexes[identity] = len(candidates)
                candidates.append(
                    EvidenceCandidate(
                        citation=citation,
                        text=candidate_text,
                        rank=len(candidates),
                    )
                )
            elif not candidates[existing_index].text.strip() and candidate_text.strip():
                existing = candidates[existing_index]
                candidates[existing_index] = EvidenceCandidate(
                    citation=existing.citation,
                    text=candidate_text,
                    rank=existing.rank,
                )
    return candidates


def _candidate_text(content: object) -> str:
    if not isinstance(content, str):
        return ""
    without_headers = _SOURCE_HEADER_PATTERN.sub("", content)
    return _PROVENANCE_LINE_PATTERN.sub("", without_headers).strip()


def _cjk_terms(token: str) -> frozenset[str]:
    """Return matchable terms for one CJK run, without cross-word fragments.

    A whole unsegmented run such as ``泵的安装步骤是什么`` used to yield every
    2-4 character window, so ``泵的``/``的安``/``装步``/``骤是`` competed with
    real words while ``安装`` was filtered out as filler.  None of those
    fragments ever occurs verbatim in manual prose, so a question about
    installation could not match installation text at all.  Splitting the run on
    structural particles and answer-shape scaffolding first keeps the windows
    inside word-like segments.
    """

    if token in _STOPWORDS:
        return frozenset()
    terms: set[str] = set()
    for segment in _cjk_segments(token):
        terms.update(_segment_terms(segment))
    return frozenset(terms)


def _cjk_segments(token: str) -> tuple[str, ...]:
    """Split one CJK run into word-like segments (see ``_cjk_terms``)."""

    normalized = _GENERIC_CJK_PHRASE_PATTERN.sub("", token)
    normalized = _CJK_SCAFFOLD_PATTERN.sub("", normalized)
    normalized = _CONDITION_PATTERN.sub(
        lambda match: _CONDITION_NORMALIZATIONS[match.group()], normalized
    )
    if not normalized:
        return ()
    return tuple(part for part in _CJK_BOUNDARY_PATTERN.split(normalized) if part)


def _segment_terms(segment: str) -> frozenset[str]:
    """Return the segment itself plus its substantive 2-4 character windows."""

    candidates: set[str] = {segment}
    if len(segment) >= 3:
        for length in _CJK_NGRAM_LENGTHS:
            for index in range(len(segment) - length + 1):
                candidates.add(segment[index : index + length])
    return frozenset(
        term
        for term in candidates
        if term not in _STOPWORDS
        and not any(
            term[index : index + 2] in _GENERIC_CJK_BIGRAMS
            for index in range(len(term) - 1)
        )
    )


def _subject_segments(question: str) -> frozenset[str]:
    """Return the question's subject segments, excluding corpus-wide nouns.

    Used to decide whether a single shared term is specific enough: ``泵体``
    alone must not answer ``泵体泄漏怎么办`` because 泄漏 is the missing half of
    the subject, while ``安装`` alone may answer ``泵的安装步骤是什么``.
    """

    segments = [
        segment
        for token in _TOKEN_PATTERN.findall(unicodedata.normalize("NFKC", question).casefold())
        if _CJK_TOKEN_PATTERN.fullmatch(token)
        for segment in _cjk_segments(token)
    ]
    return frozenset(
        segment
        for segment in segments
        if len(segment) >= 2 and segment not in _CJK_TOPIC_WORDS
    )


def _subject_head(question: str) -> str | None:
    """Return the head noun of the question's subject, if any.

    Chinese noun phrases are head-final: ``泵体泄漏`` heads on ``泄漏``, so a
    chunk that only says ``泵体`` describes the machine but never the leak the
    question is about.  ``泵的安装步骤是什么`` reduces to the single segment
    ``安装``, which is its own head.
    """

    segments = sorted(_subject_segments(question))
    if not segments:
        return None
    return segments[-1][-2:] if len(segments[-1]) > 2 else segments[-1]


def _conditions(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    state_text = _CONDITION_ACTION_PATTERN.sub("", normalized)
    return frozenset(
        _CONDITION_NORMALIZATIONS[match.group()]
        for match in _CONDITION_PATTERN.finditer(state_text)
    )


def _conditions_agree(
    question_conditions: frozenset[str], candidate_text: str
) -> bool:
    """Return False when the candidate states the opposite condition.

    ``轴承温度高`` must never be answered from ``温度低时检查冷却系统``: the
    numeric/token overlap is high while the meaning is reversed.
    """

    candidate_conditions = _conditions(candidate_text)
    if not question_conditions or not candidate_conditions:
        return True
    return not question_conditions.isdisjoint(candidate_conditions)


def _document_alias_tokens() -> frozenset[str]:
    """Alias words route a document and must not count as content overlap."""

    aliases = {document.casefold() for document in DOCUMENT_ALIASES}
    for values in DOCUMENT_ALIASES.values():
        aliases.update(alias.casefold() for alias in values)
    return frozenset(aliases)


_DOCUMENT_ALIAS_TOKENS = _document_alias_tokens()


def _shared_content_terms(
    question_tokens: frozenset[str], candidate_text: str
) -> frozenset[str]:
    """Shared terms that indicate content, excluding pure routing aliases."""

    return (question_tokens & _tokens(candidate_text)) - _DOCUMENT_ALIAS_TOKENS


def _admits(question: str, shared_terms: frozenset[str]) -> bool:
    """Decide whether the shared content terms justify answering from a chunk.

    Two independent content terms are convincing.  A single term is convincing
    only when it is the question's whole subject: ``安装`` may answer ``泵的
    安装步骤是什么``, but ``泵体`` must not answer ``泵体泄漏怎么办`` because the
    chunk never mentions the leak the question asks about.
    """

    if len(shared_terms) >= 2:
        return True
    if len(shared_terms) != 1:
        return False
    shared = next(iter(shared_terms))
    subject = _subject_segments(question)
    if len(subject) == 1 and subject == {shared}:
        return True
    # A single shared term must be the question's head noun (or inside it).
    # 泵体泄漏 asks about the leak, so sharing 泄漏 is enough while sharing only
    # 泵体 is not; 泵体漏油 needs 漏油, not 泵体.
    head = _subject_head(question)
    return head is not None and (shared == head or shared in head)


def _overlap(
    question: str,
    question_tokens: frozenset[str],
    question_conditions: frozenset[str],
    candidate_text: str,
) -> int:
    if not _conditions_agree(question_conditions, candidate_text):
        return 0
    shared_terms = question_tokens & _tokens(candidate_text)
    if len(shared_terms) == 1:
        term = next(iter(shared_terms))
        if (
            _CJK_TOKEN_PATTERN.fullmatch(term)
            and len(term) == 2
            and not question_conditions
            and _RESPONSE_CUE_PATTERN.search(question)
            and _RESPONSE_CUE_PATTERN.search(candidate_text)
            and _CONDITION_CONTEXT_PATTERN.search(candidate_text.replace(term, ""))
        ):
            return 2
    return len(shared_terms)
