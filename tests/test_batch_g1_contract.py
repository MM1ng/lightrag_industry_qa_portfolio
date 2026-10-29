"""G1 acceptance contracts; all inputs are local fixtures, never a Provider."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from industrial_rag.config import Settings
from industrial_rag.services.generation_content_fingerprint import stable_hash
from industrial_rag.services.golden_set_policy import load_canonical_policy
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def assess_policy(*args, **kwargs):
    from industrial_rag.services.validation_policy import assess_policy as assess

    return assess(*args, **kwargs)


def admit_bootstrap_suite(*args, **kwargs):
    from industrial_rag.services.validation_policy import admit_bootstrap_suite as admit

    return admit(*args, **kwargs)


def test_empty_runner_rates_are_not_measured(tmp_path):
    from dataclasses import replace

    from industrial_rag.services.canonical_validation_runner import CanonicalValidationRunner

    runner = CanonicalValidationRunner(
        Settings(api_key="fake", working_dir=tmp_path),
        replace(load_canonical_policy(), questions=()),
    )
    report = runner._summarize([])
    assert report["positive_citation_match_rate"] is None
    assert report["false_rejection_rate"] is None
    assert report["golden_subset_regression"] is False


def suite_fixture():
    text = "\n".join(f"Fact {i} has value {i}." for i in range(18))
    doc = {"document_name": "new.pdf", "file_hash": "a" * 64, "document_version": 1}
    record = {
        "chunk_id": "c1",
        "document_name": "new.pdf",
        "page_start": 1,
        "page_end": 1,
        "content": text,
    }
    rows = []
    for i in range(18):
        support = f"Fact {i} has value {i}."
        rows.append(
            {
                "id": f"P{i}",
                "question": f"What is fact {i}?",
                "expects_evidence": True,
                "fact_id": f"f{i}",
                "topic": f"topic{i // 6}",
                "reference_fact": support,
                "authored_by": "author",
                "reviewed_by": "reviewer",
                "reviewed_at": "2026-10-12T00:00:00Z",
                "approved": True,
                "expected_citations": [
                    {
                        "source_file": "new.pdf",
                        "page_number": 1,
                        "file_sha256": doc["file_hash"],
                        "document_version": 1,
                        "chunk_id": "c1",
                        "support_text": support,
                        "support_text_sha256": stable_hash(support),
                    }
                ],
            }
        )
    for i, kind in enumerate(("missing_detail", "unsupported_assertion")):
        rows.append(
            {
                "id": f"N{i}",
                "question": f"Missing item {i}?",
                "expects_evidence": False,
                "expected_citations": [],
                "negative_kind": kind,
                "rationale": f"All pages reviewed; item {i} absent.",
                "checked_documents": [doc],
                "authored_by": "author",
                "reviewed_by": "reviewer",
                "reviewed_at": "2026-10-12T00:00:00Z",
                "approved": True,
            }
        )
    return rows, [doc], [record]


def test_fonst_legacy_is_not_applicable_and_has_no_measured_rates():
    decision = assess_policy(load_canonical_policy(), {"FONST1000A1.pdf"}, bootstrap=True)
    assert decision["applicability"] == "NOT_APPLICABLE"
    assert decision["admission"] == "INSUFFICIENT_ACCEPTANCE_DATA"
    assert decision["positive_count"] == 0


def test_small_subset_is_insufficient_not_quality_failure():
    policy = load_canonical_policy()
    sources = {c["source_file"] for q in policy.questions[:1] for c in q["expected_citations"]}
    decision = assess_policy(policy, sources, bootstrap=True)
    assert decision["admission"] != "ADMITTED"


def test_active_keeps_all_twenty_even_if_candidate_loses_document():
    decision = assess_policy(load_canonical_policy(), set(), bootstrap=False)
    assert decision["admission"] == "ADMITTED"
    assert decision["positive_count"] == 18
    assert decision["negative_count"] == 2


def test_audited_suite_admits_independent_facts_and_both_negative_types():
    rows, docs, records = suite_fixture()
    assert admit_bootstrap_suite(rows, docs, records) == []


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_fact",
        "same_reviewer",
        "wrong_hash",
        "missing_negative_review",
        "bad_support",
        "missing_topic",
    ],
)
def test_bad_suite_is_rejected(change):
    rows, docs, records = suite_fixture()
    rows = deepcopy(rows)
    if change == "duplicate_fact":
        rows[1]["fact_id"] = rows[0]["fact_id"]
    if change == "same_reviewer":
        rows[0]["reviewed_by"] = rows[0]["authored_by"]
    if change == "wrong_hash":
        rows[0]["expected_citations"][0]["file_sha256"] = "b" * 64
    if change == "missing_negative_review":
        rows[-1]["checked_documents"] = []
    if change == "bad_support":
        rows[0]["expected_citations"][0]["support_text"] = "not present"
    if change == "missing_topic":
        rows[0]["topic"] = ""
    assert admit_bootstrap_suite(rows, docs, records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///file:g1memory?mode=memory&cache=shared&uri=true",
    ],
)
async def test_integrity_checks_bound_connection_even_when_env_changes(url, monkeypatch, tmp_path):
    from industrial_rag.services.generation_validation_service import GenerationValidationService

    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            monkeypatch.setenv("DATABASE_URL", "postgresql://wrong.invalid/db")
            service = GenerationValidationService(
                session, settings=Settings(api_key="fake", working_dir=tmp_path)
            )
            gate = await service._db_integrity()
            assert gate["status"] == "PASS"
            assert gate["passed"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsupported_db_is_skipped_and_never_passes(tmp_path):
    from industrial_rag.services.generation_validation_service import GenerationValidationService

    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )
    gate = await GenerationValidationService(
        session, settings=Settings(api_key="fake", working_dir=tmp_path)
    )._db_integrity()
    assert gate["status"] == "SKIPPED"
    assert gate["passed"] is False


@pytest.mark.asyncio
async def test_integrity_reads_all_rows_instead_of_accepting_first_ok(tmp_path):
    from industrial_rag.services.generation_validation_service import GenerationValidationService

    class Connection:
        async def exec_driver_sql(self, query):
            rows = (
                [(0, "main", "private.db")]
                if query == "PRAGMA database_list"
                else [("ok",), ("corrupt page",)]
            )
            return SimpleNamespace(all=lambda: rows)

    class Session:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        async def connection(self):
            return Connection()

    gate = await GenerationValidationService(
        Session(), settings=Settings(api_key="fake", working_dir=tmp_path)
    )._db_integrity()
    assert gate["status"] == "FAIL" and gate["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["chinese_path", "query_path", "relative_path"])
async def test_sqlite_file_url_checks_actual_database(kind, tmp_path, monkeypatch):
    from industrial_rag.services.generation_validation_service import GenerationValidationService

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "中文 空格.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    if kind == "query_path":
        url += "?timeout=2"
    if kind == "relative_path":
        url = "sqlite+aiosqlite:///relative.db"
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            gate = await GenerationValidationService(
                session, settings=Settings(api_key="fake", working_dir=tmp_path)
            )._db_integrity()
            assert gate["status"] == "PASS"
    finally:
        await engine.dispose()


def test_empty_database_url_uses_default_and_sync_url_preserves_query(monkeypatch):
    from industrial_rag.db import session

    monkeypatch.setattr(session, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("DATABASE_URL", " ")
    assert session._database_url() == session.DEFAULT_DB_URL
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///file:shared?mode=memory&uri=true")
    assert session.get_sync_url() == "sqlite:///file:shared?mode=memory&uri=true"


def test_validate_request_cannot_supply_approval_or_thresholds():
    from industrial_rag.routers.schemas import GenerationValidateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerationValidateRequest.model_validate(
            {"suite_id": "newdomain", "passed": True, "positive_matches": 1}
        )
