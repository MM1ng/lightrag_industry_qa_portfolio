"""In-process migrations must preserve existing application diagnostics."""
import logging

from alembic import command
from alembic.config import Config


def test_migration_keeps_existing_application_logger_enabled(tmp_path, monkeypatch):
    database = tmp_path / "logging-contract.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    logger = logging.getLogger("industrial_rag.api")
    previous_disabled = logger.disabled
    logger.disabled = False
    try:
        command.upgrade(Config("alembic.ini"), "head")
        assert logger.disabled is False
    finally:
        logger.disabled = previous_disabled
