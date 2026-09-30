"""Operational inventory, immutable allocation evidence and reservation ledger.

Revision ID: d42e4a91b6c0
Revises: 7a69c4d10e32
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d42e4a91b6c0"
down_revision: str | None = "7a69c4d10e32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_inventory_state",
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("active_revision_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["active_revision_id"], ["scenario_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["inventory_snapshots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("scenario_id"),
    )
    op.create_table(
        "operational_inventory_positions",
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("center_source_id", sa.String(100, collation="C"), nullable=False),
        sa.Column("sku", sa.String(100, collation="C"), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("source_snapshot_line_id", sa.Uuid(), nullable=False),
        sa.Column("on_hand_quantity", sa.BigInteger(), nullable=False),
        sa.Column("externally_reserved_quantity", sa.BigInteger(), nullable=False),
        sa.Column("safety_stock_quantity", sa.BigInteger(), nullable=False),
        sa.Column(
            "routeops_reserved_quantity", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "available_quantity",
            sa.BigInteger(),
            sa.Computed(
                "on_hand_quantity - externally_reserved_quantity - "
                "safety_stock_quantity - routeops_reserved_quantity",
                persisted=True,
            ),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_revision_id"], ["scenario_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_line_id"], ["inventory_snapshot_lines.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("scenario_id", "center_source_id", "sku"),
        sa.CheckConstraint(
            "on_hand_quantity >= 0 AND externally_reserved_quantity >= 0 "
            "AND safety_stock_quantity >= 0 AND routeops_reserved_quantity >= 0 "
            "AND externally_reserved_quantity + safety_stock_quantity + "
            "routeops_reserved_quantity <= on_hand_quantity",
            name="ck_operational_inventory_available",
        ),
    )
    op.create_index(
        "ix_operational_inventory_source",
        "operational_inventory_positions",
        ["source_revision_id", "source_snapshot_line_id"],
    )
    op.create_table(
        "allocation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("client_key", sa.String(100), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id"], ["scenario_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "inventory_snapshot_id"],
            ["inventory_snapshots.scenario_revision_id", "inventory_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_revision_id", "client_key", name="uq_allocation_attempt_key"),
        sa.CheckConstraint(
            "length(client_key) BETWEEN 1 AND 100", name="ck_allocation_attempt_key"
        ),
        sa.CheckConstraint(
            "status IN ('BUILDING','HELD','CONFIRMED','RELEASED')",
            name="ck_allocation_attempt_status",
        ),
        sa.CheckConstraint("version >= 0", name="ck_allocation_attempt_version"),
    )
    op.create_index(
        "ix_allocation_attempts_revision",
        "allocation_attempts",
        ["scenario_revision_id", "created_at"],
    )
    op.create_table(
        "allocation_decision_snapshots",
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_sha256", sa.String(64), nullable=False),
        sa.Column("inventory_data", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["allocation_attempts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.CheckConstraint(
            "inventory_sha256 ~ '^[0-9a-f]{64}$'", name="ck_allocation_snapshot_hash"
        ),
    )
    op.create_table(
        "allocation_order_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("center_source_id", sa.String(100, collation="C")),
        sa.Column("reason_code", sa.String(60)),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["allocation_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "order_id", name="uq_allocation_decision_order"),
        sa.UniqueConstraint("attempt_id", "sequence", name="uq_allocation_decision_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_allocation_decision_sequence"),
        sa.CheckConstraint(
            "(center_source_id IS NULL) = (reason_code IS NOT NULL)",
            name="ck_allocation_decision_outcome",
        ),
    )
    op.create_table(
        "allocation_reservation_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("order_line_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("center_source_id", sa.String(100, collation="C"), nullable=False),
        sa.Column("sku", sa.String(100, collation="C"), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["allocation_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["allocation_order_decisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["order_line_id"], ["order_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["scenario_id", "center_source_id", "sku"],
            [
                "operational_inventory_positions.scenario_id",
                "operational_inventory_positions.center_source_id",
                "operational_inventory_positions.sku",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id", "order_line_id", name="uq_allocation_reservation_order_line"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_allocation_reservation_quantity"),
    )
    op.create_index(
        "ix_allocation_reservations_position",
        "allocation_reservation_lines",
        ["scenario_id", "center_source_id", "sku"],
    )
    op.create_table(
        "allocation_attempt_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(100)),
        sa.Column("reason", sa.String(500)),
        sa.ForeignKeyConstraint(["attempt_id"], ["allocation_attempts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "sequence", name="uq_allocation_event_sequence"),
        sa.CheckConstraint("sequence >= 0", name="ck_allocation_event_sequence"),
    )
    for table in (
        "allocation_decision_snapshots",
        "allocation_order_decisions",
        "allocation_reservation_lines",
        "allocation_attempt_events",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION routeops_reject_mutation()"
        )
    op.execute("""
        CREATE FUNCTION routeops_guard_allocation_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE current_status varchar;
        BEGIN
          SELECT status INTO current_status FROM allocation_attempts WHERE id = NEW.attempt_id;
          IF current_status <> 'BUILDING' THEN
            RAISE EXCEPTION 'allocation evidence is sealed' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END $$
    """)
    for table in (
        "allocation_decision_snapshots",
        "allocation_order_decisions",
        "allocation_reservation_lines",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_building BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION routeops_guard_allocation_insert()"
        )
    op.execute("""
        CREATE FUNCTION routeops_guard_allocation_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE item record;
        BEGIN
          IF OLD.id IS DISTINCT FROM NEW.id OR OLD.scenario_id IS DISTINCT FROM NEW.scenario_id
             OR OLD.scenario_revision_id IS DISTINCT FROM NEW.scenario_revision_id
             OR OLD.inventory_snapshot_id IS DISTINCT FROM NEW.inventory_snapshot_id
             OR OLD.client_key IS DISTINCT FROM NEW.client_key
             OR OLD.policy_version IS DISTINCT FROM NEW.policy_version
             OR OLD.created_at IS DISTINCT FROM NEW.created_at
             OR NEW.version <> OLD.version + 1
             OR NOT ((OLD.status = 'BUILDING' AND NEW.status = 'HELD')
               OR (OLD.status = 'HELD' AND NEW.status IN ('CONFIRMED','RELEASED'))
               OR (OLD.status = 'CONFIRMED' AND NEW.status = 'RELEASED')) THEN
            RAISE EXCEPTION 'invalid allocation transition' USING ERRCODE = '55000';
          END IF;
          IF NEW.status = 'HELD' OR NEW.status = 'RELEASED' THEN
            FOR item IN
              SELECT scenario_id, center_source_id, sku, sum(quantity) AS quantity
              FROM allocation_reservation_lines WHERE attempt_id = NEW.id
              GROUP BY scenario_id, center_source_id, sku
              ORDER BY scenario_id, center_source_id COLLATE "C", sku COLLATE "C"
            LOOP
              IF NEW.status = 'HELD' THEN
                UPDATE operational_inventory_positions
                SET routeops_reserved_quantity = routeops_reserved_quantity + item.quantity,
                    updated_at = NEW.transitioned_at
                WHERE scenario_id = item.scenario_id
                  AND center_source_id = item.center_source_id AND sku = item.sku
                  AND available_quantity >= item.quantity;
              ELSE
                UPDATE operational_inventory_positions
                SET routeops_reserved_quantity = routeops_reserved_quantity - item.quantity,
                    updated_at = NEW.transitioned_at
                WHERE scenario_id = item.scenario_id
                  AND center_source_id = item.center_source_id AND sku = item.sku
                  AND routeops_reserved_quantity >= item.quantity;
              END IF;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'operational stock conflict' USING ERRCODE = '23514';
              END IF;
            END LOOP;
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_attempt_update
        BEFORE UPDATE ON allocation_attempts FOR EACH ROW
        EXECUTE FUNCTION routeops_guard_allocation_update()
    """)
    op.execute("""
        CREATE FUNCTION routeops_check_allocation_event() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE target_id uuid; final_status varchar; final_version integer;
                final_time timestamptz; last_event record;
        BEGIN
          IF TG_TABLE_NAME = 'allocation_attempts' THEN
            target_id := NEW.id;
          ELSE
            target_id := NEW.attempt_id;
          END IF;
          SELECT status, version, transitioned_at INTO final_status, final_version, final_time
          FROM allocation_attempts WHERE id = target_id;
          SELECT sequence, to_status, occurred_at INTO last_event
          FROM allocation_attempt_events WHERE attempt_id = target_id
          ORDER BY sequence DESC LIMIT 1;
          IF final_status = 'BUILDING' OR last_event.sequence IS DISTINCT FROM final_version
             OR last_event.to_status IS DISTINCT FROM final_status
             OR last_event.occurred_at IS DISTINCT FROM final_time
             OR (SELECT count(*) FROM allocation_attempt_events WHERE attempt_id = target_id)
                <> final_version + 1
             OR EXISTS (
               SELECT 1 FROM (
                 SELECT sequence, from_status, to_status,
                        lag(to_status) OVER (ORDER BY sequence) AS preceding_status
                 FROM allocation_attempt_events WHERE attempt_id = target_id
               ) history
               WHERE (sequence = 0 AND (from_status IS NOT NULL OR to_status <> 'BUILDING'))
                  OR (sequence > 0 AND from_status IS DISTINCT FROM preceding_status)
                  OR (sequence = 1 AND to_status <> 'HELD')
                  OR (sequence > 1 AND NOT (
                    (from_status = 'HELD' AND to_status IN ('CONFIRMED','RELEASED'))
                    OR (from_status = 'CONFIRMED' AND to_status = 'RELEASED')))
             )
             OR NOT EXISTS (
               SELECT 1 FROM allocation_decision_snapshots WHERE attempt_id = target_id
             )
             OR (SELECT count(*) FROM allocation_order_decisions WHERE attempt_id = target_id)
                <> (SELECT count(*) FROM orders WHERE scenario_revision_id =
                    (SELECT scenario_revision_id FROM allocation_attempts
                     WHERE id = target_id))
             OR EXISTS (
               SELECT 1 FROM allocation_order_decisions d
               JOIN orders o ON o.id = d.order_id
               JOIN allocation_attempts a ON a.id = d.attempt_id
               WHERE d.attempt_id = target_id
                 AND (o.scenario_revision_id <> a.scenario_revision_id
                      OR (d.center_source_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM distribution_centers c
                        WHERE c.scenario_revision_id = a.scenario_revision_id
                          AND c.source_id = d.center_source_id)))
             )
             OR EXISTS (
               SELECT 1 FROM allocation_order_decisions d
               JOIN order_lines ol ON ol.order_id = d.order_id
               JOIN orders o ON o.id = d.order_id
               JOIN allocation_attempts a ON a.id = d.attempt_id
               WHERE d.attempt_id = target_id AND d.center_source_id IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM allocation_reservation_lines r
                   WHERE r.attempt_id = d.attempt_id AND r.decision_id = d.id
                     AND r.order_line_id = ol.id AND r.scenario_id = a.scenario_id
                     AND r.center_source_id = d.center_source_id
                     AND r.sku = ol.sku AND r.quantity = ol.quantity)
             )
             OR EXISTS (
               SELECT 1 FROM allocation_reservation_lines r
               JOIN allocation_order_decisions d ON d.id = r.decision_id
               JOIN order_lines ol ON ol.id = r.order_line_id
               JOIN allocation_attempts a ON a.id = r.attempt_id
               WHERE r.attempt_id = target_id
                 AND (d.attempt_id <> r.attempt_id OR d.order_id <> ol.order_id
                      OR d.center_source_id IS DISTINCT FROM r.center_source_id
                      OR r.scenario_id <> a.scenario_id OR r.sku <> ol.sku
                      OR r.quantity <> ol.quantity)
             ) THEN
            RAISE EXCEPTION 'allocation decision or event is incomplete' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    for table in ("allocation_attempts", "allocation_attempt_events"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_consistent AFTER INSERT OR UPDATE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION routeops_check_allocation_event()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "allocation_attempts",
        "allocation_attempt_events",
        "operational_inventory_state",
        "operational_inventory_positions",
    ):
        if connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {table})").scalar():
            raise RuntimeError(f"downgrade blocked: {table} contains operational history")
    for table in ("allocation_attempts", "allocation_attempt_events"):
        op.execute(f"DROP TRIGGER trg_{table}_consistent ON {table}")
    op.execute("DROP TRIGGER trg_allocation_attempt_update ON allocation_attempts")
    for table in (
        "allocation_decision_snapshots",
        "allocation_order_decisions",
        "allocation_reservation_lines",
        "allocation_attempt_events",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    for table in (
        "allocation_decision_snapshots",
        "allocation_order_decisions",
        "allocation_reservation_lines",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_building ON {table}")
    op.execute("DROP FUNCTION routeops_check_allocation_event()")
    op.execute("DROP FUNCTION routeops_guard_allocation_update()")
    op.execute("DROP FUNCTION routeops_guard_allocation_insert()")
    op.drop_table("allocation_attempt_events")
    op.drop_index("ix_allocation_reservations_position", table_name="allocation_reservation_lines")
    op.drop_table("allocation_reservation_lines")
    op.drop_table("allocation_order_decisions")
    op.drop_table("allocation_decision_snapshots")
    op.drop_index("ix_allocation_attempts_revision", table_name="allocation_attempts")
    op.drop_table("allocation_attempts")
    op.drop_index("ix_operational_inventory_source", table_name="operational_inventory_positions")
    op.drop_table("operational_inventory_positions")
    op.drop_table("operational_inventory_state")
