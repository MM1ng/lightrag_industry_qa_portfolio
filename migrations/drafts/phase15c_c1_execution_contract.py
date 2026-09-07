"""NON-ACTIVE migration draft for the Phase15-C1 model contract.

This file deliberately lives outside migrations/versions and has no revision
identifier. Alembic upgrade head does NOT apply it. Do not move it into the
revision chain without approving schema deployment and reviewing the current
head (the C1 baseline head is f15b0a1c2d3e).

No runtime rows are classified and no lifecycle enum is changed. Existing rows
keep NULL execution status. The ORM changes must NOT be deployed against a
database without these columns. create_all does not upgrade existing tables.
"""

import sqlalchemy as sa
from alembic import op

# Frozen SQL, intentionally independent of application model imports.
EXECUTION_VALUES_CHECK = (
    "execution_status IN ('PENDING', 'RUNNING', 'RECOVERY_REQUIRED', "
    "'SUCCEEDED', 'FAILED', 'CANCELLED')"
)
EXECUTION_PAIR_CHECK = """
execution_status IS NULL
OR (execution_status = 'PENDING' AND status = 'pending')
OR (execution_status = 'RUNNING' AND status IN ('claimed', 'running', 'building'))
OR (execution_status = 'RECOVERY_REQUIRED' AND status = 'recovery_required')
OR (execution_status = 'SUCCEEDED' AND status IN
    ('building', 'validating', 'ready', 'succeeded', 'failed', 'cancelled',
     'promoted', 'rolled_back'))
OR (execution_status = 'FAILED' AND status = 'failed')
OR (execution_status = 'CANCELLED' AND status = 'cancelled')
"""


def proposed_upgrade() -> None:
    """Review-only additive shape; do not execute against a project database."""
    with op.batch_alter_table("update_jobs") as batch_op:
        batch_op.add_column(sa.Column("execution_status", sa.String(17), nullable=True))
        batch_op.add_column(sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("execution_finished_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_update_jobs_execution_status", EXECUTION_VALUES_CHECK
        )
        batch_op.create_check_constraint(
            "ck_update_jobs_lifecycle_execution", EXECUTION_PAIR_CHECK
        )


def proposed_downgrade() -> None:
    """Keep additive columns on application rollback; never erase execution history."""
    raise RuntimeError("C1 draft: destructive schema downgrade is not approved")
