"""Append-only processing measurements, no historical backfill.

Revision ID: f6b2d8a4c190
Revises: e3a1b7c9d240
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f6b2d8a4c190"
down_revision: str | None = "e3a1b7c9d240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_timing_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("owner_token", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ns", sa.BigInteger()),
        sa.Column("outcome", sa.String(30)),
        sa.Column("calculation_version", sa.String(30), nullable=False),
        sa.Column("details", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["planning_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "attempt_no", "kind", "phase", name="uq_timing_boundary"),
        sa.CheckConstraint("attempt_no > 0", name="ck_timing_attempt"),
        sa.CheckConstraint("duration_ns IS NULL OR duration_ns >= 0", name="ck_timing_duration"),
        sa.CheckConstraint(
            "kind IN ('ATTEMPT_STARTED','ATTEMPT_FINISHED','INTERRUPTED',"
            "'PHASE_STARTED','PHASE_FINISHED')",
            name="ck_timing_kind",
        ),
        sa.CheckConstraint(
            "(kind IN ('PHASE_FINISHED','ATTEMPT_FINISHED')) = (duration_ns IS NOT NULL)",
            name="ck_timing_measured",
        ),
    )
    op.create_index("ix_timing_run_attempt", "planning_timing_events", ["run_id", "attempt_no"])
    op.execute("""
        CREATE FUNCTION routeops_guard_timing_event() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE job record; state text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'timing history is immutable' USING ERRCODE='55000';
          END IF;
          IF NEW.kind <> 'ATTEMPT_STARTED' AND NOT EXISTS (
            SELECT 1 FROM planning_timing_events WHERE run_id=NEW.run_id
              AND owner_token=NEW.owner_token AND attempt_no=NEW.attempt_no
              AND kind='ATTEMPT_STARTED'
          ) THEN
            RAISE EXCEPTION 'timing attempt not started' USING ERRCODE='55000';
          END IF;
          IF NEW.kind = 'PHASE_FINISHED' AND NOT EXISTS (
            SELECT 1 FROM planning_timing_events WHERE run_id=NEW.run_id
              AND owner_token=NEW.owner_token AND phase=NEW.phase AND kind='PHASE_STARTED'
          ) THEN
            RAISE EXCEPTION 'timing phase not started' USING ERRCODE='55000';
          END IF;
          SELECT * INTO job FROM revision_run_jobs WHERE run_id=NEW.run_id FOR UPDATE;
          IF FOUND THEN
            IF job.status <> 'RUNNING' OR job.lease_token IS DISTINCT FROM NEW.owner_token
               OR job.attempts <> NEW.attempt_no
               OR (NEW.kind <> 'INTERRUPTED' AND job.lease_until < clock_timestamp()) THEN
              RAISE EXCEPTION 'timing owner fenced' USING ERRCODE='55000';
            END IF;
          ELSE
            SELECT status INTO state FROM planning_runs WHERE id=NEW.run_id FOR UPDATE;
            IF state <> 'RUNNING' OR NEW.attempt_no <> 1 THEN
              RAISE EXCEPTION 'demo timing owner fenced' USING ERRCODE='55000';
            END IF;
          END IF;
          IF EXISTS (SELECT 1 FROM planning_timing_events WHERE run_id=NEW.run_id
                     AND owner_token=NEW.owner_token
                     AND kind IN ('ATTEMPT_FINISHED','INTERRUPTED')) THEN
            RAISE EXCEPTION 'timing attempt already closed' USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_planning_timing_events_guard BEFORE INSERT OR UPDATE OR DELETE
        ON planning_timing_events FOR EACH ROW EXECUTE FUNCTION routeops_guard_timing_event()
    """)


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS (SELECT 1 FROM planning_timing_events)")
        .scalar()
    ):
        raise RuntimeError("downgrade blocked: processing measurements contain history")
    op.drop_table("planning_timing_events")
    op.execute("DROP FUNCTION routeops_guard_timing_event()")
