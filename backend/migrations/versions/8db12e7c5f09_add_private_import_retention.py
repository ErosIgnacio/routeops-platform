"""Record retention expiry and private-object deletion without mutating imports.

Revision ID: 8db12e7c5f09
Revises: 525a2c8f3a8c
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8db12e7c5f09"
down_revision: str | None = "525a2c8f3a8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batch_expirations",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.CheckConstraint("reason = 'RETENTION'", name="ck_batch_expiration_reason"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    op.create_table(
        "import_file_deletions",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.CheckConstraint("reason = 'RETENTION'", name="ck_file_deletion_reason"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_batch_expirations.batch_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("file_id"),
    )
    for table in ("import_batch_expirations", "import_file_deletions"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION routeops_reject_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("import_file_deletions", "import_batch_expirations"):
        if connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {table})").scalar():
            raise RuntimeError(f"downgrade blocked: {table} contains retention history")
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.drop_table("import_file_deletions")
    op.drop_table("import_batch_expirations")
