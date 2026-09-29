# ruff: noqa: E501
"""Add the milestone 2 persistence foundation.

Revision ID: 525a2c8f3a8c
Revises: 20260924_0001
Create Date: 2026-09-29 19:23:00.889986
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "525a2c8f3a8c"
down_revision: str | None = "20260924_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION routeops_valid_skills(value varchar[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
        SELECT value IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM unnest(value) AS item(skill)
            WHERE skill IS NULL OR skill !~ '^[a-z0-9._-]+$' OR length(skill) > 100
          )
          AND value = ARRAY(
            SELECT DISTINCT skill FROM unnest(value) AS item(skill) ORDER BY skill
          )::varchar[]
        $$
    """)
    op.execute("""
        CREATE FUNCTION routeops_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'published RouteOps data is immutable' USING ERRCODE = '55000';
        END
        $$
    """)
    op.execute("""
        CREATE FUNCTION routeops_lock_run_links() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.scenario_revision_id IS NOT NULL THEN
              RAISE EXCEPTION 'linked planning run cannot be deleted' USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.scenario_revision_id IS DISTINCT FROM NEW.scenario_revision_id
             OR OLD.inventory_snapshot_id IS DISTINCT FROM NEW.inventory_snapshot_id THEN
            RAISE EXCEPTION 'planning run source cannot change' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE FUNCTION routeops_guard_batch_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.id IS DISTINCT FROM NEW.id
             OR OLD.scenario_id IS DISTINCT FROM NEW.scenario_id
             OR OLD.client_key IS DISTINCT FROM NEW.client_key
             OR OLD.package_sha256 IS DISTINCT FROM NEW.package_sha256
             OR OLD.parser_version IS DISTINCT FROM NEW.parser_version
             OR OLD.created_at IS DISTINCT FROM NEW.created_at
             OR OLD.created_by IS DISTINCT FROM NEW.created_by THEN
            RAISE EXCEPTION 'import batch identity and content are immutable'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.version <> OLD.version + 1 OR NEW.status = OLD.status
             OR NOT (
               (OLD.status = 'RECEIVED' AND NEW.status IN ('VALIDATING','FAILED'))
               OR (OLD.status = 'VALIDATING' AND NEW.status IN ('VALID','INVALID','FAILED'))
               OR (OLD.status = 'VALID' AND NEW.status = 'PUBLISHED')
             ) THEN
            RAISE EXCEPTION 'invalid import batch transition' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE FUNCTION routeops_check_batch_event() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          target_id uuid;
          batch_version bigint;
          batch_status varchar;
          batch_time timestamptz;
          event_sequence bigint;
          event_status varchar;
          event_time timestamptz;
        BEGIN
          IF TG_TABLE_NAME = 'import_batches' THEN
            target_id := NEW.id;
          ELSE
            target_id := NEW.batch_id;
          END IF;
          SELECT version, status, transitioned_at
            INTO batch_version, batch_status, batch_time
            FROM import_batches WHERE id = target_id;
          SELECT sequence, to_status, occurred_at
            INTO event_sequence, event_status, event_time FROM import_batch_events
            WHERE batch_id = target_id ORDER BY sequence DESC LIMIT 1;
          IF event_sequence IS NULL OR event_sequence <> batch_version
             OR event_status <> batch_status OR event_time <> batch_time THEN
            RAISE EXCEPTION 'import batch state and event history diverge'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE FUNCTION routeops_check_source_dataset() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          source_dataset varchar;
        BEGIN
          SELECT dataset INTO source_dataset FROM import_files
            WHERE id = NEW.source_import_file_id AND batch_id = NEW.import_batch_id;
          IF source_dataset IS NULL
             OR source_dataset NOT IN ('workbook', TG_ARGV[0])
             OR NEW.source_row_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'invalid source provenance' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END
        $$
    """)
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_scenarios_status"),
        sa.CheckConstraint("version > 0", name="ck_scenarios_version"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scenarios_status_created", "scenarios", ["status", "created_at"], unique=False
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("client_key", sa.String(length=200), nullable=True),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="RECEIVED", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.CheckConstraint("package_sha256 ~ '^[0-9a-f]{64}$'", name="ck_import_batches_hash"),
        sa.CheckConstraint(
            "status IN ('RECEIVED','VALIDATING','VALID','INVALID','FAILED','PUBLISHED')",
            name="ck_import_batches_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_import_batches_version"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "client_key", name="uq_import_batches_client_key"),
        sa.UniqueConstraint("scenario_id", "id", name="uq_import_batches_scenario_id"),
    )
    op.create_index(
        "ix_import_batches_scenario_status",
        "import_batches",
        ["scenario_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "import_batch_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('RECEIVED','VALIDATING','VALID','INVALID','FAILED','PUBLISHED')",
            name="ck_import_batch_events_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('RECEIVED','VALIDATING','VALID','INVALID','FAILED','PUBLISHED')",
            name="ck_import_batch_events_to_status",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_import_batch_events_sequence"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "sequence", name="uq_import_batch_events_sequence"),
    )
    op.create_table(
        "import_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("dataset", sa.String(length=30), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "dataset IN ('orders','order_lines','inventory','distribution_centers','vehicles','workbook')",
            name="ck_import_files_dataset",
        ),
        sa.CheckConstraint(
            "display_name !~ '[/\\\\]' AND storage_key !~ '[/\\\\]'",
            name="ck_import_files_no_paths",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_import_files_hash"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_import_files_size"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "dataset", name="uq_import_files_batch_dataset"),
        sa.UniqueConstraint("batch_id", "id", name="uq_import_files_batch_id"),
        sa.UniqueConstraint("storage_key", name="uq_import_files_storage_key"),
    )
    op.create_table(
        "scenario_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.BigInteger(), nullable=False),
        sa.Column("planning_date", sa.Date(), nullable=False),
        sa.Column("timezone_iana", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("horizon_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "operational_area",
            geoalchemy2.types.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=True,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=40), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(length=100), nullable=True),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_scenario_revisions_hash"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_scenario_revisions_currency"),
        sa.CheckConstraint(
            "horizon_start_at < horizon_end_at", name="ck_scenario_revisions_horizon"
        ),
        sa.CheckConstraint(
            "(horizon_start_at AT TIME ZONE timezone_iana)::date = planning_date "
            "AND ((horizon_end_at - interval '1 microsecond') "
            "AT TIME ZONE timezone_iana)::date = planning_date",
            name="ck_scenario_revisions_single_day",
        ),
        sa.CheckConstraint(
            "length(trim(timezone_iana)) > 0", name="ck_scenario_revisions_timezone"
        ),
        sa.CheckConstraint(
            "operational_area IS NULL OR (ST_SRID(operational_area) = 4326 AND ST_IsValid(operational_area) AND NOT ST_IsEmpty(operational_area))",
            name="ck_scenario_revisions_area",
        ),
        sa.CheckConstraint("revision_no > 0", name="ck_scenario_revisions_number"),
        sa.ForeignKeyConstraint(
            ["scenario_id", "import_batch_id"],
            ["import_batches.scenario_id", "import_batches.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "import_batch_id", name="uq_scenario_revisions_batch_pair"),
        sa.UniqueConstraint("import_batch_id", name="uq_scenario_revisions_batch"),
        sa.UniqueConstraint("scenario_id", "revision_no", name="uq_scenario_revisions_number"),
    )
    op.create_index(
        "ix_scenario_revisions_area",
        "scenario_revisions",
        ["operational_area"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_table(
        "distribution_centers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_import_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=100, collation="C"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("operating_start", sa.Time(), nullable=False),
        sa.Column("operating_end", sa.Time(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ST_SRID(location) = 4326 AND ST_IsValid(location) AND NOT ST_IsEmpty(location) AND ST_X(location) BETWEEN -180 AND 180 AND ST_Y(location) BETWEEN -90 AND 90",
            name="ck_distribution_centers_location",
        ),
        sa.CheckConstraint("operating_start < operating_end", name="ck_distribution_centers_hours"),
        sa.CheckConstraint("source_row_number > 1", name="ck_distribution_centers_row"),
        sa.ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scenario_revision_id", "id", name="uq_distribution_centers_revision_id"
        ),
        sa.UniqueConstraint(
            "scenario_revision_id", "source_id", name="uq_distribution_centers_source"
        ),
    )
    op.create_index(
        "ix_distribution_centers_location",
        "distribution_centers",
        ["location"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="IMPORTED", nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_inventory_snapshots_hash"),
        sa.CheckConstraint("kind IN ('IMPORTED','RUN')", name="ck_inventory_snapshots_kind"),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scenario_revision_id", "id", name="uq_inventory_snapshots_revision_id"
        ),
    )
    op.create_index(
        "ix_inventory_snapshots_revision_time",
        "inventory_snapshots",
        ["scenario_revision_id", "snapshot_at"],
        unique=False,
    )
    op.create_index(
        "uq_inventory_snapshots_imported",
        "inventory_snapshots",
        ["scenario_revision_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'IMPORTED'"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_import_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=100, collation="C"), nullable=False),
        sa.Column("customer_reference", sa.String(length=200), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "required_skills",
            postgresql.ARRAY(sa.String(length=100)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ST_SRID(location) = 4326 AND ST_IsValid(location) AND NOT ST_IsEmpty(location) AND ST_X(location) BETWEEN -180 AND 180 AND ST_Y(location) BETWEEN -90 AND 90",
            name="ck_orders_location",
        ),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_orders_priority"),
        sa.CheckConstraint("routeops_valid_skills(required_skills)", name="ck_orders_skills"),
        sa.CheckConstraint("service_minutes BETWEEN 1 AND 1440", name="ck_orders_service"),
        sa.CheckConstraint("source_row_number > 1", name="ck_orders_row"),
        sa.CheckConstraint("time_window_start < time_window_end", name="ck_orders_window"),
        sa.ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_revision_id", "id", name="uq_orders_revision_id"),
        sa.UniqueConstraint("scenario_revision_id", "source_id", name="uq_orders_source"),
    )
    op.create_index(
        "ix_orders_location", "orders", ["location"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        "ix_orders_revision_window",
        "orders",
        ["scenario_revision_id", "time_window_start"],
        unique=False,
    )
    op.create_table(
        "validation_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("dataset", sa.String(length=30), nullable=True),
        sa.Column("sheet", sa.String(length=30), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("value_excerpt", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('ERROR','WARNING')", name="ck_validation_issues_severity"),
        sa.CheckConstraint("ordinal > 0", name="ck_validation_issues_ordinal"),
        sa.CheckConstraint("row_number IS NULL OR row_number > 0", name="ck_validation_issues_row"),
        sa.ForeignKeyConstraint(
            ["batch_id", "file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_validation_issues_ordinal"),
    )
    op.create_index(
        "ix_validation_issues_batch_severity",
        "validation_issues",
        ["batch_id", "severity", "code"],
        unique=False,
    )
    op.create_table(
        "inventory_snapshot_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_import_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("distribution_center_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=100, collation="C"), nullable=False),
        sa.Column("on_hand_quantity", sa.BigInteger(), nullable=False),
        sa.Column("externally_reserved_quantity", sa.BigInteger(), nullable=False),
        sa.Column("safety_stock_quantity", sa.BigInteger(), nullable=False),
        sa.Column(
            "routeops_reserved_quantity", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("available_quantity", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "externally_reserved_quantity <= on_hand_quantity AND safety_stock_quantity <= on_hand_quantity - externally_reserved_quantity AND routeops_reserved_quantity <= on_hand_quantity - externally_reserved_quantity - safety_stock_quantity AND available_quantity = on_hand_quantity - externally_reserved_quantity - safety_stock_quantity - routeops_reserved_quantity",
            name="ck_inventory_snapshot_lines_available",
        ),
        sa.CheckConstraint(
            "on_hand_quantity >= 0 AND externally_reserved_quantity >= 0 AND safety_stock_quantity >= 0 AND routeops_reserved_quantity >= 0",
            name="ck_inventory_snapshot_lines_nonnegative",
        ),
        sa.CheckConstraint("source_row_number > 1", name="ck_inventory_snapshot_lines_row"),
        sa.ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "distribution_center_id"],
            ["distribution_centers.scenario_revision_id", "distribution_centers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "snapshot_id"],
            ["inventory_snapshots.scenario_revision_id", "inventory_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "distribution_center_id",
            "sku",
            name="uq_inventory_snapshot_lines_position",
        ),
    )
    op.create_index(
        "ix_inventory_snapshot_lines_revision_sku",
        "inventory_snapshot_lines",
        ["scenario_revision_id", "sku"],
        unique=False,
    )
    op.create_table(
        "order_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_import_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=100, collation="C"), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit_weight_kg", sa.Numeric(), nullable=False),
        sa.Column("unit_volume_m3", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "unit_volume_m3 >= 0 AND unit_volume_m3::text NOT IN ('NaN','Infinity','-Infinity') AND scale(unit_volume_m3) <= 9",
            name="ck_order_lines_volume",
        ),
        sa.CheckConstraint(
            "unit_weight_kg >= 0 AND unit_weight_kg::text NOT IN ('NaN','Infinity','-Infinity') AND scale(unit_weight_kg) <= 6",
            name="ck_order_lines_weight",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_order_lines_quantity"),
        sa.CheckConstraint("source_row_number > 1", name="ck_order_lines_row"),
        sa.ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "order_id"],
            ["orders.scenario_revision_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "sku", name="uq_order_lines_order_sku"),
    )
    op.create_index(
        "ix_order_lines_revision_sku", "order_lines", ["scenario_revision_id", "sku"], unique=False
    )
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_revision_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_import_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=100, collation="C"), nullable=False),
        sa.Column("distribution_center_id", sa.Uuid(), nullable=False),
        sa.Column("vehicle_type", sa.String(length=100, collation="C"), nullable=False),
        sa.Column("capacity_units", sa.BigInteger(), nullable=False),
        sa.Column("capacity_weight_kg", sa.Numeric(), nullable=False),
        sa.Column("capacity_volume_m3", sa.Numeric(), nullable=False),
        sa.Column("shift_start", sa.Time(), nullable=False),
        sa.Column("shift_end", sa.Time(), nullable=False),
        sa.Column(
            "skills",
            postgresql.ARRAY(sa.String(length=100)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("fixed_cost", sa.Numeric(), nullable=False),
        sa.Column("cost_per_hour", sa.Numeric(), nullable=False),
        sa.Column("cost_per_km", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capacity_volume_m3 > 0 AND capacity_volume_m3::text NOT IN ('NaN','Infinity','-Infinity') AND scale(capacity_volume_m3) <= 9",
            name="ck_vehicles_volume",
        ),
        sa.CheckConstraint(
            "capacity_weight_kg > 0 AND capacity_weight_kg::text NOT IN ('NaN','Infinity','-Infinity') AND scale(capacity_weight_kg) <= 6",
            name="ck_vehicles_weight",
        ),
        sa.CheckConstraint(
            "cost_per_hour >= 0 AND cost_per_hour::text NOT IN ('NaN','Infinity','-Infinity') AND scale(cost_per_hour) <= 4",
            name="ck_vehicles_cost_per_hour",
        ),
        sa.CheckConstraint(
            "cost_per_km >= 0 AND cost_per_km::text NOT IN ('NaN','Infinity','-Infinity') AND scale(cost_per_km) <= 4",
            name="ck_vehicles_cost_per_km",
        ),
        sa.CheckConstraint(
            "fixed_cost >= 0 AND fixed_cost::text NOT IN ('NaN','Infinity','-Infinity') AND scale(fixed_cost) <= 4",
            name="ck_vehicles_fixed_cost",
        ),
        sa.CheckConstraint("capacity_units > 0", name="ck_vehicles_units"),
        sa.CheckConstraint("routeops_valid_skills(skills)", name="ck_vehicles_skills"),
        sa.CheckConstraint("shift_start < shift_end", name="ck_vehicles_shift"),
        sa.CheckConstraint("source_row_number > 1", name="ck_vehicles_row"),
        sa.ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "distribution_center_id"],
            ["distribution_centers.scenario_revision_id", "distribution_centers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_revision_id", "source_id", name="uq_vehicles_source"),
    )
    op.create_index(
        "ix_vehicles_revision_center",
        "vehicles",
        ["scenario_revision_id", "distribution_center_id"],
        unique=False,
    )
    op.add_column("planning_runs", sa.Column("scenario_revision_id", sa.Uuid(), nullable=True))
    op.add_column("planning_runs", sa.Column("inventory_snapshot_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_planning_runs_revision", "planning_runs", ["scenario_revision_id"], unique=False
    )
    op.create_foreign_key(
        "fk_planning_runs_revision_snapshot",
        "planning_runs",
        "inventory_snapshots",
        ["scenario_revision_id", "inventory_snapshot_id"],
        ["scenario_revision_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_planning_runs_revision_snapshot_pair",
        "planning_runs",
        "(scenario_revision_id IS NULL) = (inventory_snapshot_id IS NULL)",
    )
    for table in (
        "import_files",
        "import_batch_events",
        "validation_issues",
        "scenario_revisions",
        "distribution_centers",
        "orders",
        "order_lines",
        "vehicles",
        "inventory_snapshots",
        "inventory_snapshot_lines",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION routeops_reject_mutation()"
        )
    for table, dataset in (
        ("distribution_centers", "distribution_centers"),
        ("orders", "orders"),
        ("order_lines", "order_lines"),
        ("vehicles", "vehicles"),
        ("inventory_snapshot_lines", "inventory"),
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_provenance BEFORE INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION routeops_check_source_dataset('{dataset}')"
        )
    op.execute("""
        CREATE TRIGGER trg_planning_runs_links BEFORE UPDATE OR DELETE ON planning_runs
        FOR EACH ROW EXECUTE FUNCTION routeops_lock_run_links()
    """)
    op.execute("""
        CREATE TRIGGER trg_import_batches_guard BEFORE UPDATE ON import_batches
        FOR EACH ROW EXECUTE FUNCTION routeops_guard_batch_update()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_import_batches_event_match
        AFTER INSERT OR UPDATE ON import_batches DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION routeops_check_batch_event()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_import_batch_events_match
        AFTER INSERT ON import_batch_events DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION routeops_check_batch_event()
    """)


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "scenarios",
        "import_batches",
        "import_files",
        "import_batch_events",
        "validation_issues",
        "scenario_revisions",
        "distribution_centers",
        "orders",
        "order_lines",
        "vehicles",
        "inventory_snapshots",
        "inventory_snapshot_lines",
    ):
        if connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {table})").scalar():
            raise RuntimeError(f"downgrade blocked: {table} contains milestone 2 data")
    if connection.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM planning_runs "
        "WHERE scenario_revision_id IS NOT NULL OR inventory_snapshot_id IS NOT NULL)"
    ).scalar():
        raise RuntimeError("downgrade blocked: planning runs reference milestone 2 data")
    op.execute("DROP TRIGGER trg_planning_runs_links ON planning_runs")
    op.drop_constraint("ck_planning_runs_revision_snapshot_pair", "planning_runs", type_="check")
    op.drop_constraint("fk_planning_runs_revision_snapshot", "planning_runs", type_="foreignkey")
    op.drop_index("ix_planning_runs_revision", table_name="planning_runs")
    op.drop_column("planning_runs", "inventory_snapshot_id")
    op.drop_column("planning_runs", "scenario_revision_id")
    op.drop_index("ix_vehicles_revision_center", table_name="vehicles")
    op.drop_table("vehicles")
    op.drop_index("ix_order_lines_revision_sku", table_name="order_lines")
    op.drop_table("order_lines")
    op.drop_index("ix_inventory_snapshot_lines_revision_sku", table_name="inventory_snapshot_lines")
    op.drop_table("inventory_snapshot_lines")
    op.drop_index("ix_validation_issues_batch_severity", table_name="validation_issues")
    op.drop_table("validation_issues")
    op.drop_index("ix_orders_revision_window", table_name="orders")
    op.drop_index("ix_orders_location", table_name="orders", postgresql_using="gist")
    op.drop_table("orders")
    op.drop_index(
        "uq_inventory_snapshots_imported",
        table_name="inventory_snapshots",
        postgresql_where=sa.text("kind = 'IMPORTED'"),
    )
    op.drop_index("ix_inventory_snapshots_revision_time", table_name="inventory_snapshots")
    op.drop_table("inventory_snapshots")
    op.drop_index(
        "ix_distribution_centers_location",
        table_name="distribution_centers",
        postgresql_using="gist",
    )
    op.drop_table("distribution_centers")
    op.drop_index(
        "ix_scenario_revisions_area", table_name="scenario_revisions", postgresql_using="gist"
    )
    op.drop_table("scenario_revisions")
    op.drop_table("import_files")
    op.drop_table("import_batch_events")
    op.drop_index("ix_import_batches_scenario_status", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_index("ix_scenarios_status_created", table_name="scenarios")
    op.drop_table("scenarios")
    op.execute("DROP FUNCTION routeops_lock_run_links()")
    op.execute("DROP FUNCTION IF EXISTS routeops_check_source_dataset()")
    op.execute("DROP FUNCTION IF EXISTS routeops_check_batch_event()")
    op.execute("DROP FUNCTION IF EXISTS routeops_guard_batch_update()")
    op.execute("DROP FUNCTION routeops_reject_mutation()")
    op.execute("DROP FUNCTION routeops_valid_skills(varchar[])")
