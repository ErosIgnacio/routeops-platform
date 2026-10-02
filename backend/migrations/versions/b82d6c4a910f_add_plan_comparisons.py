"""Immutable analytic contexts/results and PostgreSQL leased comparison jobs.

Revision ID: b82d6c4a910f
Revises: a71c9e3d602b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b82d6c4a910f"
down_revision: str | None = "a71c9e3d602b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_comparisons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Uuid(),
            sa.ForeignKey("scenarios.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "scenario_revision_id",
            sa.Uuid(),
            sa.ForeignKey("scenario_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(100), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("request_data", JSONB(), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("context_data", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scenario_id", "client_key", name="uq_comparison_key"),
        sa.CheckConstraint("length(client_key) BETWEEN 1 AND 100", name="ck_comparison_key"),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' AND context_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_comparison_hashes",
        ),
    )
    op.create_index(
        "ix_comparison_revision_created", "plan_comparisons", ["scenario_revision_id", "created_at"]
    )
    op.create_table(
        "comparison_jobs",
        sa.Column(
            "comparison_id",
            sa.Uuid(),
            sa.ForeignKey("plan_comparisons.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','READY','FAILED')", name="ck_comparison_job_status"
        ),
        sa.CheckConstraint("attempts >= 0 AND version >= 0", name="ck_comparison_job_counters"),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL)", name="ck_comparison_job_lease"
        ),
    )
    op.create_index(
        "ix_comparison_job_claim", "comparison_jobs", ["status", "next_attempt_at", "lease_until"]
    )
    op.create_table(
        "comparison_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "comparison_id",
            sa.Uuid(),
            sa.ForeignKey("comparison_jobs.comparison_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("comparison_id", "sequence", name="uq_comparison_event_sequence"),
        sa.CheckConstraint(
            "sequence >= 0 AND attempt_no >= 0", name="ck_comparison_event_counters"
        ),
    )
    op.create_table(
        "comparison_results",
        sa.Column(
            "comparison_id",
            sa.Uuid(),
            sa.ForeignKey("comparison_jobs.comparison_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
        sa.Column("owner_token", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$' AND context_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_comparison_result_hashes",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_comparison_result_attempt"),
    )
    op.execute("""
      CREATE FUNCTION routeops_guard_comparison_history() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE job record; ctx record; previous record;
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'comparison history is immutable' USING ERRCODE='55000';
        END IF;
        IF TG_TABLE_NAME = 'plan_comparisons' THEN
          IF NOT EXISTS (SELECT 1 FROM scenario_revisions r
                         WHERE r.id=NEW.scenario_revision_id AND r.scenario_id=NEW.scenario_id)
             OR NEW.context_data->>'revision_id'
                IS DISTINCT FROM NEW.scenario_revision_id::text THEN
            RAISE EXCEPTION 'comparison revision mismatch' USING ERRCODE='55000';
          END IF;
        ELSIF TG_TABLE_NAME = 'comparison_events' THEN
          SELECT * INTO previous FROM comparison_events WHERE comparison_id=NEW.comparison_id
            ORDER BY sequence DESC LIMIT 1;
          IF (previous.id IS NULL AND (NEW.sequence <> 0 OR NEW.from_status IS NOT NULL
                                      OR NEW.to_status <> 'QUEUED' OR NEW.attempt_no <> 0))
             OR (previous.id IS NOT NULL AND (NEW.sequence <> previous.sequence+1
                    OR NEW.from_status IS DISTINCT FROM previous.to_status)) THEN
            RAISE EXCEPTION 'comparison event chain mismatch' USING ERRCODE='55000';
          END IF;
        ELSIF TG_TABLE_NAME = 'comparison_results' THEN
          SELECT * INTO job FROM comparison_jobs WHERE comparison_id=NEW.comparison_id FOR UPDATE;
          SELECT * INTO ctx FROM plan_comparisons WHERE id=NEW.comparison_id;
          IF job.status <> 'RUNNING' OR job.lease_token IS DISTINCT FROM NEW.owner_token
             OR job.attempts <> NEW.attempt_no OR job.lease_until IS NULL
             OR job.lease_until < clock_timestamp() THEN
            RAISE EXCEPTION 'comparison owner fenced' USING ERRCODE='55000';
          END IF;
          IF NEW.context_sha256 IS DISTINCT FROM ctx.context_sha256
             OR NEW.document->>'context_sha256' IS DISTINCT FROM ctx.context_sha256 THEN
            RAISE EXCEPTION 'comparison context mismatch' USING ERRCODE='55000';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION routeops_guard_comparison_job() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'comparison job history is immutable' USING ERRCODE='55000';
        END IF;
        IF TG_OP = 'INSERT' THEN
          IF NEW.status <> 'QUEUED' OR NEW.version <> 0 OR NEW.attempts <> 0 THEN
            RAISE EXCEPTION 'comparison initial state invalid' USING ERRCODE='55000';
          END IF;
        ELSE
          IF NEW.comparison_id <> OLD.comparison_id OR OLD.status IN ('READY','FAILED') THEN
            RAISE EXCEPTION 'comparison terminal state immutable' USING ERRCODE='55000';
          END IF;
          IF NEW.version = OLD.version THEN
            IF NEW.status <> OLD.status OR NEW.attempts <> OLD.attempts
               OR NEW.lease_token IS DISTINCT FROM OLD.lease_token
               OR NEW.transitioned_at <> OLD.transitioned_at
               OR NEW.next_attempt_at <> OLD.next_attempt_at
               OR OLD.lease_until IS NULL OR OLD.lease_until < clock_timestamp()
               OR NEW.lease_until IS NULL OR NEW.lease_until < OLD.lease_until THEN
              RAISE EXCEPTION 'invalid comparison heartbeat' USING ERRCODE='55000';
            END IF;
          ELSIF NEW.version <> OLD.version+1 OR NOT (
              (OLD.status='QUEUED' AND NEW.status IN ('RUNNING','FAILED')) OR
              (OLD.status='RUNNING' AND NEW.status IN ('RUNNING','QUEUED','READY','FAILED'))
          ) THEN
            RAISE EXCEPTION 'invalid comparison transition' USING ERRCODE='55000';
          END IF;
          IF NEW.version > OLD.version AND NEW.status='RUNNING' THEN
            IF NEW.attempts <> OLD.attempts+1 OR NEW.lease_token IS NULL
               OR (OLD.status='RUNNING' AND OLD.lease_until >= clock_timestamp()) THEN
              RAISE EXCEPTION 'invalid comparison claim' USING ERRCODE='55000';
            END IF;
          ELSIF NEW.version > OLD.version AND NEW.attempts <> OLD.attempts THEN
            RAISE EXCEPTION 'invalid comparison attempts' USING ERRCODE='55000';
          END IF;
          IF NEW.status IN ('READY','QUEUED') AND OLD.status='RUNNING'
             AND (OLD.lease_until IS NULL OR OLD.lease_until < clock_timestamp()) THEN
            RAISE EXCEPTION 'comparison transition owner expired' USING ERRCODE='55000';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION routeops_check_comparison_event() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE job record; event record;
      BEGIN
        SELECT * INTO job FROM comparison_jobs WHERE comparison_id=NEW.comparison_id;
        SELECT * INTO event FROM comparison_events WHERE comparison_id=NEW.comparison_id
          ORDER BY sequence DESC LIMIT 1;
        IF event.sequence IS DISTINCT FROM job.version
           OR event.to_status IS DISTINCT FROM job.status
           OR event.attempt_no IS DISTINCT FROM job.attempts
           OR event.occurred_at IS DISTINCT FROM job.transitioned_at THEN
          RAISE EXCEPTION 'comparison event state mismatch' USING ERRCODE='55000';
        END IF;
        IF (job.status='READY') IS DISTINCT FROM
           EXISTS(SELECT 1 FROM comparison_results WHERE comparison_id=NEW.comparison_id) THEN
          RAISE EXCEPTION 'comparison result state mismatch' USING ERRCODE='55000';
        END IF;
        RETURN NULL;
      END $$;
    """)
    for table in ("plan_comparisons", "comparison_events", "comparison_results"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION routeops_guard_comparison_history()"
        )
    op.execute("""
      CREATE TRIGGER trg_comparison_job_guard BEFORE INSERT OR UPDATE OR DELETE
        ON comparison_jobs FOR EACH ROW EXECUTE FUNCTION routeops_guard_comparison_job();
      CREATE CONSTRAINT TRIGGER trg_comparison_job_event AFTER INSERT OR UPDATE ON comparison_jobs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION routeops_check_comparison_event();
      CREATE CONSTRAINT TRIGGER trg_comparison_event_job AFTER INSERT ON comparison_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION routeops_check_comparison_event();
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM plan_comparisons)")):
        raise RuntimeError("downgrade blocked: comparisons contain history")
    for table in ("comparison_results", "comparison_events", "comparison_jobs", "plan_comparisons"):
        op.drop_table(table)
    for name in (
        "routeops_check_comparison_event",
        "routeops_guard_comparison_job",
        "routeops_guard_comparison_history",
    ):
        op.execute(f"DROP FUNCTION {name}()")
