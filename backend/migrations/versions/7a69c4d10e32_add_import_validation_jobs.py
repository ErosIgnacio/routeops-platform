"""Durable provisional validation context, lease and immutable report.

Revision ID: 7a69c4d10e32
Revises: 8db12e7c5f09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "7a69c4d10e32"
down_revision: str | None = "8db12e7c5f09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_validation_contexts",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("context_data", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("context_sha256 ~ '^[0-9a-f]{64}$'", name="ck_validation_context_hash"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    op.create_table(
        "import_validation_jobs",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("owner_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_validation_job_attempts"),
        sa.CheckConstraint(
            "(owner_token IS NULL AND lease_until IS NULL) OR "
            "(owner_token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_validation_job_lease",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_validation_contexts.batch_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    op.create_index(
        "ix_validation_jobs_due", "import_validation_jobs", ["lease_until", "updated_at"]
    )
    op.create_table(
        "import_validation_reports",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("package_sha256", sa.String(64), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(40), nullable=False),
        sa.Column("validator_version", sa.String(40), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("counts", JSONB(), nullable=False),
        sa.Column("checked_rules", JSONB(), nullable=False),
        sa.Column("deferred_rules", JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.CheckConstraint(
                f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"ck_validation_report_{suffix}"
            )
            for column, suffix in (
                ("package_sha256", "package"),
                ("context_sha256", "context"),
                ("report_sha256", "hash"),
            )
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_validation_contexts.batch_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    for table in ("import_validation_contexts", "import_validation_reports"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION routeops_reject_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "import_validation_reports",
        "import_validation_jobs",
        "import_validation_contexts",
    ):
        if connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {table})").scalar():
            raise RuntimeError(f"downgrade blocked: {table} contains validation history")
    for table in ("import_validation_reports", "import_validation_contexts"):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.drop_table("import_validation_reports")
    op.drop_index("ix_validation_jobs_due", table_name="import_validation_jobs")
    op.drop_table("import_validation_jobs")
    op.drop_table("import_validation_contexts")
