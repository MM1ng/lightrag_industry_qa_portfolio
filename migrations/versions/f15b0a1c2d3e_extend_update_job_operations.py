"""extend update job operations for Phase15-B Step1

Revision ID: f15b0a1c2d3e
Revises: e2f3a4b5c6d7
Create Date: 2026-09-04

SQLite stores the existing operation enum as VARCHAR, so it accepts the new
values without an enum-table rewrite. PostgreSQL needs an additive ALTER TYPE.
The downgrade intentionally retains PostgreSQL enum values because removing
enum labels requires destructive type recreation and could invalidate jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f15b0a1c2d3e"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPARSE_DOCUMENT_CONSTRAINT = "ck_update_jobs_reparse_requires_document"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE updateoperation ADD VALUE IF NOT EXISTS 'reparse'")
        op.execute("ALTER TYPE updateoperation ADD VALUE IF NOT EXISTS 'reindex'")

    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.create_check_constraint(
            _REPARSE_DOCUMENT_CONSTRAINT,
            "operation != 'reparse' OR document_id IS NOT NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.drop_constraint(_REPARSE_DOCUMENT_CONSTRAINT, type_="check")

    # PostgreSQL enum labels are intentionally retained. Recreating the enum
    # to remove labels would be destructive to existing UpdateJob rows.
