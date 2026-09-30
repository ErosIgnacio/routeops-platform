"""Recoverable imported-revision runs and per-order reservation transitions.

Revision ID: c951e2a7d430
Revises: d42e4a91b6c0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c951e2a7d430"
down_revision: str | None = "d42e4a91b6c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revision_run_jobs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("client_key", sa.String(100), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("allocation_attempt_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["planning_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id"], ["scenario_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["allocation_attempt_id"], ["allocation_attempts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("scenario_revision_id", "client_key", name="uq_revision_run_key"),
        sa.UniqueConstraint("allocation_attempt_id", name="uq_revision_run_allocation"),
        sa.CheckConstraint("length(client_key) BETWEEN 1 AND 100", name="ck_revision_run_key"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_run_hash"),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','READY','ACCEPTED','CANCELED','FAILED')",
            name="ck_revision_run_status",
        ),
        sa.CheckConstraint("version >= 0 AND attempts >= 0", name="ck_revision_run_counters"),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL)", name="ck_revision_run_lease_pair"
        ),
    )
    op.create_index("ix_revision_run_claim", "revision_run_jobs", ["status", "lease_until"])
    op.create_index("ix_revision_run_scenario", "revision_run_jobs", ["scenario_id", "created_at"])
    op.create_table(
        "revision_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.ForeignKeyConstraint(["run_id"], ["revision_run_jobs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_revision_run_event_sequence"),
        sa.CheckConstraint("sequence >= 0", name="ck_revision_run_event_sequence"),
    )
    op.create_table(
        "run_order_reservations",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["revision_run_jobs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["allocation_order_decisions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("run_id", "decision_id"),
        sa.UniqueConstraint("decision_id", name="uq_run_order_reservation_decision"),
        sa.CheckConstraint(
            "status IN ('HELD','CONFIRMED','RELEASED')", name="ck_run_order_reservation_status"
        ),
        sa.CheckConstraint("version >= 0", name="ck_run_order_reservation_version"),
    )
    op.create_table(
        "run_reservation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.ForeignKeyConstraint(
            ["run_id", "decision_id"],
            ["run_order_reservations.run_id", "run_order_reservations.decision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "decision_id", "sequence", name="uq_run_reservation_event_sequence"
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_run_reservation_event_sequence"),
    )
    for table in ("revision_run_events", "run_reservation_events"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION routeops_reject_mutation()"
        )
    op.execute("""
        CREATE FUNCTION routeops_guard_run_reservation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE item record;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'run reservation is immutable' USING ERRCODE = '55000';
          END IF;
          IF OLD.run_id IS DISTINCT FROM NEW.run_id
             OR OLD.decision_id IS DISTINCT FROM NEW.decision_id
             OR NEW.version <> OLD.version + 1
             OR NOT (OLD.status = 'HELD' AND NEW.status IN ('CONFIRMED','RELEASED')) THEN
            RAISE EXCEPTION 'invalid run reservation transition' USING ERRCODE = '55000';
          END IF;
          IF NEW.status = 'RELEASED' THEN
            FOR item IN
              SELECT r.scenario_id, r.center_source_id, r.sku, sum(r.quantity) quantity
              FROM allocation_reservation_lines r WHERE r.decision_id = NEW.decision_id
              GROUP BY r.scenario_id, r.center_source_id, r.sku
              ORDER BY r.scenario_id, r.center_source_id COLLATE "C", r.sku COLLATE "C"
            LOOP
              UPDATE operational_inventory_positions
              SET routeops_reserved_quantity = routeops_reserved_quantity - item.quantity,
                  updated_at = NEW.transitioned_at
              WHERE scenario_id = item.scenario_id AND center_source_id = item.center_source_id
                AND sku = item.sku AND routeops_reserved_quantity >= item.quantity;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'run reservation counter conflict' USING ERRCODE = '23514';
              END IF;
            END LOOP;
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_run_order_reservation_update
        BEFORE UPDATE OR DELETE ON run_order_reservations FOR EACH ROW
        EXECUTE FUNCTION routeops_guard_run_reservation()
    """)
    op.execute("""
        CREATE FUNCTION routeops_guard_managed_attempt() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM revision_run_jobs WHERE allocation_attempt_id = OLD.id
          ) THEN
            RAISE EXCEPTION 'allocation is managed by a planning run' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_managed_allocation_attempt
        BEFORE UPDATE OR DELETE ON allocation_attempts FOR EACH ROW
        EXECUTE FUNCTION routeops_guard_managed_attempt()
    """)
    op.execute("""
        CREATE FUNCTION routeops_check_revision_run_event() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE target_id uuid; current_status varchar; current_version integer;
                event_status varchar; event_sequence integer; event_count bigint;
        BEGIN
          IF TG_TABLE_NAME = 'revision_run_jobs' THEN target_id := NEW.run_id;
          ELSE target_id := NEW.run_id; END IF;
          SELECT status, version INTO current_status, current_version
          FROM revision_run_jobs WHERE run_id = target_id;
          SELECT to_status, sequence INTO event_status, event_sequence
          FROM revision_run_events WHERE run_id = target_id ORDER BY sequence DESC LIMIT 1;
          SELECT count(*) INTO event_count FROM revision_run_events WHERE run_id = target_id;
          IF current_status IS DISTINCT FROM event_status
             OR current_version IS DISTINCT FROM event_sequence
             OR event_count <> current_version + 1
             OR (SELECT status FROM planning_runs WHERE id = target_id)
                IS DISTINCT FROM current_status THEN
            RAISE EXCEPTION 'revision run event mismatch' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    for table in ("revision_run_jobs", "revision_run_events"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_consistent AFTER INSERT OR UPDATE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION routeops_check_revision_run_event()"
        )
    op.execute("""
        CREATE FUNCTION routeops_check_run_reservation_event() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE current_status varchar; current_version integer;
                event_status varchar; event_sequence integer; event_count bigint;
        BEGIN
          SELECT status, version INTO current_status, current_version
          FROM run_order_reservations
          WHERE run_id = NEW.run_id AND decision_id = NEW.decision_id;
          SELECT to_status, sequence INTO event_status, event_sequence
          FROM run_reservation_events
          WHERE run_id = NEW.run_id AND decision_id = NEW.decision_id
          ORDER BY sequence DESC LIMIT 1;
          SELECT count(*) INTO event_count FROM run_reservation_events
          WHERE run_id = NEW.run_id AND decision_id = NEW.decision_id;
          IF current_status IS DISTINCT FROM event_status
             OR current_version IS DISTINCT FROM event_sequence
             OR event_count <> current_version + 1 THEN
            RAISE EXCEPTION 'run reservation event mismatch' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    for table in ("run_order_reservations", "run_reservation_events"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_consistent AFTER INSERT OR UPDATE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION routeops_check_run_reservation_event()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("revision_run_jobs", "run_order_reservations"):
        if connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {table})").scalar():
            raise RuntimeError(f"downgrade blocked: {table} contains planning history")
    op.execute("DROP TRIGGER trg_managed_allocation_attempt ON allocation_attempts")
    for table in ("run_order_reservations", "run_reservation_events"):
        op.execute(f"DROP TRIGGER trg_{table}_consistent ON {table}")
    for table in ("revision_run_jobs", "revision_run_events"):
        op.execute(f"DROP TRIGGER trg_{table}_consistent ON {table}")
    op.execute("DROP TRIGGER trg_run_order_reservation_update ON run_order_reservations")
    for table in ("revision_run_events", "run_reservation_events"):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.drop_table("run_reservation_events")
    op.drop_table("run_order_reservations")
    op.drop_table("revision_run_events")
    op.drop_index("ix_revision_run_scenario", table_name="revision_run_jobs")
    op.drop_index("ix_revision_run_claim", table_name="revision_run_jobs")
    op.drop_table("revision_run_jobs")
    op.execute("DROP FUNCTION routeops_check_run_reservation_event()")
    op.execute("DROP FUNCTION routeops_check_revision_run_event()")
    op.execute("DROP FUNCTION routeops_guard_managed_attempt()")
    op.execute("DROP FUNCTION routeops_guard_run_reservation()")
