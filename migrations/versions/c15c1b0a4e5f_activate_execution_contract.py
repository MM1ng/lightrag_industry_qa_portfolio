"""activate Phase15-C1 execution contract

Revision ID: c15c1b0a4e5f
Revises: f15b0a1c2d3e
Create Date: 2026-09-07

This is expand-only: existing Phase15-B jobs remain unclassified with a NULL
execution_status.  It does not alter lifecycle status values or jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c15c1b0a4e5f"
down_revision: str | Sequence[str] | None = "f15b0a1c2d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALUES = "execution_status IS NULL OR execution_status IN ('PENDING', 'RUNNING', 'RECOVERY_REQUIRED', 'SUCCEEDED', 'FAILED', 'CANCELLED')"
_PAIRS = """
execution_status IS NULL
OR (execution_status = 'PENDING' AND status = 'pending')
OR (execution_status = 'RUNNING' AND status IN ('claimed', 'running', 'building'))
OR (execution_status = 'RECOVERY_REQUIRED' AND status = 'recovery_required')
OR (execution_status = 'SUCCEEDED' AND status IN ('building', 'validating', 'ready', 'succeeded', 'failed', 'cancelled', 'promoted', 'rolled_back'))
OR (execution_status = 'FAILED' AND status = 'failed')
OR (execution_status = 'CANCELLED' AND status = 'cancelled')
"""


def upgrade() -> None:
    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.add_column(sa.Column("execution_status", sa.String(17), nullable=True))
        batch_op.add_column(sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("execution_finished_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint("ck_update_jobs_execution_status", _VALUES)
        batch_op.create_check_constraint("ck_update_jobs_lifecycle_execution", _PAIRS)


def downgrade() -> None:
    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.drop_constraint("ck_update_jobs_lifecycle_execution", type_="check")
        batch_op.drop_constraint("ck_update_jobs_execution_status", type_="check")
        batch_op.drop_column("execution_finished_at")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("next_run_at")
        batch_op.drop_column("execution_status")
