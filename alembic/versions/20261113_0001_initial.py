"""Create persisted migration jobs and typed-category job records."""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("created", sa.Float(), nullable=False),
        sa.Column("updated", sa.Float(), nullable=False),
        sa.Column("started", sa.Float()),
        sa.Column("finished", sa.Float()),
        sa.Column("owner", sa.String(32)),
        sa.Column("lease_until", sa.Float()),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("pr_url", sa.String(500)),
    )
    op.create_index("ix_jobs_state", "jobs", ["state"])
    op.create_index("ix_jobs_created", "jobs", ["created"])
    op.create_table(
        "job_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(32),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("created", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_job_records_job_id", "job_records", ["job_id"])
    op.create_index("ix_job_records_kind", "job_records", ["kind"])


def downgrade() -> None:
    op.drop_table("job_records")
    op.drop_table("jobs")
