"""Exercise the new schema against a disposable PostgreSQL/PostGIS database."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_upload import ReceivedFile, ReceivedPackage, UploadError
from routeops.application.import_validation import (
    ImportLimits,
    Issue,
    ValidationReport,
    validate_package,
)
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
    OrderLineModel,
    OrderModel,
    ScenarioModel,
    ScenarioRevisionModel,
    ValidationIssueModel,
    VehicleModel,
)
from routeops.infrastructure.persistence.planning_data_repository import (
    ImportBatchRepository,
    ScenarioRepository,
    normalize_skills,
    validate_timezone,
)
from routeops.infrastructure.persistence.repository import DatabaseRunRepository
from routeops.infrastructure.persistence.session import create_session_factory
from routeops.infrastructure.storage import LocalObjectStorage
from routeops.infrastructure.storage.maintenance import ImportStorageMaintenance

pytestmark = pytest.mark.integration
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
SHA = "a" * 64
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
            migrate(monkeypatch, url, "head")
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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "7a69c4d10e32"


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
