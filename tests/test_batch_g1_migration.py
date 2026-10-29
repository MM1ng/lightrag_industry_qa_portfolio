"""G1 migration executes only against a temporary database."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from industrial_rag.db.models import KnowledgeBase, ValidationRun, VectorIndexGeneration
from sqlalchemy import create_engine, inspect, text


def test_g1_expand_migration_preserves_existing_active_and_historical_run(tmp_path, monkeypatch):
    path = tmp_path / "g1-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    config = Config("alembic.ini")
    command.upgrade(config, "c15c1b0a4e5f")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            connection.execute(
                KnowledgeBase.__table__.insert().values(
                    id="a" * 32,
                    name="existing",
                    workspace_path="old",
                    upload_path="old",
                    parsed_path="old",
                    active_vector_generation_id="b" * 32,
                )
            )
            connection.execute(
                VectorIndexGeneration.__table__.insert().values(
                    id="b" * 32,
                    knowledge_base_id="a" * 32,
                    backend="qdrant",
                    generation="old",
                    status="active",
                    workspace_path="old",
                    document_manifest_hash="1" * 64,
                    child_chunks_manifest_hash="2" * 64,
                    embedding_config_hash="3" * 64,
                    chunking_config_hash="4" * 64,
                    activated_at=now,
                )
            )
            connection.execute(
                ValidationRun.__table__.insert().values(
                    id="c" * 32,
                    knowledge_base_id="a" * 32,
                    generation_id="b" * 32,
                    status="failed",
                    golden_set_version="old",
                    golden_set_sha256="5" * 64,
                    runner_version="old",
                    app_git_commit="6" * 40,
                    configured_model="old",
                    strategy_fingerprint="7" * 64,
                    generation_manifest_hash="8" * 64,
                    qdrant_content_fingerprint="9" * 64,
                    document_registry_fingerprint="a" * 64,
                    generation_content_epoch=0,
                    actor="old",
                    expires_at=now + timedelta(hours=1),
                )
            )
            old_active = dict(
                connection.execute(text("SELECT * FROM vector_index_generations")).mappings().one()
            )
            old_run = dict(
                connection.execute(text("SELECT * FROM validation_runs")).mappings().one()
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            active = dict(
                connection.execute(text("SELECT * FROM vector_index_generations")).mappings().one()
            )
            run = dict(connection.execute(text("SELECT * FROM validation_runs")).mappings().one())
            assert {k: active[k] for k in old_active} == old_active
            assert {k: run[k] for k in old_run} == old_run
            assert active["accepted_policy_snapshot"] is None
            assert run["validation_policy_fingerprint"] is None and run["passed"] == 0
            assert (
                connection.execute(
                    text("SELECT active_vector_generation_id FROM knowledge_bases")
                ).scalar()
                == "b" * 32
            )
        columns = {c["name"]: c for c in inspect(engine).get_columns("validation_runs")}
        assert columns["qdrant_content_fingerprint"]["nullable"]
        assert {
            "policy_snapshot",
            "validation_policy_fingerprint",
            "effective_questions_sha256",
        } <= columns.keys()
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE vector_index_generations SET accepted_policy_snapshot='{}'")
            )
        with pytest.raises(RuntimeError, match="evidence-preserving"):
            command.downgrade(config, "c15c1b0a4e5f")
        assert "accepted_policy_snapshot" in {
            c["name"] for c in inspect(engine).get_columns("vector_index_generations")
        }
    finally:
        engine.dispose()
