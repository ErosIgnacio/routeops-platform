"""Exercise the new schema against a disposable PostgreSQL/PostGIS database."""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import os
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS, LEGACY_SCHEMA, SCHEMA
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_upload import ReceivedFile, ReceivedPackage, UploadError
from routeops.application.import_validation import (
    ImportLimits,
    Issue,
    ValidationReport,
    validate_package,
)
from routeops.application.operating_cost import OperatingCostError
from routeops.application.processing_times import AttemptTimer
from routeops.application.revision_problem import (
    RunInputError,
    WorkloadLimits,
    load_prepared_revision,
)
from routeops.domain.optimization import (
    Certainty,
    OptimizationProblem,
    OptimizationResult,
    OptimizationSummary,
    ResultStatus,
    SolutionQuality,
    SolverMetadata,
    UnassignedReason,
    UnassignedTask,
)
from routeops.domain.policies.operational_allocation import (
    OperationalAllocationPolicy,
    OrderAllocation,
)
from routeops.infrastructure.data.demo_catalog import DemoCatalogService
from routeops.infrastructure.persistence.import_publication import (
    ImportPublicationService,
    PublicationError,
)
from routeops.infrastructure.persistence.import_upload_repository import UploadService
from routeops.infrastructure.persistence.import_validation_jobs import (
    ValidationJobError,
    ValidationJobService,
)
from routeops.infrastructure.persistence.models import (
    AllocationAttemptEventModel,
    AllocationAttemptModel,
    AllocationDecisionSnapshotModel,
    AllocationReservationLineModel,
    DistributionCenterModel,
    ImportBatchEventModel,
    ImportBatchExpirationModel,
    ImportBatchModel,
    ImportFileDeletionModel,
    ImportFileModel,
    ImportValidationContextModel,
    ImportValidationJobModel,
    ImportValidationReportModel,
    InventorySnapshotLineModel,
    InventorySnapshotModel,
    OperationalInventoryPositionModel,
    OperationalInventoryStateModel,
    OptimizedRouteModel,
    OrderLineModel,
    OrderModel,
    PlanningTimingEventModel,
    RevisionRunJobModel,
    ScenarioModel,
    ScenarioRevisionModel,
    ValidationIssueModel,
    VehicleModel,
)
from routeops.infrastructure.persistence.operating_costs import OperatingCostQuery
from routeops.infrastructure.persistence.operational_allocation import (
    AllocationError,
    OperationalAllocationService,
)
from routeops.infrastructure.persistence.plan_metrics import PlanMetricsQuery
from routeops.infrastructure.persistence.planning_data_repository import (
    ImportBatchRepository,
    ScenarioRepository,
    normalize_skills,
    validate_timezone,
)
from routeops.infrastructure.persistence.repository import DatabaseRunRepository
from routeops.infrastructure.persistence.revision_runs import PlanningRunError, RevisionRunService
from routeops.infrastructure.persistence.session import create_session_factory
from routeops.infrastructure.routing.errors import RoutingDependencyError
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.errors import SolverDependencyError
from routeops.infrastructure.solver.vroom import VroomAdapter
from routeops.infrastructure.storage import LocalObjectStorage
from routeops.infrastructure.storage.maintenance import ImportStorageMaintenance

pytestmark = pytest.mark.integration
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
SHA = "a" * 64
OSRM_TEST_URL = os.getenv("ROUTEOPS_TEST_OSRM_URL", "http://127.0.0.1:5000")
VROOM_TEST_URL = os.getenv("ROUTEOPS_TEST_VROOM_URL", "http://127.0.0.1:3000")
TRIGGER_COUNT_SQL = "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_%'"


@contextmanager
def temporary_database() -> Iterator[URL]:
    raw_url = os.getenv("ROUTEOPS_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("set ROUTEOPS_TEST_DATABASE_URL to test PostgreSQL/PostGIS migrations")
    base = make_url(raw_url)
    name = f"routeops_m22_test_{uuid4().hex}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {name}")
        try:
            yield base.set(database=name)
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
    finally:
        admin.dispose()


def migrate(monkeypatch: pytest.MonkeyPatch, url: URL, target: str) -> None:
    monkeypatch.setenv("ROUTEOPS_DATABASE_URL", url.render_as_string(hide_password=False))
    command.upgrade(Config(str(ALEMBIC_INI)), target)


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    with temporary_database() as url:
        migrate(monkeypatch, url, "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


def seed_revision(
    engine: Engine, scenario_id: UUID, number: int, source_id: str = "CD-001"
) -> dict[str, UUID]:
    sessions = create_session_factory(engine)
    batches = ImportBatchRepository(sessions)
    batch_id = batches.create(scenario_id, SHA, "2.1")
    keys = ("file", "revision", "center", "snapshot", "line", "order", "order_line", "vehicle")
    ids = {key: uuid4() for key in keys}
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    with sessions.begin() as session:
        session.add(
            ImportFileModel(
                id=ids["file"],
                batch_id=batch_id,
                dataset="workbook",
                display_name="package.xlsx",
                storage_key=uuid4().hex,
                sha256=SHA,
                size_bytes=100,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ScenarioRevisionModel(
                id=ids["revision"],
                scenario_id=scenario_id,
                import_batch_id=batch_id,
                revision_no=number,
                planning_date=date(2026, 10, 5),
                timezone_iana=validate_timezone("America/Santiago"),
                currency="CLP",
                horizon_start_at=now,
                horizon_end_at=now + timedelta(hours=8),
                operational_area=None,
                content_sha256=SHA,
                contract_version="1",
                published_at=now,
                published_by=None,
            )
        )
        session.flush()
        session.add(
            DistributionCenterModel(
                id=ids["center"],
                scenario_revision_id=ids["revision"],
                import_batch_id=batch_id,
                source_import_file_id=ids["file"],
                source_row_number=2,
                source_row_sha256=SHA,
                source_id=source_id,
                name="Centro",
                location=WKTElement("POINT (-70.65 -33.45)", srid=4326),
                operating_start=time(8),
                operating_end=time(18),
                created_at=now,
            )
        )
        session.add(
            InventorySnapshotModel(
                id=ids["snapshot"],
                scenario_revision_id=ids["revision"],
                import_batch_id=batch_id,
                kind="IMPORTED",
                snapshot_at=now,
                content_sha256=SHA,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            InventorySnapshotLineModel(
                id=ids["line"],
                scenario_revision_id=ids["revision"],
                snapshot_id=ids["snapshot"],
                import_batch_id=batch_id,
                source_import_file_id=ids["file"],
                source_row_number=2,
                source_row_sha256=SHA,
                distribution_center_id=ids["center"],
                sku="SKU-1",
                on_hand_quantity=10,
                externally_reserved_quantity=2,
                safety_stock_quantity=1,
                routeops_reserved_quantity=0,
                available_quantity=7,
                created_at=now,
            )
        )
        session.add(
            OrderModel(
                id=ids["order"],
                scenario_revision_id=ids["revision"],
                import_batch_id=batch_id,
                source_import_file_id=ids["file"],
                source_row_number=2,
                source_row_sha256=SHA,
                source_id="ORD-001",
                customer_reference="demo",
                location=WKTElement("POINT (-70.66 -33.46)", srid=4326),
                priority=1,
                time_window_start=now,
                time_window_end=now + timedelta(hours=2),
                service_minutes=5,
                required_skills=normalize_skills("Cold|fragile|cold"),
                created_at=now,
            )
        )
        session.add(
            VehicleModel(
                id=ids["vehicle"],
                scenario_revision_id=ids["revision"],
                import_batch_id=batch_id,
                source_import_file_id=ids["file"],
                source_row_number=2,
                source_row_sha256=SHA,
                source_id="VEH-001",
                distribution_center_id=ids["center"],
                vehicle_type="van",
                capacity_units=20,
                capacity_weight_kg=Decimal("100"),
                capacity_volume_m3=Decimal("10"),
                shift_start=time(8),
                shift_end=time(18),
                skills=[],
                fixed_cost=Decimal("100.1234"),
                cost_per_hour=Decimal("1"),
                cost_per_km=Decimal("0.1"),
                created_at=now,
            )
        )
        session.flush()
        session.add(
            OrderLineModel(
                id=ids["order_line"],
                scenario_revision_id=ids["revision"],
                import_batch_id=batch_id,
                source_import_file_id=ids["file"],
                source_row_number=2,
                source_row_sha256=SHA,
                order_id=ids["order"],
                sku="SKU-1",
                quantity=2,
                unit_weight_kg=Decimal("1.25"),
                unit_volume_m3=Decimal("0.001"),
                created_at=now,
            )
        )
    ids["batch"] = batch_id
    return ids


def rejects(engine: Engine, statement: str, params: dict[str, object], sqlstate: str) -> None:
    with pytest.raises(DBAPIError) as raised, engine.begin() as connection:
        connection.execute(text(statement), params)
    assert getattr(raised.value.orig, "sqlstate", None) == sqlstate


def insert_complete_revision(
    session: Session, scenario_id: UUID, revision_no: int
) -> dict[str, UUID]:
    """Insert one five-CSV publication in the caller's single transaction."""
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    ids = {
        key: uuid4()
        for key in (
            "batch",
            "revision",
            "center",
            "order",
            "order_line",
            "vehicle",
            "snapshot",
            "stock",
        )
    }
    files = {
        dataset: uuid4()
        for dataset in ("orders", "order_lines", "inventory", "distribution_centers", "vehicles")
    }
    batch = ImportBatchModel(
        id=ids["batch"],
        scenario_id=scenario_id,
        client_key=f"package-{revision_no}",
        package_sha256=SHA,
        parser_version="2.1",
        status="RECEIVED",
        version=1,
        created_at=now,
        transitioned_at=now,
        created_by="integration-test",
    )
    session.add(batch)
    session.flush()
    session.add(
        ImportBatchEventModel(
            id=uuid4(),
            batch_id=batch.id,
            sequence=1,
            from_status=None,
            to_status="RECEIVED",
            occurred_at=now,
            actor="integration-test",
            reason=None,
        )
    )
    for dataset, file_id in files.items():
        session.add(
            ImportFileModel(
                id=file_id,
                batch_id=batch.id,
                dataset=dataset,
                display_name=f"{dataset}.csv",
                storage_key=uuid4().hex,
                sha256=SHA,
                size_bytes=100,
                created_at=now,
            )
        )
    session.flush()
    for sequence, before, after in ((2, "RECEIVED", "VALIDATING"), (3, "VALIDATING", "VALID")):
        changed_at = now + timedelta(seconds=sequence)
        batch.status = after
        batch.version = sequence
        batch.transitioned_at = changed_at
        session.add(
            ImportBatchEventModel(
                id=uuid4(),
                batch_id=batch.id,
                sequence=sequence,
                from_status=before,
                to_status=after,
                occurred_at=changed_at,
                actor="integration-test",
                reason=None,
            )
        )
        session.flush()
    session.add(
        ScenarioRevisionModel(
            id=ids["revision"],
            scenario_id=scenario_id,
            import_batch_id=batch.id,
            revision_no=revision_no,
            planning_date=date(2026, 10, 5),
            timezone_iana="America/Santiago",
            currency="CLP",
            horizon_start_at=now,
            horizon_end_at=now + timedelta(hours=8),
            operational_area=None,
            content_sha256=SHA,
            contract_version="1",
            published_at=now,
            published_by="integration-test",
        )
    )
    session.flush()
    common = {
        "scenario_revision_id": ids["revision"],
        "import_batch_id": batch.id,
        "source_row_number": 2,
        "source_row_sha256": SHA,
        "created_at": now,
    }
    session.add(
        DistributionCenterModel(
            id=ids["center"],
            source_import_file_id=files["distribution_centers"],
            source_id="CD-001",
            name="Centro",
            location=WKTElement("POINT (-70.65 -33.45)", srid=4326),
            operating_start=time(8),
            operating_end=time(18),
            **common,
        )
    )
    session.add(
        OrderModel(
            id=ids["order"],
            source_import_file_id=files["orders"],
            source_id="ORD-001",
            customer_reference="test",
            location=WKTElement("POINT (-70.66 -33.46)", srid=4326),
            priority=1,
            time_window_start=now,
            time_window_end=now + timedelta(hours=2),
            service_minutes=5,
            required_skills=["cold"],
            **common,
        )
    )
    session.add(
        InventorySnapshotModel(
            id=ids["snapshot"],
            scenario_revision_id=ids["revision"],
            import_batch_id=batch.id,
            kind="IMPORTED",
            snapshot_at=now,
            content_sha256=SHA,
            created_at=now,
        )
    )
    session.flush()
    session.add(
        OrderLineModel(
            id=ids["order_line"],
            source_import_file_id=files["order_lines"],
            order_id=ids["order"],
            sku="SKU-1",
            quantity=2,
            unit_weight_kg=Decimal("1.25"),
            unit_volume_m3=Decimal("0.001"),
            **common,
        )
    )
    session.add(
        VehicleModel(
            id=ids["vehicle"],
            source_import_file_id=files["vehicles"],
            source_id="VEH-001",
            distribution_center_id=ids["center"],
            vehicle_type="van",
            capacity_units=20,
            capacity_weight_kg=Decimal("100"),
            capacity_volume_m3=Decimal("10"),
            shift_start=time(8),
            shift_end=time(18),
            skills=["cold"],
            fixed_cost=Decimal("100.1234"),
            cost_per_hour=Decimal("1"),
            cost_per_km=Decimal("0.1"),
            **common,
        )
    )
    session.add(
        InventorySnapshotLineModel(
            id=ids["stock"],
            source_import_file_id=files["inventory"],
            snapshot_id=ids["snapshot"],
            distribution_center_id=ids["center"],
            sku="SKU-1",
            on_hand_quantity=10,
            externally_reserved_quantity=2,
            safety_stock_quantity=1,
            routeops_reserved_quantity=0,
            available_quantity=7,
            **common,
        )
    )
    session.flush()
    published_at = now + timedelta(seconds=4)
    batch.status = "PUBLISHED"
    batch.version = 4
    batch.transitioned_at = published_at
    session.add(
        ImportBatchEventModel(
            id=uuid4(),
            batch_id=batch.id,
            sequence=4,
            from_status="VALID",
            to_status="PUBLISHED",
            occurred_at=published_at,
            actor="integration-test",
            reason=None,
        )
    )
    return ids


def test_complete_transaction_retry_boundary_and_revision_isolation(database: Engine) -> None:
    sessions = create_session_factory(database)
    scenario_id = uuid4()
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    with sessions.begin() as session:
        session.add(
            ScenarioModel(
                id=scenario_id,
                name="Integrated",
                status="ACTIVE",
                version=1,
                created_at=now,
                updated_at=now,
                created_by="integration-test",
            )
        )
        session.flush()
        first = insert_complete_revision(session, scenario_id, 1)

    with sessions() as session:
        assert session.get(ScenarioModel, scenario_id).name == "Integrated"  # type: ignore[union-attr]
        assert session.get(ImportBatchModel, first["batch"]).status == "PUBLISHED"  # type: ignore[union-attr]
        assert session.get(ScenarioRevisionModel, first["revision"]).revision_no == 1  # type: ignore[union-attr]
        assert session.get(OrderModel, first["order"]).source_id == "ORD-001"  # type: ignore[union-attr]
        assert session.get(OrderLineModel, first["order_line"]).order_id == first["order"]  # type: ignore[union-attr]
        assert session.get(VehicleModel, first["vehicle"]).distribution_center_id == first["center"]  # type: ignore[union-attr]
        assert session.get(InventorySnapshotLineModel, first["stock"]).available_quantity == 7  # type: ignore[union-attr]
        assert session.get(DistributionCenterModel, first["center"]) is not None
        assert session.get(InventorySnapshotModel, first["snapshot"]) is not None
        assert (
            session.scalar(
                select(ImportFileModel).where(ImportFileModel.batch_id == first["batch"])
            )
            is not None
        )
        assert (
            len(
                session.scalars(
                    select(ImportFileModel).where(ImportFileModel.batch_id == first["batch"])
                ).all()
            )
            == 5
        )

    with sessions.begin() as session:
        second = insert_complete_revision(session, scenario_id, 2)
    assert len(ScenarioRepository(sessions).revisions(scenario_id)) == 2
    with sessions() as session:
        one = session.get(OrderModel, first["order"])
        two = session.get(OrderModel, second["order"])
        assert one is not None and two is not None
        assert one.source_id == two.source_id == "ORD-001"
        assert one.scenario_revision_id != two.scenario_revision_id
        first_vehicle = session.get(VehicleModel, first["vehicle"])
        second_vehicle = session.get(VehicleModel, second["vehicle"])
        assert first_vehicle is not None and second_vehicle is not None
        assert first_vehicle.distribution_center_id == first["center"]
        assert second_vehicle.distribution_center_id == second["center"]
    rejects(
        database,
        "INSERT INTO order_lines SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, :foreign, "
        "'SKU-2', quantity, unit_weight_kg, unit_volume_m3, created_at "
        "FROM order_lines WHERE id=:copy",
        {"id": uuid4(), "foreign": second["order"], "copy": first["order_line"]},
        "23503",
    )

    failed_batch = ImportBatchRepository(sessions).create(scenario_id, SHA, "2.1")
    failed_file = uuid4()
    with pytest.raises(DBAPIError) as raised, sessions.begin() as session:
        batch = session.get(ImportBatchModel, failed_batch)
        assert batch is not None
        batch.status = "VALIDATING"
        batch.version = 2
        batch.transitioned_at = datetime.now(UTC)
        session.add(
            ImportFileModel(
                id=failed_file,
                batch_id=failed_batch,
                dataset="orders",
                display_name="orders.csv",
                storage_key=uuid4().hex,
                sha256=SHA,
                size_bytes=10,
                created_at=datetime.now(UTC),
            )
        )
    assert getattr(raised.value.orig, "sqlstate", None) == "23514"
    with sessions() as session:
        batch = session.get(ImportBatchModel, failed_batch)
        assert batch is not None and batch.status == "RECEIVED" and batch.version == 1
        assert session.get(ImportFileModel, failed_file) is None
        events = session.scalars(
            select(ImportBatchEventModel).where(ImportBatchEventModel.batch_id == failed_batch)
        ).all()
        assert [(event.sequence, event.to_status) for event in events] == [(1, "RECEIVED")]


def test_allowed_downgrade_removes_routeops_objects_only(monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "525a2c8f3a8c")
        engine = create_engine(url)
        with engine.connect() as connection:
            extension_oid = connection.scalar(
                text("SELECT oid FROM pg_extension WHERE extname='postgis'")
            )
            assert extension_oid is not None
            postgis_objects = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_depend WHERE refclassid='pg_extension'::regclass "
                    "AND refobjid=:oid"
                ),
                {"oid": extension_oid},
            )
            assert connection.scalar(text(TRIGGER_COUNT_SQL)) == 19
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'routeops_%'")
                )
                == 6
            )
        command.downgrade(Config(str(ALEMBIC_INI)), "20260924_0001")
        with engine.connect() as connection:
            assert connection.scalar(text(TRIGGER_COUNT_SQL)) == 0
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'routeops_%'")
                )
                == 0
            )
            assert (
                connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'"))
                == extension_oid
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_depend WHERE refclassid='pg_extension'::regclass "
                        "AND refobjid=:oid"
                    ),
                    {"oid": extension_oid},
                )
                == postgis_objects
            )
            assert (
                connection.scalar(text("SELECT ST_SRID(ST_GeomFromText('POINT (0 0)', 4326))"))
                == 4326
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                        "AND tablename IN ('planning_runs','optimized_routes','unassigned_orders')"
                    )
                )
                == 3
            )
        engine.dispose()


def test_validation_migration_downgrade_keeps_postgis_and_previous_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "8db12e7c5f09")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                before = connection.scalar(text(TRIGGER_COUNT_SQL))
                postgis_oid = connection.scalar(
                    text("SELECT oid FROM pg_extension WHERE extname='postgis'")
                )
            migrate(monkeypatch, url, "7a69c4d10e32")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == before + 2
            command.downgrade(Config(str(ALEMBIC_INI)), "8db12e7c5f09")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == before
                assert (
                    connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'"))
                    == postgis_oid
                )
        finally:
            engine.dispose()


def test_upgrade_preserves_hito_1_and_empty_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "20260924_0001")
        engine = create_engine(url)
        run_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO planning_runs
                      (id, scenario_name, status, started_at, input_data)
                    VALUES (:id, 'legacy demo', 'RUNNING', :started, '{}'::jsonb)
                """),
                {"id": run_id, "started": datetime.now(UTC)},
            )
            connection.execute(
                text("""
                    INSERT INTO optimized_routes
                      (id, run_id, vehicle_id, source_vehicle_id, distribution_center_id,
                       sequence, distance_meters, total_duration_seconds, payload)
                    VALUES (:id, :run, :vehicle, 'VEH-DEMO', 'CD-DEMO', 0, 100, 60, '{}'::jsonb)
                """),
                {"id": uuid4(), "run": run_id, "vehicle": uuid4()},
            )
            connection.execute(
                text("""
                    INSERT INTO unassigned_orders (id, run_id, order_id, stage, reasons)
                    VALUES (:id, :run, 'ORD-003', 'ALLOCATION', '[]'::jsonb)
                """),
                {"id": uuid4(), "run": run_id},
            )
        migrate(monkeypatch, url, "head")
        repo = DatabaseRunRepository(create_session_factory(engine))
        assert repo.get(run_id)["scenario_name"] == "legacy demo"  # type: ignore[index]
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT scenario_revision_id IS NULL AND inventory_snapshot_id IS NULL "
                    "FROM planning_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM optimized_routes WHERE run_id=:id"), {"id": run_id}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM unassigned_orders WHERE run_id=:id"),
                    {"id": run_id},
                )
                == 1
            )
        command.downgrade(Config(str(ALEMBIC_INI)), "20260924_0001")
        migrate(monkeypatch, url, "head")
        assert repo.get(run_id)["scenario_name"] == "legacy demo"  # type: ignore[index]
        engine.dispose()


def test_revisions_keys_links_and_downgrade_guard(
    database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = ScenarioRepository(create_session_factory(database))
    scenario_id = scenarios.create("Test")
    one = seed_revision(database, scenario_id, 1)
    two = seed_revision(database, scenario_id, 2)
    assert len(scenarios.revisions(scenario_id)) == 2
    assert one["center"] != two["center"]
    third_batch = ImportBatchRepository(create_session_factory(database)).create(
        scenario_id, SHA, "2.1"
    )
    rejects(
        database,
        "INSERT INTO scenario_revisions SELECT :id, scenario_id, :batch, 3, "
        "planning_date, timezone_iana, currency, horizon_start_at, "
        "horizon_start_at + interval '2 days', operational_area, content_sha256, "
        "contract_version, published_at, published_by "
        "FROM scenario_revisions WHERE id=:copy",
        {"id": uuid4(), "batch": third_batch, "copy": one["revision"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO distribution_centers SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, source_id, name, "
        "location, operating_start, operating_end, created_at "
        "FROM distribution_centers WHERE id=:copy",
        {"id": uuid4(), "copy": one["center"]},
        "23505",
    )
    rejects(
        database,
        "INSERT INTO vehicles SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, 'VEH-002', "
        ":foreign, vehicle_type, capacity_units, capacity_weight_kg, capacity_volume_m3, "
        "shift_start, shift_end, skills, fixed_cost, cost_per_hour, cost_per_km, created_at "
        "FROM vehicles WHERE id=:copy",
        {"id": uuid4(), "foreign": two["center"], "copy": one["vehicle"]},
        "23503",
    )
    rejects(
        database,
        "INSERT INTO planning_runs (id, scenario_name, status, started_at, input_data, "
        "scenario_revision_id, inventory_snapshot_id) VALUES "
        "(:id, 'bad', 'RUNNING', now(), '{}'::jsonb, :revision, :snapshot)",
        {"id": uuid4(), "revision": one["revision"], "snapshot": two["snapshot"]},
        "23503",
    )
    rejects(
        database,
        "INSERT INTO planning_runs (id, scenario_name, status, started_at, input_data, "
        "scenario_revision_id) VALUES (:id, 'bad', 'RUNNING', now(), '{}'::jsonb, :revision)",
        {"id": uuid4(), "revision": one["revision"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO order_lines SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, :foreign, "
        "'SKU-2', quantity, unit_weight_kg, unit_volume_m3, created_at "
        "FROM order_lines WHERE id=:copy",
        {"id": uuid4(), "foreign": two["order"], "copy": one["order_line"]},
        "23503",
    )
    rejects(
        database,
        "INSERT INTO order_lines SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, order_id, "
        "sku, quantity, unit_weight_kg, unit_volume_m3, created_at "
        "FROM order_lines WHERE id=:copy",
        {"id": uuid4(), "copy": one["order_line"]},
        "23505",
    )
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO planning_runs (id, scenario_name, status, started_at, input_data) "
                "VALUES (:id, 'demo', 'RUNNING', now(), '{}'::jsonb)"
            ),
            {"id": uuid4()},
        )
    with pytest.raises(RuntimeError, match="downgrade blocked"):
        command.downgrade(Config(str(ALEMBIC_INI)), "20260924_0001")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c951e2a7d430"


def test_quantities_costs_geometry_and_immutability(database: Engine) -> None:
    scenario_id = ScenarioRepository(create_session_factory(database)).create("Test")
    ids = seed_revision(database, scenario_id, 1)
    wrong_file_id = uuid4()
    with Session(database) as session, session.begin():
        session.add(
            ImportFileModel(
                id=wrong_file_id,
                batch_id=ids["batch"],
                dataset="inventory",
                display_name="inventory.csv",
                storage_key=uuid4().hex,
                sha256=SHA,
                size_bytes=100,
                created_at=datetime.now(UTC),
            )
        )
    rejects(
        database,
        "INSERT INTO orders SELECT :id, scenario_revision_id, import_batch_id, "
        ":wrong_file, source_row_number, source_row_sha256, 'ORD-NEW', "
        "customer_reference, location, priority, time_window_start, time_window_end, "
        "service_minutes, required_skills, created_at FROM orders WHERE id=:copy",
        {"id": uuid4(), "wrong_file": wrong_file_id, "copy": ids["order"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO inventory_snapshot_lines SELECT :id, scenario_revision_id, snapshot_id, "
        "import_batch_id, source_import_file_id, source_row_number, source_row_sha256, "
        "distribution_center_id, 'SKU-2', -1, 0, 0, 0, -1, created_at "
        "FROM inventory_snapshot_lines WHERE id=:copy",
        {"id": uuid4(), "copy": ids["line"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO inventory_snapshot_lines SELECT :id, scenario_revision_id, snapshot_id, "
        "import_batch_id, source_import_file_id, source_row_number, source_row_sha256, "
        "distribution_center_id, 'SKU-3', 1, 2, 0, 0, -1, created_at "
        "FROM inventory_snapshot_lines WHERE id=:copy",
        {"id": uuid4(), "copy": ids["line"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO order_lines SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, order_id, "
        "'SKU-3', 0, unit_weight_kg, unit_volume_m3, created_at "
        "FROM order_lines WHERE id=:copy",
        {"id": uuid4(), "copy": ids["order_line"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO vehicles SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, 'VEH-002', "
        "distribution_center_id, vehicle_type, capacity_units, capacity_weight_kg, "
        "capacity_volume_m3, shift_start, shift_end, skills, -1, cost_per_hour, "
        "cost_per_km, created_at FROM vehicles WHERE id=:copy",
        {"id": uuid4(), "copy": ids["vehicle"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO distribution_centers SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, 'CD-002', name, "
        "ST_SetSRID(ST_Point(181, 0), 4326), operating_start, operating_end, created_at "
        "FROM distribution_centers WHERE id=:copy",
        {"id": uuid4(), "copy": ids["center"]},
        "23514",
    )
    rejects(
        database,
        "INSERT INTO orders SELECT :id, scenario_revision_id, import_batch_id, "
        "source_import_file_id, source_row_number, source_row_sha256, 'ORD-NEW', "
        "customer_reference, location, priority, time_window_start, time_window_end, "
        "service_minutes, ARRAY['Cold']::varchar[], created_at "
        "FROM orders WHERE id=:copy",
        {"id": uuid4(), "copy": ids["order"]},
        "23514",
    )
    for table, row_id in (
        ("scenario_revisions", ids["revision"]),
        ("inventory_snapshots", ids["snapshot"]),
        ("inventory_snapshot_lines", ids["line"]),
        ("orders", ids["order"]),
        ("order_lines", ids["order_line"]),
    ):
        rejects(database, f"DELETE FROM {table} WHERE id=:id", {"id": row_id}, "55000")
        rejects(database, f"UPDATE {table} SET id=id WHERE id=:id", {"id": row_id}, "55000")
    run_id = uuid4()
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO planning_runs (id, scenario_name, status, started_at, input_data, "
                "scenario_revision_id, inventory_snapshot_id) VALUES "
                "(:id, 'linked', 'RUNNING', now(), '{}'::jsonb, :revision, :snapshot)"
            ),
            {"id": run_id, "revision": ids["revision"], "snapshot": ids["snapshot"]},
        )
    rejects(database, "DELETE FROM planning_runs WHERE id=:id", {"id": run_id}, "55000")
    rejects(
        database,
        "UPDATE planning_runs SET scenario_revision_id=NULL WHERE id=:id",
        {"id": run_id},
        "55000",
    )


def test_batch_state_events_and_skills(database: Engine) -> None:
    sessions = create_session_factory(database)
    scenario_id = ScenarioRepository(sessions).create("Test")
    batches = ImportBatchRepository(sessions)
    batch_id = batches.create(scenario_id, SHA, "2.1")
    rejects(
        database,
        "UPDATE import_batches SET package_sha256=:other WHERE id=:id",
        {"other": "b" * 64, "id": batch_id},
        "55000",
    )
    rejects(
        database,
        "UPDATE import_batches SET status='VALIDATING', version=2, "
        "transitioned_at=now() WHERE id=:id",
        {"id": batch_id},
        "23514",
    )
    assert batches.transition(batch_id, "VALIDATING") == 2
    assert batches.transition(batch_id, "VALID") == 3
    assert batches.get(batch_id).status == "VALID"  # type: ignore[union-attr]
    with Session(database) as session:
        events = session.scalars(
            select(ImportBatchEventModel)
            .where(ImportBatchEventModel.batch_id == batch_id)
            .order_by(ImportBatchEventModel.sequence)
        ).all()
    assert [(event.sequence, event.to_status) for event in events] == [
        (1, "RECEIVED"),
        (2, "VALIDATING"),
        (3, "VALID"),
    ]
    with pytest.raises(ValueError, match="invalid import batch transition"):
        batches.transition(batch_id, "PUBLISHED")
    rejects(
        database,
        "INSERT INTO import_batch_events (id, batch_id, sequence, to_status, occurred_at) "
        "VALUES (:id, :batch, 3, 'VALID', now())",
        {"id": uuid4(), "batch": batch_id},
        "23505",
    )
    assert normalize_skills("Cold|fragile|cold") == ["cold", "fragile"]
    with pytest.raises(ValueError):
        normalize_skills("bad skill")


def _stored_package(storage: LocalObjectStorage, *, suffix: bytes = b"") -> ReceivedPackage:
    files: list[ReceivedFile] = []
    for dataset in DATASETS:
        writer = storage.begin()
        writer.write(dataset.encode("ascii") + suffix)
        files.append(ReceivedFile(dataset, f"{dataset}.csv", writer.finish()))
    manifest = hashlib.sha256()
    for item in sorted(files, key=lambda file: file.dataset):
        manifest.update(
            f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode("ascii")
        )
    return ReceivedPackage(tuple(files), manifest.hexdigest())


def _valid_package(storage: LocalObjectStorage, *, workbook: bool = False) -> ReceivedPackage:
    files: list[ReceivedFile] = []
    for dataset in ("workbook",) if workbook else DATASETS:
        writer = storage.begin()
        writer.write(xlsx_template() if workbook else csv_template(dataset))
        files.append(
            ReceivedFile(
                dataset, f"{dataset}.xlsx" if workbook else f"{dataset}.csv", writer.finish()
            )
        )
    manifest = hashlib.sha256()
    for item in sorted(files, key=lambda file: file.dataset):
        manifest.update(
            f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode("ascii")
        )
    return ReceivedPackage(tuple(files), manifest.hexdigest())


def _publication_rows() -> dict[str, list[dict[str, str]]]:
    return {
        "orders": [
            {
                "order_id": "ORD-001",
                "customer_reference": "Cliente",
                "latitude": "-33.45",
                "longitude": "-70.65",
                "priority": "1",
                "time_window_start": "2026-10-15T10:00:00-03:00",
                "time_window_end": "2026-10-15T11:00:00-03:00",
                "service_minutes": "10",
                "required_skills": "cold",
            }
        ],
        "order_lines": [
            {
                "order_id": "ORD-001",
                "sku": "SKU-1",
                "quantity": "2",
                "unit_weight_kg": "1.25",
                "unit_volume_m3": "0.002",
            }
        ],
        "inventory": [
            {
                "snapshot_at": "2026-10-15T08:00:00-03:00",
                "distribution_center_id": "CD-001",
                "sku": "SKU-1",
                "on_hand_quantity": "10",
                "externally_reserved_quantity": "2",
                "safety_stock_quantity": "1",
            }
        ],
        "distribution_centers": [
            {
                "distribution_center_id": "CD-001",
                "name": "Centro",
                "latitude": "-33.44",
                "longitude": "-70.64",
                "operating_start": "08:00",
                "operating_end": "18:00",
            }
        ],
        "vehicles": [
            {
                "vehicle_id": "VEH-001",
                "distribution_center_id": "CD-001",
                "vehicle_type": "van",
                "capacity_units": "20",
                "capacity_weight_kg": "100",
                "capacity_volume_m3": "10",
                "shift_start": "08:00",
                "shift_end": "18:00",
                "skills": "cold",
                "fixed_cost": "100",
                "cost_per_hour": "10",
                "cost_per_km": "1",
            }
        ],
    }


def _publication_package(
    storage: LocalObjectStorage,
    rows: dict[str, list[dict[str, str]]],
    *,
    workbook: bool = False,
) -> ReceivedPackage:
    csv_files: dict[str, bytes] = {}
    for name in DATASETS:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=[spec.name for spec in SCHEMA[name]])
        writer.writeheader()
        writer.writerows(rows[name])
        csv_files[name] = stream.getvalue().encode()
    payloads: dict[str, bytes]
    if workbook:
        source = io.BytesIO(xlsx_template())
        target = io.BytesIO()
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as result:
            for entry in original.infolist():
                payload = original.read(entry.filename)
                for index, name in enumerate(DATASETS, start=1):
                    if entry.filename != f"xl/worksheets/sheet{index}.xml":
                        continue
                    sheet = ET.fromstring(payload)
                    sheet_data = sheet.find(f"{ns}sheetData")
                    assert sheet_data is not None
                    for number, values in enumerate(rows[name], start=2):
                        xml_row = ET.SubElement(sheet_data, f"{ns}row", r=str(number))
                        for column, spec in enumerate(SCHEMA[name]):
                            cell = ET.SubElement(
                                xml_row, f"{ns}c", r=f"{chr(65 + column)}{number}", t="inlineStr"
                            )
                            inline = ET.SubElement(cell, f"{ns}is")
                            ET.SubElement(inline, f"{ns}t").text = values.get(spec.name, "")
                    payload = ET.tostring(sheet, encoding="utf-8", xml_declaration=True)
                result.writestr(entry, payload)
        payloads = {"workbook": target.getvalue()}
    else:
        payloads = csv_files
    files: list[ReceivedFile] = []
    for name, payload in payloads.items():
        writer = storage.begin()
        writer.write(payload)
        files.append(
            ReceivedFile(name, f"{name}.xlsx" if workbook else f"{name}.csv", writer.finish())
        )
    manifest = hashlib.sha256()
    for item in sorted(files, key=lambda file: file.dataset):
        manifest.update(
            f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode("ascii")
        )
    return ReceivedPackage(tuple(files), manifest.hexdigest())


def _publication_services(
    engine: Engine, storage: LocalObjectStorage, tmp_path: Path
) -> tuple[UploadService, ValidationJobService, ImportPublicationService]:
    sessions = create_session_factory(engine)
    validation = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=3
    )
    return (
        UploadService(sessions, storage),
        validation,
        ImportPublicationService(sessions, validation, tmp_path, retention_days=30),
    )


def _validated_publication_batch(
    scenario: UUID,
    key: str,
    rows: dict[str, list[dict[str, str]]],
    services: tuple[UploadService, ValidationJobService, ImportPublicationService],
    storage: LocalObjectStorage,
    *,
    workbook: bool = False,
) -> UUID:
    upload, validation, _ = services
    batch, _ = upload.create(scenario, key, _publication_package(storage, rows, workbook=workbook))
    validation.request(scenario, batch, _validation_context())
    claim = validation.claim()
    assert claim is not None and claim[0] == batch
    contents, context, _ = validation._read(batch)
    report = validate_package(contents, context=context)
    assert report.valid, report.to_dict()
    assert validation.finish(batch, claim[1], report)
    return batch


def _validation_context(*, currency: str = "CLP") -> ValidationContext:
    return ValidationContext(
        planning_date=date(2026, 10, 15),
        horizon_start_at=datetime.fromisoformat("2026-10-15T00:00:00-03:00"),
        horizon_end_at=datetime.fromisoformat("2026-10-15T23:59:59-03:00"),
        timezone_iana="America/Santiago",
        currency=currency,
    )


@pytest.mark.parametrize("workbook", [False, True])
def test_publication_persists_verified_rows_and_is_idempotent(
    database: Engine, tmp_path: Path, workbook: bool
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Publication")
    batch = _validated_publication_batch(
        scenario, "first", _publication_rows(), services, storage, workbook=workbook
    )
    publisher = services[2]
    revision, created = publisher.publish(scenario, batch)
    assert created and revision["revision_no"] == 1
    with sessions() as session:
        revision_id = UUID(revision["id"])
        assert (
            session.scalar(
                select(func.count())
                .select_from(OrderModel)
                .where(OrderModel.scenario_revision_id == revision_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OrderLineModel)
                .where(OrderLineModel.scenario_revision_id == revision_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(VehicleModel)
                .where(VehicleModel.scenario_revision_id == revision_id)
            )
            == 1
        )
        line = session.scalar(
            select(InventorySnapshotLineModel).where(
                InventorySnapshotLineModel.scenario_revision_id == revision_id
            )
        )
        assert line is not None and line.available_quantity == 7
        assert line.externally_reserved_quantity == 2
        assert session.get(ImportBatchModel, batch).status == "PUBLISHED"  # type: ignore[union-attr]
        assert [
            item.to_status
            for item in session.scalars(
                select(ImportBatchEventModel)
                .where(ImportBatchEventModel.batch_id == batch)
                .order_by(ImportBatchEventModel.sequence)
            )
        ] == ["RECEIVED", "VALIDATING", "VALID", "PUBLISHED"]
    for item in storage.objects.iterdir():
        item.unlink()
    repeated, created = publisher.publish(scenario, batch)
    assert not created and repeated == revision
    assert publisher.get_revision(scenario, 1)["snapshot_at"] is not None
    assert publisher.list_revisions(scenario, limit=1)["items"] == [revision]


def test_publication_rolls_back_and_preserves_previous_revisions(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Revisions")
    first = _validated_publication_batch(scenario, "one", _publication_rows(), services, storage)
    publisher = services[2]
    initial, _ = publisher.publish(scenario, first)
    second = _validated_publication_batch(scenario, "two", _publication_rows(), services, storage)
    original = publisher._insert_datasets

    def broken(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected after insertion")

    monkeypatch.setattr(publisher, "_insert_datasets", broken)
    with pytest.raises(RuntimeError, match="injected"):
        publisher.publish(scenario, second)
    with sessions() as session:
        assert session.get(ImportBatchModel, second).status == "VALID"  # type: ignore[union-attr]
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScenarioRevisionModel)
                .where(ScenarioRevisionModel.scenario_id == scenario)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DistributionCenterModel)
                .where(DistributionCenterModel.import_batch_id == second)
            )
            == 0
        )
    monkeypatch.setattr(publisher, "_insert_datasets", original)
    next_revision, _ = publisher.publish(scenario, second)
    assert next_revision["revision_no"] == 2
    assert publisher.get_revision(scenario, 1)["id"] == initial["id"]
    first_page = publisher.list_revisions(scenario, limit=1)
    assert first_page == {"items": [initial], "next_after": 1}
    assert publisher.list_revisions(scenario, after=1, limit=1) == {
        "items": [next_revision],
        "next_after": None,
    }


def test_publication_rejects_empty_expired_and_tampered_packages(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Rejection")
    empty = _validated_publication_batch(
        scenario, "empty", {name: [] for name in DATASETS}, services, storage
    )
    with pytest.raises(PublicationError, match="PUBLISH_PACKAGE_EMPTY"):
        services[2].publish(scenario, empty)
    no_inventory = _publication_rows()
    no_inventory["inventory"] = []
    incomplete = _validated_publication_batch(
        scenario, "no-inventory", no_inventory, services, storage
    )
    with pytest.raises(PublicationError, match="PUBLISH_INVENTORY_EMPTY"):
        services[2].publish(scenario, incomplete)
    expired = _validated_publication_batch(
        scenario, "expired", _publication_rows(), services, storage
    )
    with sessions.begin() as session:
        session.add(
            ImportBatchExpirationModel(
                batch_id=expired, expired_at=datetime.now(UTC), reason="RETENTION"
            )
        )
    with pytest.raises(PublicationError, match="BATCH_EXPIRED"):
        services[2].publish(scenario, expired)
    tampered = _validated_publication_batch(
        scenario, "tampered", _publication_rows(), services, storage
    )
    with sessions() as session:
        file = session.scalar(select(ImportFileModel).where(ImportFileModel.batch_id == tampered))
        assert file is not None
        (storage.objects / file.storage_key).write_bytes(b"tampered")
    with pytest.raises(PublicationError, match="VALIDATED_SOURCE_UNAVAILABLE"):
        services[2].publish(scenario, tampered)


def test_publication_concurrent_batches_get_unique_revisions(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Concurrent")
    batches = [
        _validated_publication_batch(
            scenario, f"concurrent-{index}", _publication_rows(), services, storage
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(services[2].publish, scenario, batch) for batch in batches * 2]
        results = [future.result() for future in futures]
    assert sorted({result[0]["revision_no"] for result in results}) == [1, 2]
    assert sum(created for _, created in results) == 2
    assert len({result[0]["id"] for result in results}) == 2


def test_publication_expiration_wins_after_verified_replay(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Expiry race")
    batch = _validated_publication_batch(
        scenario, "expiry-race", _publication_rows(), services, storage
    )
    replayed = Event()
    proceed = Event()
    original = services[1].replay_verified_for_publication

    def delayed(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        replayed.set()
        assert proceed.wait(10)
        return result

    monkeypatch.setattr(services[1], "replay_verified_for_publication", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(services[2].publish, scenario, batch)
        assert replayed.wait(10)
        with sessions.begin() as session:
            session.add(
                ImportBatchExpirationModel(
                    batch_id=batch, expired_at=datetime.now(UTC), reason="RETENTION"
                )
            )
        proceed.set()
        with pytest.raises(PublicationError, match="BATCH_EXPIRED"):
            future.result()
    with sessions() as session:
        assert (
            session.scalar(
                select(ScenarioRevisionModel.id).where(
                    ScenarioRevisionModel.import_batch_id == batch
                )
            )
            is None
        )
        saved = session.get(ImportBatchModel, batch)
        assert saved is not None and saved.status == "VALID"


def test_publication_rejects_unvalidated_batch(database: Engine, tmp_path: Path) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    upload, validation, publisher = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("States")
    batch, _ = upload.create(
        scenario, "received", _publication_package(storage, _publication_rows())
    )
    with pytest.raises(PublicationError, match="BATCH_NOT_VALID"):
        publisher.publish(scenario, batch)
    validation.request(scenario, batch, _validation_context())
    with pytest.raises(PublicationError, match="BATCH_NOT_VALID"):
        publisher.publish(scenario, batch)


@pytest.mark.parametrize("workbook", [False, True])
def test_validation_job_replays_exact_originals_and_is_idempotent(
    database: Engine, tmp_path: Path, workbook: bool
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("Validation worker")
    batch, _ = UploadService(sessions, storage).create(
        scenario, f"valid-{workbook}", _valid_package(storage, workbook=workbook)
    )
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=3
    )
    first = worker.request(scenario, batch, _validation_context())
    assert first["status"] == "VALIDATING"
    assert (
        worker.request(scenario, batch, _validation_context())["context_sha256"]
        == first["context_sha256"]
    )
    with pytest.raises(ValidationJobError, match="VALIDATION_CONTEXT_CONFLICT"):
        worker.request(scenario, batch, _validation_context(currency="USD"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    claim = next(claim for claim in claims if claim is not None)
    assert claim is not None and claim[0] == batch
    contents, context, package_sha = worker._read(batch)
    assert package_sha == first["package_sha256"]
    report = validate_package(contents, context=context)
    assert report.valid
    assert worker.finish(batch, claim[1], report)
    assert not worker.finish(batch, claim[1], report)
    assert worker.get(scenario, batch)["status"] == "VALID"
    replayed, replay_context = worker.replay_verified_for_publication(scenario, batch)
    assert len(replayed) == (1 if workbook else 5)
    assert replay_context.sha256 == context.sha256
    with sessions() as session:
        saved = session.get(ImportValidationReportModel, batch)
        assert saved is not None
        assert saved.package_sha256 == package_sha
        assert saved.context_sha256 == context.sha256
        assert session.get(ImportValidationContextModel, batch) is not None
        assert session.get(ImportValidationJobModel, batch) is not None
        events = list(
            session.scalars(
                select(ImportBatchEventModel).where(ImportBatchEventModel.batch_id == batch)
            )
        )
        assert [event.to_status for event in events] == ["RECEIVED", "VALIDATING", "VALID"]
        assert (
            session.scalar(
                select(ValidationIssueModel).where(ValidationIssueModel.batch_id == batch)
            )
            is None
        )
        original = session.scalar(
            select(ImportFileModel).where(ImportFileModel.batch_id == batch).limit(1)
        )
        assert original is not None
        key = original.storage_key
    (storage.objects / key).write_bytes(b"changed after validation")
    with pytest.raises(ValidationJobError, match="VALIDATED_SOURCE_UNAVAILABLE"):
        worker.replay_verified_for_publication(scenario, batch)


def test_expired_lease_fences_old_worker_and_expiry_waits_for_live_lease(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("Lease and expiry")
    batch, _ = UploadService(sessions, storage).create(scenario, "lease", _valid_package(storage))
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=3
    )
    worker.request(scenario, batch, _validation_context())
    original = worker.claim()
    assert original is not None
    assert worker.heartbeat(batch, original[1])
    assert not worker.heartbeat(batch, uuid4())
    maintenance = ImportStorageMaintenance(
        sessions, storage, retention_days=30, orphan_grace_seconds=3600
    )
    maintenance._retention = timedelta(seconds=0)
    assert maintenance._expire_due() == 0
    with sessions.begin() as session:
        job = session.get(ImportValidationJobModel, batch)
        assert job is not None
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert not worker.heartbeat(batch, original[1])
    recovered = worker.claim()
    assert recovered is not None and recovered[1] != original[1]
    report = validate_package(worker._read(batch)[0], context=_validation_context())
    assert not worker.finish(batch, original[1], report)
    assert worker.finish(batch, recovered[1], report)
    assert maintenance._expire_due() == 1
    assert worker.get(scenario, batch)["status"] == "EXPIRED"
    with sessions() as session:
        events = list(
            session.scalars(
                select(ImportBatchEventModel).where(ImportBatchEventModel.batch_id == batch)
            )
        )
        assert [event.to_status for event in events] == ["RECEIVED", "VALIDATING", "VALID"]


def test_validation_failures_retry_without_partial_issues(database: Engine, tmp_path: Path) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("Validation failure")
    batch, _ = UploadService(sessions, storage).create(scenario, "infra", _valid_package(storage))
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=2
    )
    worker.request(scenario, batch, _validation_context())
    first = worker.claim()
    assert first is not None and worker.fail(*first)
    with sessions() as session:
        assert session.get(ImportValidationReportModel, batch) is None
        assert (
            session.scalar(
                select(ValidationIssueModel).where(ValidationIssueModel.batch_id == batch)
            )
            is None
        )
    second = worker.claim()
    assert second is not None and worker.fail(*second)
    assert worker.get(scenario, batch)["status"] == "FAILED"
    assert worker.claim() is None


def test_invalid_report_retry_has_one_set_of_immutable_issues(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("Invalid import")
    batch, _ = UploadService(sessions, storage).create(
        scenario, "invalid", _stored_package(storage)
    )
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=3
    )
    worker.request(scenario, batch, _validation_context())
    first = worker.claim()
    assert first is not None
    with sessions.begin() as session:
        job = session.get(ImportValidationJobModel, batch)
        assert job is not None
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    second = worker.claim()
    assert second is not None
    report = validate_package(worker._read(batch)[0], context=_validation_context())
    assert not report.valid
    assert not worker.finish(batch, first[1], report)
    assert worker.finish(batch, second[1], report)
    assert not worker.finish(batch, second[1], report)
    assert worker.get(scenario, batch)["status"] == "INVALID"
    page = worker.issues(scenario, batch, limit=2)
    assert len(page["items"]) == 2
    assert page["next_after"] is not None
    remainder = worker.issues(scenario, batch, after=page["next_after"], limit=200)
    assert len(page["items"]) + len(remainder["items"]) == len(report.issues)
    assert all("\\" not in (item["source"] or "") for item in page["items"])
    with sessions() as session:
        events = list(
            session.scalars(
                select(ImportBatchEventModel).where(ImportBatchEventModel.batch_id == batch)
            )
        )
        assert [event.to_status for event in events] == ["RECEIVED", "VALIDATING", "INVALID"]


def test_worker_retries_missing_private_original_then_fails(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("Missing original")
    batch, _ = UploadService(sessions, storage).create(scenario, "missing", _valid_package(storage))
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=2
    )
    worker.request(scenario, batch, _validation_context())
    with sessions() as session:
        file = session.scalar(
            select(ImportFileModel).where(ImportFileModel.batch_id == batch).limit(1)
        )
        assert file is not None
        storage.delete(file.storage_key)
    assert worker.process_once()
    assert worker.get(scenario, batch)["status"] == "VALIDATING"
    assert worker.process_once()
    assert worker.get(scenario, batch)["status"] == "FAILED"


def test_validation_finish_is_atomic_and_expiry_fences_result(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("Atomic validation")
    uploader = UploadService(sessions, storage)
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=3
    )
    batch, _ = uploader.create(scenario, "rollback", _valid_package(storage))
    worker.request(scenario, batch, _validation_context())
    claim = worker.claim()
    assert claim is not None
    broken = ValidationReport(
        issues=[Issue("INSTANT_INVALID", "BROKEN", None, None, None, None, "Invalid")]
    )
    with pytest.raises(DBAPIError):
        worker.finish(batch, claim[1], broken)
    with sessions() as session:
        assert session.get(ImportValidationReportModel, batch) is None
        assert (
            session.scalar(
                select(ValidationIssueModel).where(ValidationIssueModel.batch_id == batch)
            )
            is None
        )
        persisted = session.get(ImportBatchModel, batch)
        assert persisted is not None and persisted.status == "VALIDATING"
    assert worker.finish(
        batch, claim[1], validate_package(worker._read(batch)[0], context=_validation_context())
    )

    expired_batch, _ = uploader.create(scenario, "expired-midwork", _valid_package(storage))
    worker.request(scenario, expired_batch, _validation_context())
    held = worker.claim()
    assert held is not None
    worker.retention = timedelta(seconds=0)
    assert not worker.finish(
        expired_batch,
        held[1],
        validate_package(worker._read(expired_batch)[0], context=_validation_context()),
    )
    with sessions() as session:
        assert session.get(ImportValidationReportModel, expired_batch) is None
        persisted = session.get(ImportBatchModel, expired_batch)
        assert persisted is not None and persisted.status == "VALIDATING"
    assert worker.get(scenario, expired_batch)["status"] == "EXPIRED"


def test_operational_area_is_checked_by_postgis_on_request(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario = ScenarioRepository(sessions).create("PostGIS area")
    batch, _ = UploadService(sessions, storage).create(scenario, "area", _valid_package(storage))
    worker = ValidationJobService(
        sessions, storage, ImportLimits(), retention_days=30, lease_seconds=60, max_attempts=3
    )
    area: dict[str, object] = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-71.0, -34.0], [-70.0, -34.0], [-70.0, -33.0], [-71.0, -33.0], [-71.0, -34.0]]]
        ],
    }
    plain = _validation_context()
    context = ValidationContext(
        planning_date=plain.planning_date,
        horizon_start_at=plain.horizon_start_at,
        horizon_end_at=plain.horizon_end_at,
        timezone_iana=plain.timezone_iana,
        currency=plain.currency,
        operational_area=area,
    )
    assert worker.request(scenario, batch, context)["status"] == "VALIDATING"


def test_concurrent_same_key_upload_has_one_batch_and_no_extra_objects(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario_id = ScenarioRepository(sessions).create("Concurrent upload")
    service = UploadService(sessions, storage)
    packages = (_stored_package(storage), _stored_package(storage))
    barrier = Barrier(2)
    lock = Lock()
    calls = 0
    original_existing = service._existing

    def both_observe_missing(scenario: UUID, key: str) -> ImportBatchModel | None:
        nonlocal calls
        result = original_existing(scenario, key)
        with lock:
            calls += 1
            first_lookup = calls <= 2
        if first_lookup:
            assert result is None
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(service, "_existing", both_observe_missing)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(service.create, scenario_id, "concurrent-key", package)
            for package in packages
        ]
        results = [future.result(timeout=15) for future in futures]
    assert len({batch_id for batch_id, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]
    batch_id = results[0][0]
    with sessions() as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(ImportBatchModel).where(
                            ImportBatchModel.scenario_id == scenario_id,
                            ImportBatchModel.client_key == "concurrent-key",
                        )
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(ImportFileModel).where(ImportFileModel.batch_id == batch_id)
                    )
                )
            )
            == 5
        )
        events = list(
            session.scalars(
                select(ImportBatchEventModel).where(ImportBatchEventModel.batch_id == batch_id)
            )
        )
    assert [(event.sequence, event.to_status) for event in events] == [(1, "RECEIVED")]
    assert len(list(storage.object_keys())) == 5
    assert list(storage.temporary_keys()) == []
    with pytest.raises(UploadError) as caught:
        service.create(scenario_id, "concurrent-key", _stored_package(storage, suffix=b"other"))
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(list(storage.object_keys())) == 5


def test_sql_failure_is_recovered_even_if_immediate_cleanup_fails(
    database: Engine, tmp_path: Path
) -> None:
    class FailingDeleteStorage(LocalObjectStorage):
        fail_delete = True

        def delete(self, key: str) -> None:
            if self.fail_delete:
                raise OSError("simulated cleanup interruption")
            super().delete(key)

    sessions = create_session_factory(database)
    storage = FailingDeleteStorage(tmp_path)
    package = _stored_package(storage)
    service = UploadService(sessions, storage)
    with pytest.raises(DBAPIError):
        service.create(uuid4(), "orphaned-upload", package)
    with sessions() as session:
        assert (
            session.scalar(
                select(ImportBatchModel).where(ImportBatchModel.client_key == "orphaned-upload")
            )
            is None
        )
        assert (
            session.scalar(
                select(ImportFileModel).where(
                    ImportFileModel.storage_key == package.files[0].object.key
                )
            )
            is None
        )
    assert len(list(storage.object_keys())) == 5
    storage.fail_delete = False
    past = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    for key, _ in storage.object_keys():
        os.utime(storage.objects / key, (past, past))
    maintenance = ImportStorageMaintenance(
        sessions, storage, retention_days=30, orphan_grace_seconds=3600
    )
    assert maintenance.run_once() == (0, 0, 5)
    assert list(storage.object_keys()) == []


def test_private_upload_transaction_retry_and_gateway(database: Engine, tmp_path: Path) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario_id = ScenarioRepository(sessions).create("Import test")
    service = UploadService(sessions, storage)
    first = _stored_package(storage)
    batch_id, created = service.create(scenario_id, "same-key", first)
    assert created
    metadata = service.get(scenario_id, batch_id)
    assert metadata is not None
    assert metadata["status"] == "RECEIVED"
    assert len(metadata["files"]) == 5  # type: ignore[arg-type]
    with sessions() as session:
        files = list(
            session.scalars(select(ImportFileModel).where(ImportFileModel.batch_id == batch_id))
        )
        events = list(
            session.scalars(
                select(ImportBatchEventModel).where(ImportBatchEventModel.batch_id == batch_id)
            )
        )
    assert [(event.sequence, event.to_status) for event in events] == [(1, "RECEIVED")]
    for file in files:
        with storage.open(file.storage_key) as stream:
            assert stream.read() == file.dataset.encode("ascii")
    duplicate = _stored_package(storage)
    assert service.create(scenario_id, "same-key", duplicate) == (batch_id, False)
    assert len(list(storage.object_keys())) == 5
    conflicting = _stored_package(storage, suffix=b"changed")
    with pytest.raises(UploadError) as caught:
        service.create(scenario_id, "same-key", conflicting)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(list(storage.object_keys())) == 5
    failed = _stored_package(storage)
    with pytest.raises(DBAPIError):
        service.create(uuid4(), "missing-scenario", failed)
    assert len(list(storage.object_keys())) == 5


def test_retention_audit_and_orphan_recovery(database: Engine, tmp_path: Path) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path)
    scenario_id = ScenarioRepository(sessions).create("Expired import")
    batch_id = uuid4()
    file_id = uuid4()
    old = datetime.now(UTC) - timedelta(days=31)
    writer = storage.begin()
    writer.write(b"original")
    stored = writer.finish()
    with sessions.begin() as session:
        session.add(
            ImportBatchModel(
                id=batch_id,
                scenario_id=scenario_id,
                client_key="expired-key",
                package_sha256=SHA,
                parser_version="2.1",
                status="RECEIVED",
                version=1,
                created_at=old,
                transitioned_at=old,
            )
        )
        session.flush()
        session.add(
            ImportBatchEventModel(
                id=uuid4(),
                batch_id=batch_id,
                sequence=1,
                from_status=None,
                to_status="RECEIVED",
                occurred_at=old,
            )
        )
        session.add(
            ImportFileModel(
                id=file_id,
                batch_id=batch_id,
                dataset="workbook",
                display_name="book.xlsx",
                storage_key=stored.key,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                created_at=old,
            )
        )
    service = UploadService(sessions, storage)
    overdue = service.get(scenario_id, batch_id)
    assert overdue is not None
    assert overdue["status"] == "EXPIRED"
    assert overdue["expired_at"] is None
    assert overdue["expires_at"] is not None
    with pytest.raises(UploadError) as overdue_retry:
        service.create(scenario_id, "expired-key", _stored_package(storage))
    assert overdue_retry.value.code == "BATCH_EXPIRED"

    published_batch_id = uuid4()
    published_file_id = uuid4()
    published_old = datetime(2026, 8, 1, 12, tzinfo=UTC)
    published_writer = storage.begin()
    published_writer.write(b"published-original")
    published_object = published_writer.finish()
    with sessions.begin() as session:
        published_batch = ImportBatchModel(
            id=published_batch_id,
            scenario_id=scenario_id,
            client_key="published-key",
            package_sha256=SHA,
            parser_version="2.1",
            status="RECEIVED",
            version=1,
            created_at=published_old,
            transitioned_at=published_old,
        )
        session.add(published_batch)
        session.flush()
        session.add(
            ImportBatchEventModel(
                id=uuid4(),
                batch_id=published_batch_id,
                sequence=1,
                from_status=None,
                to_status="RECEIVED",
                occurred_at=published_old,
            )
        )
        session.add(
            ImportFileModel(
                id=published_file_id,
                batch_id=published_batch_id,
                dataset="workbook",
                display_name="published.xlsx",
                storage_key=published_object.key,
                sha256=published_object.sha256,
                size_bytes=published_object.size_bytes,
                created_at=published_old,
            )
        )
        session.flush()
        for sequence, before, after in (
            (2, "RECEIVED", "VALIDATING"),
            (3, "VALIDATING", "VALID"),
            (4, "VALID", "PUBLISHED"),
        ):
            changed_at = published_old + timedelta(seconds=sequence)
            published_batch.status = after
            published_batch.version = sequence
            published_batch.transitioned_at = changed_at
            session.add(
                ImportBatchEventModel(
                    id=uuid4(),
                    batch_id=published_batch_id,
                    sequence=sequence,
                    from_status=before,
                    to_status=after,
                    occurred_at=changed_at,
                )
            )
            session.flush()
        session.add(
            ScenarioRevisionModel(
                id=uuid4(),
                scenario_id=scenario_id,
                import_batch_id=published_batch_id,
                revision_no=1,
                planning_date=date(2026, 8, 1),
                timezone_iana="America/Santiago",
                currency="CLP",
                horizon_start_at=published_old,
                horizon_end_at=published_old + timedelta(hours=8),
                operational_area=None,
                content_sha256=SHA,
                contract_version="1",
                published_at=published_old + timedelta(seconds=4),
            )
        )
    orphan = storage.begin()
    orphan.write(b"orphan")
    orphan_key = orphan.finish().key
    temporary_key = uuid4().hex
    (storage.temporary / temporary_key).write_bytes(b"interrupted")
    past = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(storage.objects / orphan_key, (past, past))
    os.utime(storage.temporary / temporary_key, (past, past))
    maintenance = ImportStorageMaintenance(
        sessions, storage, retention_days=30, orphan_grace_seconds=3600
    )
    assert maintenance.run_once() == (1, 1, 2)
    assert maintenance.run_once() == (0, 0, 0)
    with sessions() as session:
        assert session.get(ImportBatchExpirationModel, batch_id) is not None
        assert session.get(ImportFileDeletionModel, file_id) is not None
        assert session.get(ImportFileModel, file_id) is not None
        assert session.get(ImportBatchModel, batch_id).status == "RECEIVED"  # type: ignore[union-attr]
        assert session.get(ImportBatchExpirationModel, published_batch_id) is None
        assert session.get(ImportFileDeletionModel, published_file_id) is None
    published = service.get(scenario_id, published_batch_id)
    assert published is not None and published["status"] == "PUBLISHED"
    with storage.open(published_object.key) as stream:
        assert stream.read() == b"published-original"
    with pytest.raises(FileNotFoundError), storage.open(stored.key):
        pass
    expired = service.get(scenario_id, batch_id)
    assert expired is not None and expired["status"] == "EXPIRED"
    assert expired["expired_at"] is not None
    with pytest.raises(UploadError) as caught:
        UploadService(sessions, storage).create(
            scenario_id, "expired-key", _stored_package(storage)
        )
    assert caught.value.code == "BATCH_EXPIRED"


class FixedAllocationTravel:
    def duration_seconds(self, origin: object, destination: object) -> int:
        return 120


class FailedAllocationTravel:
    def duration_seconds(self, origin: object, destination: object) -> int:
        raise RoutingDependencyError("synthetic OSRM outage")


def _published_allocation_fixture(
    database: Engine, tmp_path: Path, rows: dict[str, list[dict[str, str]]] | None = None
) -> tuple[
    UUID, LocalObjectStorage, tuple[UploadService, ValidationJobService, ImportPublicationService]
]:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Operational allocation")
    package_rows = rows if rows is not None else _publication_rows()
    batch = _validated_publication_batch(scenario, "first", package_rows, services, storage)
    services[2].publish(scenario, batch)
    return scenario, storage, services


def test_revision_run_reaches_real_vroom_and_accepts_reservations(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
    )
    queued, created = service.submit(scenario, 1, "real-solver")
    assert created and queued["status"] == "QUEUED"
    assert service.process_once()
    ready = service.get(UUID(queued["run_id"]))
    assert ready["status"] == "READY", ready["error"]
    assert ready["result"]["routes"]
    assert ready["decisions"][0]["reservation_status"] == "HELD"
    stored_before = DatabaseRunRepository(sessions).get(UUID(queued["run_id"]))
    costs = OperatingCostQuery(sessions).get(UUID(queued["run_id"]))
    assert costs["provenance"]["rate_source"] == "immutable_scenario_revision"
    assert costs["provenance"]["scenario_revision_id"] == queued["scenario_revision_id"]
    assert len(costs["provenance"]["route_facts_sha256"]) == 64
    assert Decimal(costs["total"]) > 0
    assert costs["solver_objective"]["units"] == ready["result"]["summary"]["objective_cost_units"]
    assert costs == OperatingCostQuery(sessions).get(UUID(queued["run_id"]))
    assert DatabaseRunRepository(sessions).get(UUID(queued["run_id"])) == stored_before
    accepted = service.accept(UUID(queued["run_id"]))
    assert accepted["status"] == "ACCEPTED"
    assert accepted["decisions"][0]["reservation_status"] == "CONFIRMED"
    assert OperatingCostQuery(sessions).get(UUID(queued["run_id"])) == costs
    query = PlanMetricsQuery(sessions)
    before_metrics = query.get(UUID(queued["run_id"]))
    assert before_metrics["current_status"] == "ACCEPTED"
    metrics = before_metrics["plan"]["metrics"]
    assert metrics["valid_input_orders"]["value"] == 1
    assert metrics["allocated_orders"]["value"] == 1
    assert metrics["routed_orders"]["value"] == 1
    assert metrics["coverage"]["value"] == "1"
    assert metrics["window_compliance"]["value"] == "1"
    assert before_metrics["plan"]["operating_cost"] == costs
    assert before_metrics["processing"]["active_all_attempts"]["value"] is not None
    assert before_metrics["processing"]["attempts"][0]["outcome"] == "READY"
    assert query.get(UUID(queued["run_id"])) == before_metrics


def test_run_result_insert_failure_rolls_back_and_releases_holds(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
    )

    def fail_geometry(_: object) -> None:
        raise RuntimeError("injected route persistence failure")

    monkeypatch.setattr(service, "_geometry", fail_geometry)
    queued, _ = service.submit(scenario, 1, "persistence-fault")
    assert service.process_once()
    failed = service.get(UUID(queued["run_id"]))
    assert failed["status"] == "FAILED" and failed["result"] is None
    assert failed["decisions"][0]["reservation_status"] == "RELEASED"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizedRouteModel)) == 0
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None and position.routeops_reserved_quantity == 0


def test_isolated_demo_catalog_publishes_all_four_fixtures(
    database: Engine, tmp_path: Path
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "demo-originals")
    upload, validation, publication = _publication_services(database, storage, tmp_path / "spool")
    catalog = DemoCatalogService(
        ScenarioRepository(sessions), storage, upload, validation, publication
    )
    prepared = {
        name: catalog.prepare(name)
        for name in (
            "exclusive_stock",
            "choice_between_centers",
            "shared_stock_restricted",
            "fleet_restrictions",
        )
    }
    assert len({item["scenario_id"] for item in prepared.values()}) == 4
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
    )
    results: dict[str, dict[str, object]] = {}
    for name, item in prepared.items():
        queued, _ = service.submit(UUID(item["scenario_id"]), 1, f"run-{name}")
        assert service.process_once()
        results[name] = service.get(UUID(queued["run_id"]))
        assert results[name]["status"] == "READY", results[name]["error"]
    assert [row["center_id"] for row in results["exclusive_stock"]["decisions"]] == [
        "CD-A",
        "CD-B",
        None,
    ]
    assert results["exclusive_stock"]["decisions"][-1]["reason_code"] == "STOCK_NO_FULL_COVERAGE"
    assert results["choice_between_centers"]["decisions"][0]["center_id"] in ("CD-A", "CD-B")
    assert [row["center_id"] for row in results["shared_stock_restricted"]["decisions"]] == [
        "CD-B",
        "CD-A",
    ]
    assert all(
        row["reason_code"] == "NO_COMPATIBLE_VEHICLE"
        for row in results["fleet_restrictions"]["decisions"]
    )


class FixedMatrix:
    def duration_matrix(
        self, origins: tuple[object, ...], destinations: tuple[object, ...]
    ) -> tuple[tuple[int, ...], ...]:
        return tuple(tuple(100 for _ in destinations) for _ in origins)

    def checked_duration_matrix(
        self, origins: tuple[object, ...], destinations: tuple[object, ...],
        max_snap_distance_m: float,
    ) -> object:
        from routeops.infrastructure.routing.osrm import AllocationMatrix

        return AllocationMatrix(self.duration_matrix(origins, destinations), [])  # type: ignore[arg-type]


def test_uncovered_point_fails_before_reserving_stock(database: Engine, tmp_path: Path) -> None:
    from routeops.infrastructure.routing.errors import RoutingCoverageError

    scenario, _, _ = _published_allocation_fixture(database, tmp_path)

    class UncoveredMatrix(FixedMatrix):
        def checked_duration_matrix(
            self, origins: tuple[object, ...], destinations: tuple[object, ...],
            max_snap_distance_m: float,
        ) -> object:
            raise RoutingCoverageError("ROUTING_SNAP_TOO_FAR", [
                {"role": "center", "index": 0, "original": [-70.7, -33.44],
                 "snapped": [-70.68, -33.435], "distance_m": 1902.0},
            ])

    sessions = create_session_factory(database)
    service = RevisionRunService(
        sessions, OperationalAllocationService(sessions, FixedAllocationTravel()),
        UncoveredMatrix(), AllUnassignedSolver(), WorkloadLimits(),  # type: ignore[arg-type]
    )
    queued, _ = service.submit(scenario, 1, "uncovered")
    assert service.process_once()
    result = service.get(UUID(queued["run_id"]))
    assert result["status"] == "FAILED"
    assert result["error"] == "ROUTING_SNAP_TOO_FAR"
    assert result["kpis"]["network_coverage"]["points"][0]["business_id"] == "CD-001"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 0
        assert session.scalar(select(func.count()).select_from(AllocationReservationLineModel)) == 0


class AllUnassignedSolver:
    def solve(self, problem: OptimizationProblem) -> OptimizationResult:
        return OptimizationResult(
            contract_version=problem.contract_version,
            problem_id=problem.problem_id,
            status=ResultStatus.PARTIAL,
            solver=SolverMetadata("fake", "1", "1", "fake", "1", 1),
            summary=OptimizationSummary(
                0,
                0,
                len(problem.tasks),
                0,
                0,
                0,
                0,
                0,
                0,
                10_000,
                problem.vehicles[0].costs.currency,
            ),
            routes=(),
            unassigned=tuple(
                UnassignedTask(
                    task.task_id,
                    task.order_id,
                    "OPTIMIZATION",
                    (
                        UnassignedReason(
                            "SOLVER_NO_FEASIBLE_ROUTE", Certainty.INFERRED, "No route", {}
                        ),
                    ),
                )
                for task in problem.tasks
            ),
        )


class BrokenSolver:
    def solve(self, problem: OptimizationProblem) -> OptimizationResult:
        raise SolverDependencyError("synthetic outage")


class InconsistentSolver:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def solve(self, problem: OptimizationProblem) -> OptimizationResult:
        valid = AllUnassignedSolver().solve(problem)
        if self.kind == "duplicate":
            return replace(valid, unassigned=valid.unassigned * 2)
        if self.kind == "unknown":
            return replace(valid, unassigned=(replace(valid.unassigned[0], task_id=uuid4()),))
        if self.kind == "summary":
            return replace(valid, summary=replace(valid.summary, unassigned_task_count=0))
        return replace(valid, contract_version="wrong")


def _revision_run_service(database: Engine, solver: object | None = None) -> RevisionRunService:
    sessions = create_session_factory(database)
    return RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, FixedAllocationTravel()),
        FixedMatrix(),  # type: ignore[arg-type]
        solver if solver is not None else AllUnassignedSolver(),
        WorkloadLimits(),
    )


def test_revision_run_idempotency_and_workload_limit(database: Engine, tmp_path: Path) -> None:
    scenario, storage, services = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    first, created = service.submit(scenario, 1, "same-key")
    repeated, again = service.submit(scenario, 1, "same-key")
    assert created and not again and first["run_id"] == repeated["run_id"]
    with pytest.raises(PlanningRunError, match="RUN_IDEMPOTENCY_CONFLICT"):
        service.submit(scenario, 1, "same-key", policy_version="greedy-v1")
    matrix_limited = RevisionRunService(
        service.sessions,
        service.allocation,
        service.osrm,
        service.solver,
        WorkloadLimits(max_solver_matrix_cells=8),
    )
    with pytest.raises(RunInputError, match="SOLVER_WORKLOAD_LIMIT"):
        matrix_limited.submit(scenario, 1, "solver-matrix-too-large")
    limited = RevisionRunService(
        service.sessions,
        service.allocation,
        service.osrm,
        service.solver,
        WorkloadLimits(max_orders=1, max_lines=1, max_vehicles=1, max_matrix_cells=1),
    )
    rows = _publication_rows()
    rows["order_lines"].append({**rows["order_lines"][0], "sku": "SKU-2"})
    rows["inventory"].append({**rows["inventory"][0], "sku": "SKU-2"})
    new_batch = _validated_publication_batch(scenario, "larger", rows, services, storage)
    # A valid larger publication is not interpreted as a solver capacity guarantee.
    services[2].publish(scenario, new_batch)
    inventory_limited = RevisionRunService(
        service.sessions,
        service.allocation,
        service.osrm,
        service.solver,
        WorkloadLimits(max_inventory_positions=1),
    )
    with pytest.raises(RunInputError, match="SOLVER_WORKLOAD_LIMIT"):
        inventory_limited.submit(scenario, 2, "inventory-too-large")
    prior, _ = service.submit(scenario, 2, "already-created")
    same, created = limited.submit(scenario, 2, "already-created")
    assert not created and same["run_id"] == prior["run_id"]
    with pytest.raises(RunInputError, match="SOLVER_WORKLOAD_LIMIT"):
        limited.submit(scenario, 2, "too-large")
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 0


def test_solver_unassigned_releases_only_routeops_stock(database: Engine, tmp_path: Path) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    queued, _ = service.submit(scenario, 1, "unassigned")
    assert service.process_once()
    ready = service.get(UUID(queued["run_id"]))
    assert ready["status"] == "READY"
    assert ready["result"]["unassigned"][0]["stage"] == "OPTIMIZATION"
    assert ready["decisions"][0]["reservation_status"] == "RELEASED"
    with service.sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None
        assert position.externally_reserved_quantity == 2
        assert position.routeops_reserved_quantity == 0
        assert position.available_quantity == 7
    assert service.accept(UUID(queued["run_id"]))["status"] == "ACCEPTED"


def test_solver_unassigned_releases_all_order_lines(database: Engine, tmp_path: Path) -> None:
    rows = _publication_rows()
    rows["order_lines"].append({**rows["order_lines"][0], "sku": "SKU-2", "quantity": "3"})
    rows["inventory"].append({**rows["inventory"][0], "sku": "SKU-2", "on_hand_quantity": "8"})
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    service = _revision_run_service(database)
    queued, _ = service.submit(scenario, 1, "multiline-unassigned")
    assert service.process_once()
    ready = service.get(UUID(queued["run_id"]))
    assert ready["status"] == "READY"
    assert ready["decisions"][0]["reservation_status"] == "RELEASED"
    with service.sessions() as session:
        positions = list(session.scalars(select(OperationalInventoryPositionModel)))
        assert {item.sku for item in positions} == {"SKU-1", "SKU-2"}
        assert all(item.routeops_reserved_quantity == 0 for item in positions)
        assert all(item.externally_reserved_quantity == 2 for item in positions)


def test_solver_failure_releases_holds_and_cancel_is_idempotent(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database, BrokenSolver())
    failed, _ = service.submit(scenario, 1, "fails")
    assert service.process_once()
    result = service.get(UUID(failed["run_id"]))
    assert result["status"] == "FAILED"
    assert result["error"] == "SOLVER_DEPENDENCY_FAILED"
    assert result["decisions"][0]["reservation_status"] == "RELEASED"
    with pytest.raises(PlanningRunError, match="RUN_TRANSITION_CONFLICT"):
        service.cancel(UUID(failed["run_id"]))
    queued, _ = service.submit(scenario, 1, "cancel-me")
    canceled = service.cancel(UUID(queued["run_id"]))
    assert canceled["status"] == "CANCELED"
    assert service.cancel(UUID(queued["run_id"]))["status"] == "CANCELED"


@pytest.mark.parametrize("kind", ["duplicate", "unknown", "contract", "summary"])
def test_inconsistent_solver_result_is_rejected_and_holds_released(
    database: Engine, tmp_path: Path, kind: str
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database, InconsistentSolver(kind))
    queued, _ = service.submit(scenario, 1, f"invalid-{kind}")
    assert service.process_once()
    failed = service.get(UUID(queued["run_id"]))
    assert failed["status"] == "FAILED"
    assert failed["error"] == "SOLVER_RESPONSE_INVALID"
    assert failed["result"] is None
    assert failed["decisions"][0]["reservation_status"] == "RELEASED"
    with service.sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None and position.routeops_reserved_quantity == 0


def test_planning_lease_recovery_fences_stale_worker(database: Engine, tmp_path: Path) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    service.submit(scenario, 1, "recover")
    first = service.claim()
    assert first is not None
    with service.sessions.begin() as session:
        job = session.get(RevisionRunJobModel, first[0])
        assert job is not None
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    second = service.claim()
    assert second is not None and second[0] == first[0] and second[1] != first[1]
    assert not service.heartbeat(*first)
    service._fail(*first, "STALE")
    assert service.get(first[0])["status"] == "RUNNING"
    service._process(*second)
    assert service.get(first[0])["status"] == "READY"


def test_concurrent_run_submission_uses_one_idempotency_key(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    barrier = Barrier(2)

    def submit() -> tuple[dict[str, object], bool]:
        barrier.wait(timeout=10)
        return service.submit(scenario, 1, "simultaneous")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(submit) for _ in range(2)]
        first, second = [future.result(timeout=30) for future in results]
    assert first[0]["run_id"] == second[0]["run_id"]
    assert sorted((first[1], second[1])) == [False, True]
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RevisionRunJobModel)) == 1


def test_planning_api_preserves_key_history_and_transitions(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "revision_planning", service)
    path = f"/api/v1/scenarios/{scenario}/revisions/1/runs"
    with TestClient(api.app) as client:
        assert client.post(path, json={}).status_code == 422
        first = client.post(path, headers={"Idempotency-Key": "api-key"}, json={})
        assert first.status_code == 202
        run_id = first.json()["run_id"]
        repeated = client.post(path, headers={"Idempotency-Key": "api-key"}, json={})
        assert repeated.status_code == 200 and repeated.json()["run_id"] == run_id
        conflict = client.post(
            path,
            headers={"Idempotency-Key": "api-key"},
            json={"allocation_policy": "greedy-v1"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "RUN_IDEMPOTENCY_CONFLICT"
        lookup = client.get(f"{path}/lookup", params={"key": "api-key"})
        assert lookup.status_code == 200 and lookup.json()["run_id"] == run_id
        history = client.get(f"/api/v1/scenarios/{scenario}/revision-runs")
        assert history.status_code == 200 and history.json()["items"][0]["run_id"] == run_id
        assert service.process_once()
        detail = client.get(f"/api/v1/revision-runs/{run_id}")
        assert detail.status_code == 200 and detail.json()["status"] == "READY"
        accepted = client.post(f"/api/v1/revision-runs/{run_id}/accept")
        assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED"
        assert client.post(f"/api/v1/revision-runs/{run_id}/accept").status_code == 200
        assert client.post(f"/api/v1/revision-runs/{run_id}/cancel").status_code == 409


def test_two_revision_runs_compete_for_one_stock_position(database: Engine, tmp_path: Path) -> None:
    rows = _publication_rows()
    rows["order_lines"][0]["quantity"] = "7"
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    sessions = create_session_factory(database)
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
    )
    first, _ = service.submit(scenario, 1, "compete-1")
    second, _ = service.submit(scenario, 1, "compete-2")
    barrier = Barrier(2)

    def execute() -> bool:
        barrier.wait(timeout=10)
        return service.process_once()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        assert all(future.result(timeout=45) for future in futures)
    outcomes = [service.get(UUID(item["run_id"])) for item in (first, second)]
    assert [item["status"] for item in outcomes] == ["READY", "READY"]
    assert sorted(len(item["result"]["routes"]) for item in outcomes) == [0, 1]
    loser = next(item for item in outcomes if not item["result"]["routes"])
    assert loser["decisions"][0]["reason_code"] == "STOCK_NO_FULL_COVERAGE"
    with sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None
        assert position.routeops_reserved_quantity == 7
        assert position.externally_reserved_quantity == 2


def test_external_calls_do_not_hold_scenario_or_inventory_locks(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)

    class ProbeMatrix(FixedMatrix):
        def duration_matrix(
            self, origins: tuple[object, ...], destinations: tuple[object, ...]
        ) -> tuple[tuple[int, ...], ...]:
            with sessions.begin() as probe:
                assert (
                    probe.scalar(
                        select(ScenarioModel)
                        .where(ScenarioModel.id == scenario)
                        .with_for_update(nowait=True)
                    )
                    is not None
                )
            return super().duration_matrix(origins, destinations)

    class ProbeSolver(AllUnassignedSolver):
        def solve(self, problem: OptimizationProblem) -> OptimizationResult:
            with sessions.begin() as probe:
                assert (
                    probe.scalar(
                        select(OperationalInventoryPositionModel).with_for_update(nowait=True)
                    )
                    is not None
                )
            return super().solve(problem)

    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, FixedAllocationTravel()),
        ProbeMatrix(),  # type: ignore[arg-type]
        ProbeSolver(),
        WorkloadLimits(),
    )
    queued, _ = service.submit(scenario, 1, "lock-probe")
    assert service.process_once()
    assert service.get(UUID(queued["run_id"]))["status"] == "READY"


def test_accept_cancel_race_is_serialized_and_keeps_stock_consistent(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
    )
    queued, _ = service.submit(scenario, 1, "race")
    assert service.process_once()
    run_id = UUID(queued["run_id"])
    barrier = Barrier(2)

    def change(action: str) -> str:
        barrier.wait(timeout=10)
        try:
            result = service.accept(run_id) if action == "accept" else service.cancel(run_id)
            return str(result["status"])
        except PlanningRunError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(change, action) for action in ("accept", "cancel")]
        outcomes = [future.result(timeout=30) for future in futures]
    assert "RUN_TRANSITION_CONFLICT" in outcomes
    final = service.get(run_id)
    assert final["status"] in ("ACCEPTED", "CANCELED")
    expected = "CONFIRMED" if final["status"] == "ACCEPTED" else "RELEASED"
    assert final["decisions"][0]["reservation_status"] == expected
    with sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None
        assert position.routeops_reserved_quantity == (2 if expected == "CONFIRMED" else 0)


def test_cancel_recovers_allocation_committed_before_run_link(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    queued, _ = service.submit(scenario, 1, "orphan")
    claim = service.claim()
    assert claim is not None
    attempt = service.allocation.allocate(
        scenario, 1, queued["run_id"], travel_times=FixedAllocationTravel()
    )
    canceled = service.cancel(UUID(queued["run_id"]))
    assert canceled["status"] == "CANCELED"
    assert service.allocation.get(UUID(attempt["id"]))["status"] == "RELEASED"
    with service.sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None and position.routeops_reserved_quantity == 0
    service._process(*claim)
    assert service.get(UUID(queued["run_id"]))["status"] == "CANCELED"


def test_operational_multiline_reservations_and_repeated_transitions(
    database: Engine, tmp_path: Path
) -> None:
    rows = _publication_rows()
    rows["order_lines"].append({**rows["order_lines"][0], "sku": "SKU-2", "quantity": "3"})
    rows["inventory"].append({**rows["inventory"][0], "sku": "SKU-2", "on_hand_quantity": "8"})
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    sessions = create_session_factory(database)
    service = OperationalAllocationService(sessions, FixedAllocationTravel())
    held = service.allocate(scenario, 1, "multiline")
    assert held["status"] == "HELD" and len(held["reservations"]) == 2
    assert {row["sku"] for row in held["reservations"]} == {"SKU-1", "SKU-2"}
    assert {row["center_id"] for row in held["reservations"]} == {"CD-001"}
    assert held["decisions"][0]["reason_code"] is None
    assert held["decisions"][0]["evidence"]["candidates"][0]["stock"]["SKU-1"] == {
        "on_hand": 10,
        "externally_reserved": 2,
        "safety_stock": 1,
        "routeops_reserved": 0,
        "available_before": 7,
    }
    with sessions() as session:
        positions = list(session.scalars(select(OperationalInventoryPositionModel)))
        assert {
            row.sku: (row.routeops_reserved_quantity, row.available_quantity) for row in positions
        } == {"SKU-1": (2, 5), "SKU-2": (3, 2)}
    assert service.allocate(scenario, 1, "multiline") == held
    confirmed = service.confirm(UUID(held["id"]))
    assert confirmed["status"] == "CONFIRMED"
    assert service.confirm(UUID(held["id"])) == confirmed
    released = service.release(UUID(held["id"]))
    assert released["status"] == "RELEASED"
    assert service.release(UUID(held["id"])) == released
    with pytest.raises(AllocationError, match="ALLOCATION_TRANSITION_INVALID"):
        service.confirm(UUID(held["id"]))
    with sessions() as session:
        positions = list(session.scalars(select(OperationalInventoryPositionModel)))
        assert {
            row.sku: (
                row.externally_reserved_quantity,
                row.routeops_reserved_quantity,
                row.available_quantity,
            )
            for row in positions
        } == {"SKU-1": (2, 0, 7), "SKU-2": (2, 0, 5)}
        assert session.scalar(select(func.count()).select_from(AllocationReservationLineModel)) == 2
        assert [
            item.to_status
            for item in session.scalars(
                select(AllocationAttemptEventModel).order_by(AllocationAttemptEventModel.sequence)
            )
        ] == ["BUILDING", "HELD", "CONFIRMED", "RELEASED"]
    snapshot = held["inventory_snapshot"]
    assert (
        hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == held["inventory_sha256"]
    )
    rejects(
        database,
        "UPDATE allocation_decision_snapshots SET inventory_sha256=:v WHERE attempt_id=:id",
        {"v": SHA, "id": held["id"]},
        "55000",
    )
    rejects(
        database,
        "DELETE FROM allocation_order_decisions WHERE attempt_id=:id",
        {"id": held["id"]},
        "55000",
    )


def test_published_precision_is_checked_with_exact_decimals(
    database: Engine, tmp_path: Path
) -> None:
    rows = _publication_rows()
    rows["order_lines"][0]["unit_weight_kg"] = "0.001"
    rows["order_lines"][0]["unit_volume_m3"] = "0.000001"
    rows["vehicles"][0]["capacity_weight_kg"] = "0.002"
    rows["vehicles"][0]["capacity_volume_m3"] = "0.000002"
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    service = OperationalAllocationService(
        create_session_factory(database), FixedAllocationTravel()
    )
    allocated = service.allocate(scenario, 1, "fine-precision")
    assert allocated["decisions"][0]["center_id"] == "CD-001"
    assert (
        allocated["decisions"][0]["evidence"]["candidates"][0]["vehicle_checks"][0][
            "capacity_compatible"
        ]
        is True
    )


def test_concurrent_allocations_cannot_overreserve_and_new_revision_keeps_holds(
    database: Engine, tmp_path: Path
) -> None:
    rows = _publication_rows()
    rows["order_lines"][0]["quantity"] = "5"
    scenario, storage, services = _published_allocation_fixture(database, tmp_path, rows)
    sessions = create_session_factory(database)
    service = OperationalAllocationService(sessions, FixedAllocationTravel())
    barrier = Barrier(2)

    def compete(key: str) -> dict[str, object]:
        barrier.wait(timeout=10)
        return service.allocate(scenario, 1, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(compete, "competing-a")
        second = pool.submit(compete, "competing-b")
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert sorted(len(result["reservations"]) for result in results) == [0, 1]
    assert {result["decisions"][0]["reason_code"] for result in results} == {
        None,
        "STOCK_NO_FULL_COVERAGE",
    }
    winner = next(result for result in results if result["reservations"])
    with sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None and position.routeops_reserved_quantity == 5
        assert position.available_quantity == 2
    second_batch = _validated_publication_batch(scenario, "second", rows, services, storage)
    revision, created = services[2].publish(scenario, second_batch)
    assert created and revision["revision_no"] == 2
    new_attempt = service.allocate(scenario, 2, "new-revision")
    assert new_attempt["decisions"][0]["reason_code"] == "STOCK_NO_FULL_COVERAGE"
    assert new_attempt["reservations"] == []
    with sessions() as session:
        position = session.scalar(select(OperationalInventoryPositionModel))
        assert position is not None and position.routeops_reserved_quantity == 5
        assert position.source_revision_id == UUID(revision["id"])
        assert position.externally_reserved_quantity == 2
    service.release(UUID(winner["id"]))
    after_release = service.allocate(scenario, 2, "after-release")
    assert len(after_release["reservations"]) == 1
    assert after_release["decisions"][0]["center_id"] == "CD-001"
    with pytest.raises(AllocationError, match="REVISION_NOT_CURRENT"):
        service.allocate(scenario, 1, "old-revision")


def test_concurrent_same_key_returns_one_attempt(database: Engine, tmp_path: Path) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = OperationalAllocationService(
        create_session_factory(database), FixedAllocationTravel()
    )
    barrier = Barrier(2)

    def retry() -> dict[str, object]:
        barrier.wait(timeout=10)
        return service.allocate(scenario, 1, "one-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(retry) for _ in range(2)]
        first, second = [future.result(timeout=30) for future in results]
    assert first["id"] == second["id"]
    with create_session_factory(database)() as session:
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 1
        assert session.scalar(select(func.count()).select_from(AllocationReservationLineModel)) == 1


def test_database_stock_guard_rolls_back_partial_reservation(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _publication_rows()
    rows["order_lines"].append({**rows["order_lines"][0], "sku": "SKU-2", "quantity": "3"})
    rows["inventory"].append({**rows["inventory"][0], "sku": "SKU-2", "on_hand_quantity": "3"})
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)

    def wrong_policy(*args: object, **kwargs: object) -> tuple[OrderAllocation, ...]:
        return (OrderAllocation("ORD-001", "CD-001", None, {"sequence": 1}),)

    monkeypatch.setattr(OperationalAllocationPolicy, "allocate", wrong_policy)
    service = OperationalAllocationService(
        create_session_factory(database), FixedAllocationTravel()
    )
    with pytest.raises(AllocationError, match="INVENTORY_CONCURRENT_CONFLICT"):
        service.allocate(scenario, 1, "fault-injected")
    with create_session_factory(database)() as session:
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 0
        assert (
            session.scalar(select(func.count()).select_from(OperationalInventoryPositionModel)) == 0
        )
        assert session.scalar(select(func.count()).select_from(OperationalInventoryStateModel)) == 0


def test_database_rejects_incomplete_selected_order(database: Engine, tmp_path: Path) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = OperationalAllocationService(
        create_session_factory(database), FixedAllocationTravel()
    )
    valid = service.allocate(scenario, 1, "valid")
    attempt_id = uuid4()
    decision_id = uuid4()
    started = datetime.now(UTC)
    finished = started + timedelta(microseconds=1)
    with pytest.raises(DBAPIError) as raised, database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO allocation_attempts "
                "(id,scenario_id,scenario_revision_id,inventory_snapshot_id,client_key,"
                "policy_version,status,version,created_at,transitioned_at) "
                "SELECT :id,scenario_id,scenario_revision_id,inventory_snapshot_id,'incomplete',"
                "policy_version,'BUILDING',0,:started,:started "
                "FROM allocation_attempts WHERE id=:source"
            ),
            {"id": attempt_id, "source": UUID(valid["id"]), "started": started},
        )
        connection.execute(
            text(
                "INSERT INTO allocation_attempt_events "
                "(id,attempt_id,sequence,from_status,to_status,occurred_at) "
                "VALUES (:id,:attempt,0,NULL,'BUILDING',:started)"
            ),
            {"id": uuid4(), "attempt": attempt_id, "started": started},
        )
        connection.execute(
            text(
                "INSERT INTO allocation_decision_snapshots "
                "(attempt_id,inventory_sha256,inventory_data,created_at) "
                "SELECT :attempt,inventory_sha256,inventory_data,:started "
                "FROM allocation_decision_snapshots WHERE attempt_id=:source"
            ),
            {"attempt": attempt_id, "source": UUID(valid["id"]), "started": started},
        )
        connection.execute(
            text(
                "INSERT INTO allocation_order_decisions "
                "(id,attempt_id,order_id,center_source_id,reason_code,sequence,evidence) "
                "SELECT :id,:attempt,order_id,center_source_id,reason_code,sequence,evidence "
                "FROM allocation_order_decisions WHERE attempt_id=:source"
            ),
            {"id": decision_id, "attempt": attempt_id, "source": UUID(valid["id"])},
        )
        connection.execute(
            text(
                "UPDATE allocation_attempts SET status='HELD',version=1,transitioned_at=:finished "
                "WHERE id=:attempt"
            ),
            {"attempt": attempt_id, "finished": finished},
        )
        connection.execute(
            text(
                "INSERT INTO allocation_attempt_events "
                "(id,attempt_id,sequence,from_status,to_status,occurred_at) "
                "VALUES (:id,:attempt,1,'BUILDING','HELD',:finished)"
            ),
            {"id": uuid4(), "attempt": attempt_id, "finished": finished},
        )
    assert getattr(raised.value.orig, "sqlstate", None) == "23514"
    with create_session_factory(database)() as session:
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 1


def test_new_revision_cannot_reconcile_below_active_holds(database: Engine, tmp_path: Path) -> None:
    rows = _publication_rows()
    rows["order_lines"][0]["quantity"] = "5"
    scenario, storage, services = _published_allocation_fixture(database, tmp_path, rows)
    sessions = create_session_factory(database)
    service = OperationalAllocationService(sessions, FixedAllocationTravel())
    held = service.allocate(scenario, 1, "held")
    reduced = _publication_rows()
    reduced["inventory"][0]["on_hand_quantity"] = "6"
    batch = _validated_publication_batch(scenario, "reduced", reduced, services, storage)
    revision, _ = services[2].publish(scenario, batch)
    with pytest.raises(AllocationError, match="INVENTORY_RECONCILIATION_CONFLICT"):
        service.allocate(scenario, 2, "should-fail")
    with sessions() as session:
        state = session.get(OperationalInventoryStateModel, scenario)
        assert state is not None and state.active_revision_id == UUID(held["scenario_revision_id"])
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 1
    service.release(UUID(held["id"]))
    retry = service.allocate(scenario, 2, "after-release")
    assert retry["inventory_snapshot"]["scenario_revision_id"] == revision["id"]


def test_routing_failure_rolls_back_activation_and_does_not_claim_stock_failure(
    database: Engine, tmp_path: Path
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)
    service = OperationalAllocationService(sessions, FailedAllocationTravel())
    with pytest.raises(AllocationError, match="ROUTING_DEPENDENCY_FAILED"):
        service.allocate(scenario, 1, "failed-osrm")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationalInventoryStateModel)) == 0
        assert (
            session.scalar(select(func.count()).select_from(OperationalInventoryPositionModel)) == 0
        )
        assert session.scalar(select(func.count()).select_from(AllocationAttemptModel)) == 0
        assert (
            session.scalar(select(func.count()).select_from(AllocationDecisionSnapshotModel)) == 0
        )


def test_operational_migration_downgrade_removes_only_new_empty_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "7a69c4d10e32")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                before = connection.scalar(text(TRIGGER_COUNT_SQL))
                postgis_oid = connection.scalar(
                    text("SELECT oid FROM pg_extension WHERE extname='postgis'")
                )
            migrate(monkeypatch, url, "d42e4a91b6c0")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == before + 10
            command.downgrade(Config(str(ALEMBIC_INI)), "7a69c4d10e32")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == before
                assert (
                    connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'"))
                    == postgis_oid
                )
        finally:
            engine.dispose()


def test_revision_run_migration_downgrade_preserves_prior_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "d42e4a91b6c0")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                before = connection.scalar(text(TRIGGER_COUNT_SQL))
                postgis_oid = connection.scalar(
                    text("SELECT oid FROM pg_extension WHERE extname='postgis'")
                )
            migrate(monkeypatch, url, "c951e2a7d430")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == before + 8
            command.downgrade(Config(str(ALEMBIC_INI)), "d42e4a91b6c0")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == before
                assert (
                    connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'"))
                    == postgis_oid
                )
        finally:
            engine.dispose()


def test_vehicle_limit_migration_preserves_prior_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "c951e2a7d430")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                postgis_oid = connection.scalar(
                    text("SELECT oid FROM pg_extension WHERE extname='postgis'")
                )
                trigger_count = connection.scalar(text(TRIGGER_COUNT_SQL))
            migrate(monkeypatch, url, "head")
            with engine.connect() as connection:
                columns = connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='vehicles' AND column_name LIKE 'max_%'"
                )).scalars().all()
                assert set(columns) == {
                    "max_route_distance_meters", "max_driving_seconds", "max_delivery_tasks"
                }
            command.downgrade(Config(str(ALEMBIC_INI)), "c951e2a7d430")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == trigger_count
                assert connection.scalar(
                    text("SELECT oid FROM pg_extension WHERE extname='postgis'")
                ) == postgis_oid
        finally:
            engine.dispose()


def test_vehicle_limits_persist_with_provenance_and_refuse_lossy_downgrade(
    database: Engine, tmp_path: Path,
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    services = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Route limits")
    rows = _publication_rows()
    rows["vehicles"][0].update({
        "max_route_distance_meters": "4000",
        "max_driving_seconds": "1200",
        "max_delivery_tasks": "1",
    })
    batch = _validated_publication_batch(scenario, "limits", rows, services, storage)
    revision, created = services[2].publish(scenario, batch)
    assert created and revision["contract_version"] == "2.2"
    with sessions() as session:
        row = session.scalar(select(VehicleModel).where(
            VehicleModel.scenario_revision_id == UUID(revision["id"])
        ))
        assert row is not None
        assert (row.max_route_distance_meters, row.max_driving_seconds,
                row.max_delivery_tasks) == (4000, 1200, 1)
        assert row.source_row_number == 2 and row.source_import_file_id is not None
        prepared = load_prepared_revision(session, UUID(revision["id"]))
        assert prepared.vehicles[0].max_route_distance_meters == 4000
        assert prepared.vehicles[0].max_driving_seconds == 1200
        assert prepared.vehicles[0].max_delivery_tasks == 1
        vehicle_id = row.id
    with pytest.raises(DBAPIError), sessions.begin() as session:
        session.execute(
            text("UPDATE vehicles SET max_delivery_tasks=2 WHERE id=:vehicle_id"),
            {"vehicle_id": vehicle_id},
        )
    with pytest.raises(RuntimeError, match="published vehicles have route limits"):
        command.downgrade(Config(str(ALEMBIC_INI)), "c951e2a7d430")


def test_legacy_21_batch_replays_and_publishes_without_new_vehicle_fields(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "objects")
    upload, validation, publisher = _publication_services(
        database, storage, tmp_path / "spool"
    )
    scenario = ScenarioRepository(sessions).create("Legacy replay")
    files: list[ReceivedFile] = []
    rows = _publication_rows()
    for name in DATASETS:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=[spec.name for spec in LEGACY_SCHEMA[name]])
        writer.writeheader()
        writer.writerows(rows[name])
        handle = storage.begin()
        handle.write(stream.getvalue().encode())
        files.append(ReceivedFile(name, f"{name}.csv", handle.finish()))
    manifest = hashlib.sha256()
    for item in sorted(files, key=lambda file: file.dataset):
        manifest.update(
            f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode()
        )
    monkeypatch.setattr(
        "routeops.infrastructure.persistence.import_upload_repository.CONTRACT_VERSION", "2.1"
    )
    batch, _ = upload.create(
        scenario, "legacy", ReceivedPackage(tuple(files), manifest.hexdigest())
    )
    legacy = replace(
        _validation_context(), contract_version="2.1", validator_version="2.3b.1"
    )
    validation.request(scenario, batch, legacy)
    claim = validation.claim()
    assert claim is not None and claim[0] == batch
    originals, context, _ = validation._read(batch)
    report = validate_package(originals, context=context)
    assert report.valid and report.contract_version == "2.1"
    assert validation.finish(batch, claim[1], report)
    revision, created = publisher.publish(scenario, batch)
    assert created and revision["contract_version"] == "2.1"
    with sessions() as session:
        vehicle = session.scalar(select(VehicleModel).where(
            VehicleModel.scenario_revision_id == UUID(revision["id"])
        ))
        assert vehicle is not None
        assert vehicle.max_route_distance_meters is None
        assert vehicle.max_driving_seconds is None
        assert vehicle.max_delivery_tasks is None



def test_historical_demo_without_rate_snapshot_is_not_repriced(database: Engine) -> None:
    sessions = create_session_factory(database)
    repository = DatabaseRunRepository(sessions)
    run_id = uuid4()
    now = datetime.now(UTC)
    repository.start(run_id, "Historical demo", now, {"dataset_name": "historical"})
    with pytest.raises(OperatingCostError, match="COST_RESULT_NOT_AVAILABLE"):
        OperatingCostQuery(sessions).get(run_id)
    result = {"status": "SUCCEEDED", "routes": [], "unassigned": [], "summary": {
        "objective_cost_units": 0, "cost_scale": 100, "currency": "CLP",
    }}
    repository.complete(run_id, now, result, {"estimated_cost": 0})
    before = repository.get(run_id)
    with pytest.raises(OperatingCostError, match="COST_RATES_NOT_RECORDED"):
        OperatingCostQuery(sessions).get(run_id)
    assert repository.get(run_id) == before
    with pytest.raises(OperatingCostError, match="RUN_NOT_FOUND"):
        OperatingCostQuery(sessions).get(uuid4())



def test_real_vroom_cost_excludes_forced_wait_and_service(database: Engine, tmp_path: Path) -> None:
    from routeops.application.operating_cost import VehicleRate, estimate_operating_cost
    from routeops.application.revision_problem import load_prepared_revision
    from routeops.application.serialization import to_primitive

    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)
    with sessions() as session:
        revision = session.scalar(select(ScenarioRevisionModel).where(
            ScenarioRevisionModel.scenario_id == scenario))
        assert revision is not None
        prepared = load_prepared_revision(session, revision.id)
    request = prepared.problem(uuid4(), {prepared.orders[0].id: prepared.centers[0].id},
                               SolutionQuality.BALANCED, 15)
    first = replace(request.tasks[0], time_window_start=request.vehicles[0].shift_start + timedelta(
        minutes=30), time_window_end=request.vehicles[0].shift_start + timedelta(minutes=40),
        service_seconds=600)
    second = replace(first, task_id=uuid4(), order_id="LATE", time_window_start=
                     request.vehicles[0].shift_start + timedelta(hours=3), time_window_end=
                     request.vehicles[0].shift_start + timedelta(hours=3, minutes=10))
    vehicle = replace(request.vehicles[0], costs=replace(request.vehicles[0].costs,
                      fixed_units=100_000, per_duty_hour_units=360_000, per_km_units=20_000))
    request = replace(request, tasks=(first, second), vehicles=(vehicle,))
    result = VroomAdapter(VROOM_TEST_URL).solve(request)
    assert result.summary.assigned_task_count == 2
    assert result.summary.service_seconds == 1200
    assert result.summary.waiting_seconds >= 3600
    business = estimate_operating_cost(to_primitive(result.routes), {
        vehicle.source_vehicle_id: VehicleRate(Decimal(10), Decimal(36), Decimal(2)),
    }, prepared.currency)
    solver_decimal = Decimal(result.summary.objective_cost_units) / result.summary.cost_scale
    assert Decimal(business["total"]) > solver_decimal + Decimal(36)
    driving_proxy_units = (Decimal(100_000) + Decimal(360_000) * result.summary.driving_seconds
                           / 3600 + Decimal(20_000) * result.summary.distance_meters / 1000)
    assert abs(Decimal(result.summary.objective_cost_units) - driving_proxy_units) <= 1


def test_real_vroom_applies_optional_route_limits(database: Engine, tmp_path: Path) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    sessions = create_session_factory(database)
    with sessions() as session:
        revision = session.scalar(select(ScenarioRevisionModel).where(
            ScenarioRevisionModel.scenario_id == scenario
        ))
        assert revision is not None
        prepared = load_prepared_revision(session, revision.id)
    request = prepared.problem(
        uuid4(), {prepared.orders[0].id: prepared.centers[0].id},
        SolutionQuality.BALANCED, 15,
    )
    solver = VroomAdapter(VROOM_TEST_URL)
    baseline = solver.solve(request)
    assert baseline.summary.assigned_task_count == 1
    route = baseline.routes[0]
    vehicle = replace(
        request.vehicles[0],
        max_route_distance_meters=route.totals.distance_meters,
        max_driving_seconds=route.totals.driving_seconds,
        max_delivery_tasks=1,
    )
    limited = replace(request, contract_version="1.1", vehicles=(vehicle,))
    exact = solver.solve(limited)
    assert exact.summary.assigned_task_count == 1
    from routeops.application.optimization_reconciliation import reconcile_result
    reconcile_result(limited, exact)
    extra_task = replace(request.tasks[0], task_id=uuid4(), order_id="SECOND")
    two_tasks = replace(limited, tasks=(*limited.tasks, extra_task))
    counted = solver.solve(two_tasks)
    assert counted.summary.assigned_task_count == 1
    assert counted.summary.unassigned_task_count == 1
    reconcile_result(two_tasks, counted)


def test_published_vehicle_limits_reach_recoverable_run_and_release_on_solver_error(
    database: Engine, tmp_path: Path,
) -> None:
    rows = _publication_rows()
    rows["vehicles"][0].update({
        "max_route_distance_meters": "100000",
        "max_driving_seconds": "10000",
        "max_delivery_tasks": "1",
    })
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    sessions = create_session_factory(database)
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions, OperationalAllocationService(sessions, osrm), osrm,
        VroomAdapter(VROOM_TEST_URL), WorkloadLimits(),
    )
    queued, _ = service.submit(scenario, 1, "limited-real")
    assert service.process_once()
    ready = service.get(UUID(queued["run_id"]))
    assert ready["status"] == "READY", ready["error"]
    assert ready["result"]["contract_version"] == "1.1"
    assert ready["decisions"][0]["reservation_status"] == "HELD"
    broken = _revision_run_service(database, InconsistentSolver("contract"))
    second, _ = broken.submit(scenario, 1, "limited-broken")
    assert broken.process_once()
    failed = broken.get(UUID(second["run_id"]))
    assert failed["status"] == "FAILED"
    assert failed["decisions"][0]["reservation_status"] == "RELEASED"
    assert service.get(UUID(queued["run_id"]))["decisions"][0]["reservation_status"] == "HELD"


def test_fractional_time_is_rejected_before_run_or_reservation(
    database: Engine, tmp_path: Path,
) -> None:
    rows = _publication_rows()
    rows["orders"][0]["time_window_start"] = "2026-10-15T09:00:00.000001-03:00"
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    service = _revision_run_service(database)
    with pytest.raises(RunInputError, match="RUN_TIME_PRECISION_INVALID"):
        service.submit(scenario, 1, "fractional-second")
    with create_session_factory(database)() as session:
        assert session.scalar(select(func.count()).select_from(RevisionRunJobModel)) == 0
        assert session.scalar(select(func.count()).select_from(
            OperationalInventoryPositionModel
        )) == 0


def test_metrics_multiline_unrouted_plan_survives_cancel_without_changing_facts(
    database: Engine, tmp_path: Path,
) -> None:
    rows = _publication_rows()
    rows["order_lines"].append({**rows["order_lines"][0], "sku": "SECOND"})
    rows["inventory"].append({**rows["inventory"][0], "sku": "SECOND"})
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    service = _revision_run_service(database)
    submitted, _ = service.submit(scenario, 1, "metric-empty-route")
    run_id = UUID(submitted["run_id"])
    query = PlanMetricsQuery(service.sessions)
    assert query.get(run_id)["plan"] is None
    assert service.process_once()
    before = query.get(run_id)
    metrics = before["plan"]["metrics"]
    assert metrics["valid_input_orders"]["value"] == 1  # two lines, one order
    assert metrics["allocated_orders"]["value"] == 1
    assert metrics["routed_orders"]["value"] == 0
    assert metrics["unrouted_orders"]["value"] == 1
    assert metrics["coverage"]["value"] == "0"
    assert metrics["operating_cost"]["value"] == "0.0000"
    assert metrics["window_compliance"]["value"] is None
    assert before["plan"]["duty_balance"]["coefficient_of_variation"]["value"] is None
    assert service.get(run_id)["decisions"][0]["reservation_status"] == "RELEASED"
    service.cancel(run_id)
    after = query.get(run_id)
    assert after["current_status"] == "CANCELED"
    assert after["plan"] == before["plan"]
    assert after["processing"] == before["processing"]
    assert after["provenance"] == before["provenance"]


def test_metrics_recovery_fences_timing_and_retains_unknown_interrupted_phase(
    database: Engine, tmp_path: Path,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    service.submit(scenario, 1, "metric-recovery")
    first = service.claim()
    assert first is not None
    start_event = {"kind": "PHASE_STARTED", "phase": "SOLVER", "occurred_at": datetime.now(UTC)}
    service._record_timing(*first, start_event)
    with service.sessions.begin() as session:
        job = session.get(RevisionRunJobModel, first[0])
        assert job is not None
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    second = service.claim()
    assert second is not None
    with service.sessions() as session:
        count = session.scalar(select(func.count()).select_from(PlanningTimingEventModel))
    service._record_timing(*first, {**start_event, "phase": "LATE"})
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PlanningTimingEventModel)) == count
    # Database fencing also rejects a direct write from the replaced owner.
    with pytest.raises(DBAPIError), service.sessions.begin() as session:
        session.add(PlanningTimingEventModel(
            run_id=first[0], attempt_no=1, owner_token=first[1],
            kind="PHASE_FINISHED", phase="SOLVER",
            occurred_at=datetime.now(UTC), duration_ns=1, outcome="SUCCEEDED",
            calculation_version="processing-v1", details={},
        ))
    service._process(*second)
    result = PlanMetricsQuery(service.sessions).get(first[0])
    times = result["processing"]
    assert times["active_all_attempts"]["value"] is None
    assert times["measured_active_subtotal"]["value"] is not None
    assert times["attempts"][0]["ended_at"] is None
    assert times["attempts"][0]["phases"][0]["duration"]["value"] is None
    assert times["attempts"][0]["outcome"] == "LEASE_RECOVERED"
    assert times["retry_recovery_waits"][0]["value"] is None
    assert times["attempts"][1]["outcome"] == "READY"


def test_metrics_failed_solver_keeps_phases_and_released_stock(
    database: Engine, tmp_path: Path,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database, BrokenSolver())
    submitted, _ = service.submit(scenario, 1, "metric-failure")
    assert service.process_once()
    result = PlanMetricsQuery(service.sessions).get(UUID(submitted["run_id"]))
    assert result["plan"] is None
    assert result["current_status"] == "FAILED"
    attempt = result["processing"]["attempts"][0]
    assert attempt["active"]["value"] is not None
    assert attempt["outcome"] == "FAILED"
    solver = next(phase for phase in attempt["phases"] if phase["phase"] == "SOLVER")
    assert solver["outcome"] == "FAILED" and solver["duration"]["value"] is not None
    decisions = service.get(UUID(submitted["run_id"]))["decisions"]
    assert decisions[0]["reservation_status"] == "RELEASED"


def test_metrics_cancel_running_attempt_has_unknown_end_and_rejects_late_timing(
    database: Engine, tmp_path: Path,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    service.submit(scenario, 1, "metric-cancel-running")
    claim = service.claim()
    assert claim is not None
    service._record_timing(*claim, {"kind": "PHASE_STARTED", "phase": "PREPARE_OSRM",
                                   "occurred_at": datetime.now(UTC)})
    service.cancel(claim[0])
    service._record_timing(*claim, {"kind": "PHASE_FINISHED", "phase": "PREPARE_OSRM",
                                   "occurred_at": datetime.now(UTC), "duration_ns": 1})
    times = PlanMetricsQuery(service.sessions).get(claim[0])["processing"]
    assert times["total_elapsed"]["value"] is not None
    assert times["active_all_attempts"]["value"] is None
    assert times["attempts"][0]["outcome"] == "USER_CANCELED"
    assert times["attempts"][0]["phases"][0]["duration"]["value"] is None


def test_metrics_historical_demo_is_read_only_and_does_not_invent_input_or_timings(
    database: Engine,
) -> None:
    sessions = create_session_factory(database)
    repository = DatabaseRunRepository(sessions)
    run_id = uuid4()
    now = datetime.now(UTC)
    repository.start(run_id, "Historical", now, {"dataset_name": "old"})
    repository.complete(run_id, now + timedelta(seconds=5), {
        "status": "SUCCEEDED", "routes": [], "unassigned": [],
        "summary": {"objective_cost_units": 0, "cost_scale": 100, "currency": "CLP"},
    }, {"estimated_cost": 0})
    before = repository.get(run_id)
    result = PlanMetricsQuery(sessions).get(run_id)
    assert result["plan"]["metrics"]["valid_input_orders"]["value"] is None
    assert result["plan"]["metrics"]["operating_cost"]["value"] == "0.0000"
    assert result["processing"]["active_all_attempts"]["value"] is None
    assert result["processing"]["total_elapsed"]["value"] == "5.0"
    assert result["provenance"]["historical_derivation"]
    assert repository.get(run_id) == before


def test_processing_measurement_migration_empty_downgrade_preserves_history_and_postgis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "e3a1b7c9d240")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                old_triggers = connection.scalar(text(TRIGGER_COUNT_SQL))
                postgis = connection.scalar(text(
                    "SELECT oid FROM pg_extension WHERE extname='postgis'"
                ))
            sessions = create_session_factory(engine)
            run_id = uuid4()
            DatabaseRunRepository(sessions).start(run_id, "Legacy data", datetime.now(UTC), {})
            migrate(monkeypatch, url, "head")
            command.downgrade(Config(str(ALEMBIC_INI)), "e3a1b7c9d240")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == old_triggers
                assert connection.scalar(text(
                    "SELECT oid FROM pg_extension WHERE extname='postgis'"
                )) == postgis
                assert connection.scalar(text(
                    "SELECT to_regclass('planning_timing_events')"
                )) is None
                assert connection.scalar(text("SELECT count(*) FROM planning_runs")) == 1
                assert connection.scalar(text(
                    "SELECT to_regprocedure('routeops_guard_timing_event()')"
                )) is None
            migrate(monkeypatch, url, "head")
            assert DatabaseRunRepository(sessions).get(run_id) is not None
        finally:
            engine.dispose()


def test_processing_measurements_are_immutable_and_refuse_lossy_downgrade(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    submitted, _ = service.submit(scenario, 1, "metric-immutable")
    service.process_once()
    with pytest.raises(DBAPIError), service.sessions.begin() as session:
        session.execute(text("UPDATE planning_timing_events SET duration_ns=0"))
    with pytest.raises(DBAPIError), service.sessions.begin() as session:
        session.execute(text("DELETE FROM planning_timing_events"))
    with pytest.raises(RuntimeError, match="processing measurements contain history"):
        command.downgrade(Config(str(ALEMBIC_INI)), "e3a1b7c9d240")
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "plan_metric_queries", PlanMetricsQuery(service.sessions))
    with TestClient(api.app) as client:
        response = client.get(f"/api/v1/runs/{submitted['run_id']}/metrics")
        assert response.status_code == 200
        assert response.json()["plan"]["calculation_version"] == "plan-metrics-v1"
        objective = response.json()["plan"]["solver_objective"]
        assert objective["unit"] == "scaled_currency_units"
        assert objective["calculation_version"] == "persisted-solver-objective-v1"
        assert "owner_token" not in response.text and "lease_token" not in response.text
        missing = client.get(f"/api/v1/runs/{uuid4()}/metrics")
        assert missing.status_code == 404 and missing.json()["code"] == "RUN_NOT_FOUND"


def test_demo_failed_persistence_keeps_measured_phase_and_atomic_rollback(
    database: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = create_session_factory(database)
    repository = DatabaseRunRepository(sessions)
    run_id, token = uuid4(), uuid4()
    now = datetime.now(UTC)
    repository.start(run_id, "Faulted demo", now, {})
    repository.record_timing(run_id, token, {"kind": "ATTEMPT_STARTED", "occurred_at": now})
    timer = AttemptTimer(lambda event: repository.record_timing(run_id, token, event))

    def fault(_: object) -> None:
        raise OSError("synthetic persistence failure")

    monkeypatch.setattr(repository, "_geometry", fault)
    with pytest.raises(OSError):
        repository.complete(run_id, now, {"status": "PARTIAL", "unassigned": [],
                                         "routes": [{"geometry": []}]}, {},
                            timer=timer, timing_token=token)
    repository.fail(run_id, datetime.now(UTC), "TEST_FAILURE", timer=timer, timing_token=token)
    result = PlanMetricsQuery(sessions).get(run_id)
    assert result["plan"] is None and result["current_status"] == "FAILED"
    attempt = result["processing"]["attempts"][0]
    assert attempt["phases"][0]["phase"] == "PERSIST_RESULT"
    assert attempt["phases"][0]["outcome"] == "FAILED"
    assert attempt["phases"][0]["duration"]["value"] is not None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizedRouteModel)) == 0
