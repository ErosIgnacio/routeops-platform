from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PlanningRunModel(Base):
    __tablename__ = "planning_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scenario_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    kpis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    scenario_revision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    inventory_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "inventory_snapshot_id"],
            ["inventory_snapshots.scenario_revision_id", "inventory_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_planning_runs_revision_snapshot",
        ),
        CheckConstraint(
            "(scenario_revision_id IS NULL) = (inventory_snapshot_id IS NULL)",
            name="ck_planning_runs_revision_snapshot_pair",
        ),
        Index("ix_planning_runs_revision", "scenario_revision_id"),
    )

    routes: Mapped[list[OptimizedRouteModel]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    unassigned: Mapped[list[UnassignedOrderModel]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class OptimizedRouteModel(Base):
    __tablename__ = "optimized_routes"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    distribution_center_id: Mapped[str] = mapped_column(String(100), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    total_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry: Mapped[Any | None] = mapped_column(Geometry("LINESTRING", srid=4326))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    run: Mapped[PlanningRunModel] = relationship(back_populates="routes")

    __table_args__ = (Index("ix_optimized_routes_run_sequence", "run_id", "sequence"),)


class UnassignedOrderModel(Base):
    __tablename__ = "unassigned_orders"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    run: Mapped[PlanningRunModel] = relationship(back_populates="unassigned")

    __table_args__ = (Index("ix_unassigned_orders_run", "run_id"),)


class ScenarioModel(Base):
    __tablename__ = "scenarios"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_scenarios_status"),
        CheckConstraint("version > 0", name="ck_scenarios_version"),
        Index("ix_scenarios_status_created", "status", "created_at"),
    )


class ImportBatchModel(Base):
    __tablename__ = "import_batches"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False
    )
    client_key: Mapped[str | None] = mapped_column(String(200))
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="RECEIVED")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("scenario_id", "id", name="uq_import_batches_scenario_id"),
        UniqueConstraint("scenario_id", "client_key", name="uq_import_batches_client_key"),
        CheckConstraint(
            "status IN ('RECEIVED','VALIDATING','VALID','INVALID','FAILED','PUBLISHED')",
            name="ck_import_batches_status",
        ),
        CheckConstraint("version > 0", name="ck_import_batches_version"),
        CheckConstraint("package_sha256 ~ '^[0-9a-f]{64}$'", name="ck_import_batches_hash"),
        Index("ix_import_batches_scenario_status", "scenario_id", "status", "created_at"),
    )


class ImportFileModel(Base):
    __tablename__ = "import_files"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    dataset: Mapped[str] = mapped_column(String(30), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("batch_id", "id", name="uq_import_files_batch_id"),
        UniqueConstraint("batch_id", "dataset", name="uq_import_files_batch_dataset"),
        UniqueConstraint("storage_key", name="uq_import_files_storage_key"),
        CheckConstraint(
            "dataset IN "
            "('orders','order_lines','inventory','distribution_centers','vehicles','workbook')",
            name="ck_import_files_dataset",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_import_files_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_import_files_hash"),
        CheckConstraint(
            "display_name !~ '[/\\\\]' AND storage_key !~ '[/\\\\]'",
            name="ck_import_files_no_paths",
        ),
    )


class ImportValidationContextModel(Base):
    __tablename__ = "import_validation_contexts"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    context_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("context_sha256 ~ '^[0-9a-f]{64}$'", name="ck_validation_context_hash"),
    )


class ImportValidationJobModel(Base):
    __tablename__ = "import_validation_jobs"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_validation_contexts.batch_id", ondelete="RESTRICT"), primary_key=True
    )
    owner_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_validation_job_attempts"),
        CheckConstraint(
            "(owner_token IS NULL AND lease_until IS NULL) OR "
            "(owner_token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_validation_job_lease",
        ),
        Index("ix_validation_jobs_due", "lease_until", "updated_at"),
    )


class ImportValidationReportModel(Base):
    __tablename__ = "import_validation_reports"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_validation_contexts.batch_id", ondelete="RESTRICT"), primary_key=True
    )
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(40), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(40), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checked_rules: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    deferred_rules: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("package_sha256 ~ '^[0-9a-f]{64}$'", name="ck_validation_report_package"),
        CheckConstraint("context_sha256 ~ '^[0-9a-f]{64}$'", name="ck_validation_report_context"),
        CheckConstraint("report_sha256 ~ '^[0-9a-f]{64}$'", name="ck_validation_report_hash"),
    )


class ImportBatchExpirationModel(Base):
    __tablename__ = "import_batch_expirations"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), primary_key=True
    )
    expired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (CheckConstraint("reason = 'RETENTION'", name="ck_batch_expiration_reason"),)


class ImportFileDeletionModel(Base):
    __tablename__ = "import_file_deletions"

    file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batch_expirations.batch_id", ondelete="RESTRICT"), nullable=False
    )
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("reason = 'RETENTION'", name="ck_file_deletion_reason"),
    )


class ImportBatchEventModel(Base):
    __tablename__ = "import_batch_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("batch_id", "sequence", name="uq_import_batch_events_sequence"),
        CheckConstraint("sequence > 0", name="ck_import_batch_events_sequence"),
        CheckConstraint(
            "to_status IN ('RECEIVED','VALIDATING','VALID','INVALID','FAILED','PUBLISHED')",
            name="ck_import_batch_events_to_status",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('RECEIVED','VALIDATING','VALID','INVALID','FAILED','PUBLISHED')",
            name="ck_import_batch_events_from_status",
        ),
    )


class ValidationIssueModel(Base):
    __tablename__ = "validation_issues"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(30))
    sheet: Mapped[str | None] = mapped_column(String(30))
    row_number: Mapped[int | None] = mapped_column(Integer)
    field: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    value_excerpt: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("batch_id", "ordinal", name="uq_validation_issues_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_validation_issues_ordinal"),
        CheckConstraint("severity IN ('ERROR','WARNING')", name="ck_validation_issues_severity"),
        CheckConstraint("row_number IS NULL OR row_number > 0", name="ck_validation_issues_row"),
        Index("ix_validation_issues_batch_severity", "batch_id", "severity", "code"),
    )


class ScenarioRevisionModel(Base):
    __tablename__ = "scenario_revisions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    planning_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone_iana: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    horizon_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operational_area: Mapped[Any | None] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=False)
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(40), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "import_batch_id"],
            ["import_batches.scenario_id", "import_batches.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scenario_id", "revision_no", name="uq_scenario_revisions_number"),
        UniqueConstraint("import_batch_id", name="uq_scenario_revisions_batch"),
        UniqueConstraint("id", "import_batch_id", name="uq_scenario_revisions_batch_pair"),
        CheckConstraint("revision_no > 0", name="ck_scenario_revisions_number"),
        CheckConstraint("horizon_start_at < horizon_end_at", name="ck_scenario_revisions_horizon"),
        CheckConstraint(
            "(horizon_start_at AT TIME ZONE timezone_iana)::date = planning_date "
            "AND ((horizon_end_at - interval '1 microsecond') "
            "AT TIME ZONE timezone_iana)::date = planning_date",
            name="ck_scenario_revisions_single_day",
        ),
        CheckConstraint("length(trim(timezone_iana)) > 0", name="ck_scenario_revisions_timezone"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_scenario_revisions_currency"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_scenario_revisions_hash"),
        CheckConstraint(
            "operational_area IS NULL OR (ST_SRID(operational_area) = 4326 "
            "AND ST_IsValid(operational_area) AND NOT ST_IsEmpty(operational_area))",
            name="ck_scenario_revisions_area",
        ),
        Index("ix_scenario_revisions_area", "operational_area", postgresql_using="gist"),
    )


class DistributionCenterModel(Base):
    __tablename__ = "distribution_centers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_import_file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    operating_start: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    operating_end: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scenario_revision_id", "id", name="uq_distribution_centers_revision_id"),
        UniqueConstraint(
            "scenario_revision_id", "source_id", name="uq_distribution_centers_source"
        ),
        CheckConstraint("source_row_number > 1", name="ck_distribution_centers_row"),
        CheckConstraint("operating_start < operating_end", name="ck_distribution_centers_hours"),
        CheckConstraint(
            "ST_SRID(location) = 4326 AND ST_IsValid(location) "
            "AND NOT ST_IsEmpty(location) AND ST_X(location) BETWEEN -180 AND 180 "
            "AND ST_Y(location) BETWEEN -90 AND 90",
            name="ck_distribution_centers_location",
        ),
        Index("ix_distribution_centers_location", "location", postgresql_using="gist"),
    )


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_import_file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    customer_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    required_skills: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scenario_revision_id", "id", name="uq_orders_revision_id"),
        UniqueConstraint("scenario_revision_id", "source_id", name="uq_orders_source"),
        CheckConstraint("source_row_number > 1", name="ck_orders_row"),
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_orders_priority"),
        CheckConstraint("service_minutes BETWEEN 1 AND 1440", name="ck_orders_service"),
        CheckConstraint("time_window_start < time_window_end", name="ck_orders_window"),
        CheckConstraint("routeops_valid_skills(required_skills)", name="ck_orders_skills"),
        CheckConstraint(
            "ST_SRID(location) = 4326 AND ST_IsValid(location) "
            "AND NOT ST_IsEmpty(location) AND ST_X(location) BETWEEN -180 AND 180 "
            "AND ST_Y(location) BETWEEN -90 AND 90",
            name="ck_orders_location",
        ),
        Index("ix_orders_location", "location", postgresql_using="gist"),
        Index("ix_orders_revision_window", "scenario_revision_id", "time_window_start"),
    )


class OrderLineModel(Base):
    __tablename__ = "order_lines"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_import_file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sku: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_weight_kg: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit_volume_m3: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scenario_revision_id", "order_id"],
            ["orders.scenario_revision_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("order_id", "sku", name="uq_order_lines_order_sku"),
        CheckConstraint("source_row_number > 1", name="ck_order_lines_row"),
        CheckConstraint("quantity > 0", name="ck_order_lines_quantity"),
        CheckConstraint(
            "unit_weight_kg >= 0 AND unit_weight_kg::text NOT IN ('NaN','Infinity','-Infinity') "
            "AND scale(unit_weight_kg) <= 6",
            name="ck_order_lines_weight",
        ),
        CheckConstraint(
            "unit_volume_m3 >= 0 AND unit_volume_m3::text NOT IN ('NaN','Infinity','-Infinity') "
            "AND scale(unit_volume_m3) <= 9",
            name="ck_order_lines_volume",
        ),
        Index("ix_order_lines_revision_sku", "scenario_revision_id", "sku"),
    )


class VehicleModel(Base):
    __tablename__ = "vehicles"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_import_file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    distribution_center_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    capacity_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capacity_weight_kg: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    capacity_volume_m3: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    shift_start: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    shift_end: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    skills: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    fixed_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cost_per_hour: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cost_per_km: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scenario_revision_id", "distribution_center_id"],
            ["distribution_centers.scenario_revision_id", "distribution_centers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scenario_revision_id", "source_id", name="uq_vehicles_source"),
        CheckConstraint("source_row_number > 1", name="ck_vehicles_row"),
        CheckConstraint("capacity_units > 0", name="ck_vehicles_units"),
        CheckConstraint("shift_start < shift_end", name="ck_vehicles_shift"),
        CheckConstraint("routeops_valid_skills(skills)", name="ck_vehicles_skills"),
        CheckConstraint(
            "capacity_weight_kg > 0 AND capacity_weight_kg::text NOT IN "
            "('NaN','Infinity','-Infinity') AND scale(capacity_weight_kg) <= 6",
            name="ck_vehicles_weight",
        ),
        CheckConstraint(
            "capacity_volume_m3 > 0 AND capacity_volume_m3::text NOT IN "
            "('NaN','Infinity','-Infinity') AND scale(capacity_volume_m3) <= 9",
            name="ck_vehicles_volume",
        ),
        *(
            CheckConstraint(
                f"{column} >= 0 AND {column}::text NOT IN ('NaN','Infinity','-Infinity') "
                f"AND scale({column}) <= 4",
                name=f"ck_vehicles_{column}",
            )
            for column in ("fixed_cost", "cost_per_hour", "cost_per_km")
        ),
        Index("ix_vehicles_revision_center", "scenario_revision_id", "distribution_center_id"),
    )


class InventorySnapshotModel(Base):
    __tablename__ = "inventory_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default="IMPORTED")
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scenario_revision_id", "id", name="uq_inventory_snapshots_revision_id"),
        CheckConstraint("kind IN ('IMPORTED','RUN')", name="ck_inventory_snapshots_kind"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_inventory_snapshots_hash"),
        Index("ix_inventory_snapshots_revision_time", "scenario_revision_id", "snapshot_at"),
        Index(
            "uq_inventory_snapshots_imported",
            "scenario_revision_id",
            unique=True,
            postgresql_where=text("kind = 'IMPORTED'"),
        ),
    )


class InventorySnapshotLineModel(Base):
    __tablename__ = "inventory_snapshot_lines"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_import_file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    distribution_center_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sku: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    on_hand_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    externally_reserved_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    safety_stock_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    routeops_reserved_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    available_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "snapshot_id"],
            ["inventory_snapshots.scenario_revision_id", "inventory_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scenario_revision_id", "import_batch_id"],
            ["scenario_revisions.id", "scenario_revisions.import_batch_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_batch_id", "source_import_file_id"],
            ["import_files.batch_id", "import_files.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scenario_revision_id", "distribution_center_id"],
            ["distribution_centers.scenario_revision_id", "distribution_centers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "snapshot_id",
            "distribution_center_id",
            "sku",
            name="uq_inventory_snapshot_lines_position",
        ),
        CheckConstraint("source_row_number > 1", name="ck_inventory_snapshot_lines_row"),
        CheckConstraint(
            "on_hand_quantity >= 0 AND externally_reserved_quantity >= 0 "
            "AND safety_stock_quantity >= 0 AND routeops_reserved_quantity >= 0",
            name="ck_inventory_snapshot_lines_nonnegative",
        ),
        CheckConstraint(
            "externally_reserved_quantity <= on_hand_quantity "
            "AND safety_stock_quantity <= on_hand_quantity - externally_reserved_quantity "
            "AND routeops_reserved_quantity <= "
            "on_hand_quantity - externally_reserved_quantity - safety_stock_quantity "
            "AND available_quantity = on_hand_quantity - externally_reserved_quantity "
            "- safety_stock_quantity - routeops_reserved_quantity",
            name="ck_inventory_snapshot_lines_available",
        ),
        Index("ix_inventory_snapshot_lines_revision_sku", "scenario_revision_id", "sku"),
    )


class OperationalInventoryStateModel(Base):
    __tablename__ = "operational_inventory_state"

    scenario_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="RESTRICT"), primary_key=True
    )
    active_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenario_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("inventory_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationalInventoryPositionModel(Base):
    __tablename__ = "operational_inventory_positions"

    scenario_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="RESTRICT"), primary_key=True
    )
    center_source_id: Mapped[str] = mapped_column(String(100, collation="C"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(100, collation="C"), primary_key=True)
    source_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenario_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_line_id: Mapped[UUID] = mapped_column(
        ForeignKey("inventory_snapshot_lines.id", ondelete="RESTRICT"), nullable=False
    )
    on_hand_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    externally_reserved_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    safety_stock_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    routeops_reserved_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    available_quantity: Mapped[int] = mapped_column(
        BigInteger,
        Computed(
            "on_hand_quantity - externally_reserved_quantity - "
            "safety_stock_quantity - routeops_reserved_quantity",
            persisted=True,
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "on_hand_quantity >= 0 AND externally_reserved_quantity >= 0 "
            "AND safety_stock_quantity >= 0 AND routeops_reserved_quantity >= 0 "
            "AND externally_reserved_quantity + safety_stock_quantity + "
            "routeops_reserved_quantity <= on_hand_quantity",
            name="ck_operational_inventory_available",
        ),
        Index("ix_operational_inventory_source", "source_revision_id", "source_snapshot_line_id"),
    )


class AllocationAttemptModel(Base):
    __tablename__ = "allocation_attempts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scenario_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenario_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    inventory_snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    client_key: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_revision_id", "inventory_snapshot_id"],
            ["inventory_snapshots.scenario_revision_id", "inventory_snapshots.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scenario_revision_id", "client_key", name="uq_allocation_attempt_key"),
        CheckConstraint("length(client_key) BETWEEN 1 AND 100", name="ck_allocation_attempt_key"),
        CheckConstraint(
            "status IN ('BUILDING','HELD','CONFIRMED','RELEASED')",
            name="ck_allocation_attempt_status",
        ),
        CheckConstraint("version >= 0", name="ck_allocation_attempt_version"),
        Index("ix_allocation_attempts_revision", "scenario_revision_id", "created_at"),
    )


class AllocationDecisionSnapshotModel(Base):
    __tablename__ = "allocation_decision_snapshots"

    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("allocation_attempts.id", ondelete="RESTRICT"), primary_key=True
    )
    inventory_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    inventory_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("inventory_sha256 ~ '^[0-9a-f]{64}$'", name="ck_allocation_snapshot_hash"),
    )


class AllocationOrderDecisionModel(Base):
    __tablename__ = "allocation_order_decisions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("allocation_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    center_source_id: Mapped[str | None] = mapped_column(String(100, collation="C"))
    reason_code: Mapped[str | None] = mapped_column(String(60))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("attempt_id", "order_id", name="uq_allocation_decision_order"),
        UniqueConstraint("attempt_id", "sequence", name="uq_allocation_decision_sequence"),
        CheckConstraint("sequence > 0", name="ck_allocation_decision_sequence"),
        CheckConstraint(
            "(center_source_id IS NULL) = (reason_code IS NOT NULL)",
            name="ck_allocation_decision_outcome",
        ),
    )


class AllocationReservationLineModel(Base):
    __tablename__ = "allocation_reservation_lines"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("allocation_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("allocation_order_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    order_line_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_lines.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    center_source_id: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    sku: Mapped[str] = mapped_column(String(100, collation="C"), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "center_source_id", "sku"],
            [
                "operational_inventory_positions.scenario_id",
                "operational_inventory_positions.center_source_id",
                "operational_inventory_positions.sku",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "attempt_id", "order_line_id", name="uq_allocation_reservation_order_line"
        ),
        CheckConstraint("quantity > 0", name="ck_allocation_reservation_quantity"),
        Index("ix_allocation_reservations_position", "scenario_id", "center_source_id", "sku"),
    )


class AllocationAttemptEventModel(Base):
    __tablename__ = "allocation_attempt_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("allocation_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("attempt_id", "sequence", name="uq_allocation_event_sequence"),
        CheckConstraint("sequence >= 0", name="ck_allocation_event_sequence"),
    )
