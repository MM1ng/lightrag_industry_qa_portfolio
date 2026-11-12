"""G1 acceptance evidence; no historical approval or Active backfill.

Revision ID: g1a1b2c3d4e5
Revises: c15c1b0a4e5f
"""

import sqlalchemy as sa
from alembic import op

revision = "g1a1b2c3d4e5"
down_revision = "c15c1b0a4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vector_index_generations", sa.Column("accepted_policy_snapshot", sa.JSON(), nullable=True)
    )
    with op.batch_alter_table("validation_runs") as batch:
        batch.add_column(sa.Column("policy_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("validation_policy_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("effective_questions_sha256", sa.String(64), nullable=True))
        batch.alter_column("qdrant_content_fingerprint", existing_type=sa.String(64), nullable=True)


def downgrade():
    # Do not discard accepted regression anchors or G1 audit evidence.
    bind = op.get_bind()
    if (
        bind.execute(
            sa.text("SELECT count(*) FROM validation_runs WHERE policy_snapshot IS NOT NULL")
        ).scalar()
        or bind.execute(
            sa.text(
                "SELECT count(*) FROM vector_index_generations WHERE accepted_policy_snapshot IS NOT NULL"
            )
        ).scalar()
    ):
        raise RuntimeError(
            "G1 evidence exists; downgrade requires an explicit evidence-preserving rollback plan"
        )
    with op.batch_alter_table("validation_runs") as batch:
        batch.drop_column("effective_questions_sha256")
        batch.drop_column("validation_policy_fingerprint")
        batch.drop_column("policy_snapshot")
    op.drop_column("vector_index_generations", "accepted_policy_snapshot")
