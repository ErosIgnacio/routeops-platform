"""Immutable diagnostic documents committed with processing outcome.

Revision ID: a71c9e3d602b
Revises: f6b2d8a4c190
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a71c9e3d602b"
down_revision: str | None = "f6b2d8a4c190"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_diagnostics",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("calculation_version", sa.String(30), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
        sa.Column("owner_token", sa.Uuid()),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["planning_runs.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_diagnostics_hash"),
        sa.CheckConstraint("attempt_no >= 0", name="ck_diagnostics_attempt"),
        sa.CheckConstraint(
            "jsonb_typeof(document->'unassigned') IS NOT DISTINCT FROM 'array' "
            "AND jsonb_typeof(document->'operational') IS NOT DISTINCT FROM 'array' "
            "AND jsonb_typeof(document->'provenance') IS NOT DISTINCT FROM 'object' "
            "AND jsonb_array_length(document->'operational') <= 1",
            name="ck_diagnostics_document",
        ),
    )
    op.execute("""
        CREATE FUNCTION routeops_guard_diagnostics() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run record; job record;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'diagnostic history is immutable' USING ERRCODE='55000';
          END IF;
          SELECT * INTO job FROM revision_run_jobs WHERE run_id=NEW.run_id FOR UPDATE;
          SELECT * INTO run FROM planning_runs WHERE id=NEW.run_id FOR UPDATE;
          IF run.status NOT IN ('RUNNING','QUEUED') THEN
            RAISE EXCEPTION 'diagnostic outcome already closed' USING ERRCODE='55000';
          END IF;
          IF job.run_id IS NOT NULL AND NEW.owner_token IS NOT NULL AND (
             job.status <> 'RUNNING' OR job.lease_token IS DISTINCT FROM NEW.owner_token
             OR job.attempts <> NEW.attempt_no OR job.lease_until IS NULL
             OR job.lease_until < clock_timestamp()
          ) THEN
            RAISE EXCEPTION 'diagnostic owner fenced' USING ERRCODE='55000';
          END IF;
          IF job.run_id IS NOT NULL AND NEW.owner_token IS NULL
             AND job.lease_until >= clock_timestamp() THEN
            RAISE EXCEPTION 'diagnostic recovery conflicts with live owner' USING ERRCODE='55000';
          END IF;
          IF NEW.document->>'calculation_version' IS DISTINCT FROM NEW.calculation_version THEN
            RAISE EXCEPTION 'diagnostic version mismatch' USING ERRCODE='55000';
          END IF;
          IF jsonb_array_length(NEW.document->'operational') = 0 THEN
            IF run.result_data IS NULL OR NEW.document->'unassigned' IS DISTINCT FROM
                 run.result_data->'unassigned' THEN
              RAISE EXCEPTION 'diagnostics disagree with result' USING ERRCODE='55000';
            END IF;
          ELSE
            IF NEW.document->'operational'->0->>'code' IS DISTINCT FROM run.error
                 OR run.result_data IS NOT NULL
                 OR jsonb_array_length(NEW.document->'unassigned') <> 0 THEN
              RAISE EXCEPTION 'diagnostics disagree with failure' USING ERRCODE='55000';
            END IF;
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_planning_diagnostics_guard BEFORE INSERT OR UPDATE OR DELETE
        ON planning_diagnostics FOR EACH ROW EXECUTE FUNCTION routeops_guard_diagnostics()
    """)


def downgrade() -> None:
    if op.get_bind().exec_driver_sql("SELECT EXISTS (SELECT 1 FROM planning_diagnostics)").scalar():
        raise RuntimeError("downgrade blocked: diagnostics contain history")
    op.drop_table("planning_diagnostics")
    op.execute("DROP FUNCTION routeops_guard_diagnostics()")
