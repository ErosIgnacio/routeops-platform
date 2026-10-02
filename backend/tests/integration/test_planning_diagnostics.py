"""Diagnoses against disposable PostGIS and actual gateway exchanges."""

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from test_persistence_migration import (
    ALEMBIC_INI,
    OSRM_TEST_URL,
    TRIGGER_COUNT_SQL,
    VROOM_TEST_URL,
    BrokenSolver,
    _publication_services,
    _published_allocation_fixture,
    _revision_run_service,
    _validation_context,
    migrate,
    temporary_database,
)
from test_persistence_migration import (
    database as database,
)

from routeops.application.diagnostics import failure_document
from routeops.application.revision_problem import WorkloadLimits
from routeops.infrastructure.data.demo_catalog import DemoCatalogService
from routeops.infrastructure.data.operation_cases import CASE_NAMES, case_rows
from routeops.infrastructure.persistence.diagnostics import DiagnosticQuery, persist_diagnostics
from routeops.infrastructure.persistence.models import (
    ImportBatchModel,
    OperationalInventoryPositionModel,
    OptimizedRouteModel,
    PlanningDiagnosticModel,
    PlanningRunModel,
    RevisionRunJobModel,
)
from routeops.infrastructure.persistence.operating_costs import OperatingCostQuery
from routeops.infrastructure.persistence.operational_allocation import OperationalAllocationService
from routeops.infrastructure.persistence.plan_metrics import PlanMetricsQuery
from routeops.infrastructure.persistence.planning_data_repository import ScenarioRepository
from routeops.infrastructure.persistence.repository import DatabaseRunRepository
from routeops.infrastructure.persistence.revision_runs import RevisionRunService
from routeops.infrastructure.persistence.session import create_session_factory
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.vroom import VroomAdapter
from routeops.infrastructure.storage import LocalObjectStorage

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", CASE_NAMES)
def test_new_operation_case_real_solver_diagnostics_metrics_and_reservations(
    database: Engine,
    tmp_path: Path,
    name: str,
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "originals")
    upload, validation, publication = _publication_services(database, storage, tmp_path / "spool")
    catalog = DemoCatalogService(
        ScenarioRepository(sessions), storage, upload, validation, publication
    )
    prepared = catalog.prepare_rows(f"Test 3.1c {name}", name, case_rows(name))
    osrm = OsrmClient(OSRM_TEST_URL)
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
    )
    queued, _ = service.submit(UUID(prepared["scenario_id"]), 1, name)
    run_id = UUID(queued["run_id"])
    assert service.process_once()
    ready = service.get(run_id)
    assert ready["status"] == "READY", ready["error"]
    query = DiagnosticQuery(sessions)
    diagnostics = query.get(run_id)
    assert diagnostics["persistence"] == "immutable_document"
    routed = {
        step["order_id"]
        for route in ready["result"]["routes"]
        for step in route["steps"]
        if step["kind"] == "DELIVERY"
    }
    items = diagnostics["items"]
    for decision in ready["decisions"]:
        if decision["center_id"] is None:
            assert decision["reservation_status"] is None
            primary = next(item for item in items if item["order_id"] == decision["order_id"])
            assert primary["code"] == decision["reason_code"]
        else:
            assert decision["reservation_status"] == (
                "HELD" if decision["order_id"] in routed else "RELEASED"
            )
    if name.endswith("feasible") and name != "b2c-distance-inferred":
        assert len(routed) == (2 if name.startswith("b2b") else 6)
        assert items == []
    elif name == "b2b-diagnostics":
        assert "B2B-OK" in routed
        for key, cause in (
            ("B2B-SKILL", "NO_SKILL_COMPATIBLE_VEHICLE"),
            ("B2B-WEIGHT", "INDIVIDUAL_CAPACITY_EXCEEDED"),
            ("B2B-VOLUME", "INDIVIDUAL_CAPACITY_EXCEEDED"),
            ("B2B-UNITS", "INDIVIDUAL_CAPACITY_EXCEEDED"),
            ("B2B-SERVICE", "WINDOW_SERVICE_SHIFT_INFEASIBLE"),
        ):
            assert any(
                item["order_id"] == key and item["code"] == cause and item["certainty"] == "PROVEN"
                for item in items
            )
    elif name == "b2c-task-pressure":
        assert len(routed) == 2
        collective = [item for item in items if item["code"] == "FLEET_TASK_LIMIT_SHORTFALL"]
        assert len(collective) == 4
        assert all(item["evidence"]["minimum_unrouted"] == 4 for item in collective)
        assert all(not item["evidence"]["individual_cause_proven"] for item in collective)
    else:
        assert not routed
        assert all(item["certainty"] == "INFERRED" for item in items)
        assert {item["code"] for item in items} == {
            "VEHICLE_LIMITS_CONTEXT",
            "SOLVER_NO_FEASIBLE_ROUTE",
        }
    with sessions() as session:
        assert all(
            row.externally_reserved_quantity == 5
            for row in session.scalars(select(OperationalInventoryPositionModel))
        )
    metrics = PlanMetricsQuery(sessions).get(run_id)
    costs = OperatingCostQuery(sessions).get(run_id)
    assert metrics["plan"]["metrics"]["routed_orders"]["value"] == len(routed)
    assert metrics["plan"]["operating_cost"] == costs
    service.cancel(run_id)  # only this disposable scenario's reservations
    after = query.get(run_id)
    assert after["items"] == items and after["document_sha256"] == diagnostics["document_sha256"]
    assert PlanMetricsQuery(sessions).get(run_id)["plan"] == metrics["plan"]


def test_diagnostics_immutable_paged_filtered_and_no_live_stock_reinterpretation(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    queued, _ = service.submit(scenario, 1, "query")
    run_id = UUID(queued["run_id"])
    assert service.process_once()
    query = DiagnosticQuery(service.sessions)
    before = query.get(run_id)
    assert before["items"][0]["code"] == "SOLVER_NO_FEASIBLE_ROUTE"
    with pytest.raises(DBAPIError), service.sessions.begin() as session:
        session.execute(text("UPDATE planning_diagnostics SET content_sha256=repeat('b',64)"))
    with pytest.raises(DBAPIError), service.sessions.begin() as session:
        session.execute(text("DELETE FROM planning_diagnostics"))
    with pytest.raises(RuntimeError, match="diagnostics contain history"):
        command.downgrade(Config(str(ALEMBIC_INI)), "f6b2d8a4c190")
    # Another run changes operational availability; diagnosis still uses stored evidence.
    service.submit(scenario, 1, "another")
    assert service.process_once()
    assert query.get(run_id) == before
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "diagnostic_queries", query)
    with TestClient(api.app) as client:
        path = f"/api/v1/runs/{run_id}/diagnostics"
        assert client.get(path).json() == before
        assert client.get(path, params={"stage": "OPERATIONAL"}).json()["items"] == []
        assert client.get(path, params={"certainty": "INFERRED", "limit": 1}).json()["total"] == 1
        assert client.get(path, params={"offset": 1}).json()["items"] == []
        assert client.get(path, params={"certainty": "CERTAIN"}).status_code == 422
        assert client.get(path, params={"limit": 0}).status_code == 422
        assert client.get(f"/api/v1/runs/{uuid4()}/diagnostics").status_code == 404
        assert "owner_token" not in client.get(path).text


def test_failure_is_operational_and_stale_worker_cannot_persist_a_diagnosis(
    database: Engine,
    tmp_path: Path,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database, BrokenSolver())
    service.submit(scenario, 1, "fence")
    first = service.claim()
    assert first is not None
    with service.sessions.begin() as session:
        job = session.get(RevisionRunJobModel, first[0])
        assert job is not None
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    second = service.claim()
    assert second is not None
    service._fail(*first, "STALE_ERROR")
    with (
        pytest.raises(DBAPIError, match="diagnostic owner fenced"),
        service.sessions.begin() as session,
    ):
        run = session.get(PlanningRunModel, first[0])
        assert run is not None
        run.error = "STALE_ERROR"
        persist_diagnostics(session, first[0], failure_document("STALE_ERROR", {}), first[1], 1)
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PlanningDiagnosticModel)) == 0
        assert session.get(PlanningRunModel, first[0]).error is None
    # Current worker failure atomically releases its stock and persists one operational record.
    service._fail(*second, "SOLVER_DEPENDENCY_FAILED")
    service._fail(*second, "SOLVER_DEPENDENCY_FAILED")
    value = DiagnosticQuery(service.sessions).get(first[0])
    assert len(value["items"]) == 1
    assert value["items"][0]["stage"] == "OPERATIONAL"
    assert value["items"][0]["code"] == "SOLVER_DEPENDENCY_FAILED"
    assert value["items"][0]["order_id"] is None


def test_diagnostic_insert_failure_rolls_back_result_and_releases_only_run_holds(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, _, _ = _published_allocation_fixture(database, tmp_path)
    service = _revision_run_service(database)
    module = importlib.import_module("routeops.infrastructure.persistence.revision_runs")
    original = module.persist_diagnostics

    def interrupted(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        if not args[2]["operational"]:
            raise OSError("synthetic interruption after diagnostic insert")

    monkeypatch.setattr(module, "persist_diagnostics", interrupted)
    submitted, _ = service.submit(scenario, 1, "rollback")
    assert service.process_once()
    run_id = UUID(submitted["run_id"])
    value = service.get(run_id)
    assert value["status"] == "FAILED" and value["result"] is None
    assert value["decisions"][0]["reservation_status"] == "RELEASED"
    diagnosed = DiagnosticQuery(service.sessions).get(run_id)
    assert len(diagnosed["items"]) == 1 and diagnosed["items"][0]["stage"] == "OPERATIONAL"
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizedRouteModel)) == 0
        assert session.scalar(select(func.count()).select_from(PlanningDiagnosticModel)) == 1
        assert (
            session.scalar(select(OperationalInventoryPositionModel)).externally_reserved_quantity
            == 2
        )


def test_diagnostic_migration_empty_downgrade_and_legacy_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database() as url:
        migrate(monkeypatch, url, "f6b2d8a4c190")
        engine = create_engine(url)
        try:
            sessions = create_session_factory(engine)
            repository = DatabaseRunRepository(sessions)
            run_id = uuid4()
            now = datetime.now(UTC)
            repository.start(run_id, "Legacy", now, {})
            repository.complete(
                run_id,
                now,
                {
                    "status": "PARTIAL",
                    "routes": [],
                    "unassigned": [
                        {
                            "order_id": "HIST",
                            "stage": "OPTIMIZATION",
                            "reasons": [
                                {
                                    "code": "SOLVER_NO_FEASIBLE_ROUTE",
                                    "certainty": "INFERRED",
                                    "detail": "Historical",
                                    "evidence": {},
                                }
                            ],
                        }
                    ],
                },
                {},
            )
            before = repository.get(run_id)
            with engine.connect() as connection:
                old_triggers = connection.scalar(text(TRIGGER_COUNT_SQL))
                postgis = connection.scalar(
                    text("SELECT oid FROM pg_extension WHERE extname='postgis'")
                )
            migrate(monkeypatch, url, "head")
            value = DiagnosticQuery(sessions).get(run_id)
            assert value["persistence"] == "legacy_result_only"
            assert value["calculation_version"] == "legacy-recorded-reasons"
            assert value["items"][0]["detail"] == "Historical"
            assert repository.get(run_id) == before
            command.downgrade(Config(str(ALEMBIC_INI)), "f6b2d8a4c190")
            with engine.connect() as connection:
                assert connection.scalar(text(TRIGGER_COUNT_SQL)) == old_triggers
                assert (
                    connection.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'"))
                    == postgis
                )
                assert (
                    connection.scalar(
                        text("SELECT to_regprocedure('routeops_guard_diagnostics()')")
                    )
                    is None
                )
                assert (
                    connection.scalar(text("SELECT to_regclass('planning_timing_events')"))
                    is not None
                )
            migrate(monkeypatch, url, "head")
            assert repository.get(run_id) == before
        finally:
            engine.dispose()


def test_invalid_import_diagnostics_are_separate_from_any_published_run(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = create_session_factory(database)
    storage = LocalObjectStorage(tmp_path / "originals")
    upload, validation, publication = _publication_services(database, storage, tmp_path / "spool")
    scenario = ScenarioRepository(sessions).create("Invalid input 3.1c")
    catalog = DemoCatalogService(
        ScenarioRepository(sessions), storage, upload, validation, publication
    )
    prepared = catalog.prepare_rows("Valid reference", "valid", case_rows("b2b-feasible"))
    # New batch in separate scenario: no publication, no run, existing immutable issue storage.
    from routeops.infrastructure.data.demo_catalog import _package

    rows = case_rows("b2b-feasible")
    rows["orders"][0]["latitude"] = "100"
    batch, _ = upload.create(scenario, "invalid", _package(storage, rows))
    validation.request(scenario, batch, _validation_context())
    assert validation.process_once()
    assert validation.get(scenario, batch)["status"] == "INVALID"
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "validation_service", validation)
    with TestClient(api.app) as client:
        response = client.get(
            f"/api/v1/scenarios/{scenario}/imports/{batch}/diagnostics", params={"limit": 1}
        )
        assert response.status_code == 200
        value = response.json()
        item = value["items"][0]
        assert item["stage"] == "VALIDATION" and item["certainty"] == "PROVEN"
        assert item["code"] == "COORDINATE_RANGE"
        assert item["evidence"]["dataset"] == "orders"
        assert item["evidence"]["row"] == 2 and item["evidence"]["field"] == "latitude"
        assert "order_id" not in item and "value_excerpt" not in response.text
        assert value["provenance"]["validator_version"] == "3.1a.1"
    with sessions() as session:
        assert session.get(ImportBatchModel, batch).status == "INVALID"
        assert session.scalar(select(func.count()).select_from(PlanningRunModel)) == 0
    assert prepared["scenario_id"] != str(scenario)


def test_timing_guard_still_blocks_loss_when_diagnostic_table_is_empty(database: Engine) -> None:
    sessions = create_session_factory(database)
    repository = DatabaseRunRepository(sessions)
    run_id, token = uuid4(), uuid4()
    repository.start(run_id, "Timing-only history", datetime.now(UTC), {})
    repository.record_timing(
        run_id,
        token,
        {
            "kind": "ATTEMPT_STARTED",
            "occurred_at": datetime.now(UTC),
        },
    )
    before = repository.get(run_id)
    with pytest.raises(RuntimeError, match="processing measurements contain history"):
        command.downgrade(Config(str(ALEMBIC_INI)), "e3a1b7c9d240")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "a71c9e3d602b"
        assert connection.scalar(text("SELECT count(*) FROM planning_timing_events")) == 1
        assert connection.scalar(text("SELECT count(*) FROM planning_diagnostics")) == 0
    assert repository.get(run_id) == before
