"""Analytic simulations against disposable PostGIS and the real road/solver stack."""

import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_persistence_migration import (
    ALEMBIC_INI,
    OSRM_TEST_URL,
    VROOM_TEST_URL,
    FixedAllocationTravel,
    _published_allocation_fixture,
    _validated_publication_batch,
    migrate,
    temporary_database,
)
from test_persistence_migration import database as database

from routeops.application.plan_comparison import ComparisonError
from routeops.application.revision_problem import WorkloadLimits
from routeops.infrastructure.data.allocation_demos import allocation_demos
from routeops.infrastructure.data.demo_catalog import demo_rows
from routeops.infrastructure.data.operation_cases import CASE_NAMES, case_rows
from routeops.infrastructure.persistence.models import (
    ComparisonJobModel,
    ComparisonResultModel,
    PlanComparisonModel,
)
from routeops.infrastructure.persistence.operating_costs import _hash
from routeops.infrastructure.persistence.operational_allocation import OperationalAllocationService
from routeops.infrastructure.persistence.plan_comparisons import PlanComparisonService
from routeops.infrastructure.persistence.session import create_session_factory
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.vroom import VroomAdapter

pytestmark = pytest.mark.integration


def service(database, **options):
    return PlanComparisonService(
        create_session_factory(database),
        OsrmClient(OSRM_TEST_URL),
        VroomAdapter(VROOM_TEST_URL),
        WorkloadLimits(),
        map_dataset_sha256="a" * 64,
        **options,
    )


def stock_facts(database):
    # Includes complete operational rows, timestamps, external/RouteOps reservations and history.
    with database.connect() as conn:
        return {
            table: [dict(r) for r in conn.execute(text(f"SELECT * FROM {table}")).mappings()]
            for table in (
                "operational_inventory_positions",
                "operational_inventory_state",
                "allocation_attempts",
                "allocation_order_decisions",
                "allocation_reservation_lines",
                "run_order_reservations",
                "run_reservation_events",
                "planning_runs",
            )
        }


def manual_rows(rows):
    v = rows["vehicles"][0]
    return [
        {
            "vehicle_id": v["vehicle_id"],
            "center_id": v["distribution_center_id"],
            "order_ids": [o["order_id"] for o in rows["orders"]],
        }
    ]


@pytest.mark.parametrize("name", CASE_NAMES)
def test_real_b2b_b2c_manual_optimized_metrics_and_zero_operational_mutations(
    database, tmp_path, name
):
    rows = case_rows(name)
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database)
    before = stock_facts(database)
    pending, created = svc.submit(scenario, 1, "case", manual_rows(rows))
    assert created
    assert svc.process_once()
    value = svc.get(UUID(pending["comparison_id"]))
    assert value["status"] == "READY", value["history"]
    plans = value["result"]["alternatives"]
    assert len({p["inventory_initial_sha256"] for p in plans.values()}) == 1
    expected = {
        "b2b-feasible": 2,
        "b2b-diagnostics": 1,
        "b2c-feasible": 6,
        "b2c-task-pressure": 2,
        "b2c-distance-inferred": 0,
    }[name]
    for policy in ("greedy-v1", "alternatives-v2"):
        plan = plans[policy]
        assert len(plan["routed_order_ids"]) == expected
        assert plan["metrics"]["metrics"]["routed_orders"]["value"] == expected
        assert plan["provenance"]["context_sha256"] == value["context_sha256"]
        assert plan["departure_policy"] == "SOLVER_CHOSEN_WITHIN_EFFECTIVE_SHIFT"
        for route in plan["routes"]:
            assert route["departure_condition"]["departure_at"] == route["steps"][0]["departure_at"]
    assert plans["manual"]["departure_policy"] == "EFFECTIVE_SHIFT_START"
    assert plans["manual"]["feasible"] == name.endswith("feasible") and (
        name != "b2c-distance-inferred" or not plans["manual"]["feasible"]
    )
    assert plans["manual"]["solver_objective"] is None
    assert stock_facts(database) == before
    duplicate, created = svc.submit(scenario, 1, "case", manual_rows(rows))
    assert not created and duplicate == value
    assert not svc.process_once()


def test_shared_stock_policies_start_equal_and_manual_stock_violation_is_retained(
    database, tmp_path
):
    rows = demo_rows(allocation_demos()["shared_stock_restricted"])
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database)
    v = next(v for v in rows["vehicles"] if v["distribution_center_id"] == "CD-A")
    manual = [
        {
            "vehicle_id": v["vehicle_id"],
            "center_id": "CD-A",
            "order_ids": ["ORD-FLEX", "ORD-RESTRICTED"],
        }
    ]
    before = stock_facts(database)
    queued, _ = svc.submit(scenario, 1, "shared", manual)
    assert svc.process_once()
    value = svc.get(UUID(queued["comparison_id"]))
    assert value["status"] == "READY", value["history"]
    p = value["result"]["alternatives"]
    assert p["greedy-v1"]["routed_order_ids"] == ["ORD-FLEX"]
    assert p["alternatives-v2"]["routed_order_ids"] == ["ORD-FLEX", "ORD-RESTRICTED"]
    assert {d["order_id"]: d["center_id"] for d in p["alternatives-v2"]["decisions"]} == {
        "ORD-FLEX": "CD-B",
        "ORD-RESTRICTED": "CD-A",
    }
    assert not p["manual"]["feasible"]
    assert any(i["code"] == "MANUAL_STOCK_NO_FULL_COVERAGE" for i in p["manual"]["incidences"])
    difference = value["result"]["comparisons"]["greedy-v1__alternatives-v2"]
    assert difference["deltas"]["routed_orders"]["absolute"] == "1"
    assert not difference["savings_claim_allowed"]
    assert stock_facts(database) == before


def test_concurrent_submit_conflict_pagination_api_and_history_guards(
    database, tmp_path, monkeypatch
):
    rows = case_rows("b2b-feasible")
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database)
    barrier = Barrier(2)

    def submit():
        barrier.wait()
        return svc.submit(scenario, 1, "same", manual_rows(rows))

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sum(created for _, created in results) == 1
    comparison = UUID(results[0][0]["comparison_id"])
    assert all(v["comparison_id"] == str(comparison) for v, _ in results)
    with pytest.raises(ComparisonError, match="COMPARISON_IDEMPOTENCY_CONFLICT"):
        svc.submit(scenario, 1, "same", [])
    assert svc.process_once()
    value = svc.get(comparison)
    assert value["status"] == "READY", value["history"]
    with pytest.raises(DBAPIError), svc.sessions.begin() as s:
        s.execute(text("UPDATE plan_comparisons SET context_data='{}'"))
    with pytest.raises(DBAPIError), svc.sessions.begin() as s:
        s.execute(text("DELETE FROM comparison_results"))
    with pytest.raises(DBAPIError), svc.sessions.begin() as s:
        s.execute(text("UPDATE comparison_events SET reason='changed'"))
    with pytest.raises(RuntimeError, match="comparisons contain history"):
        command.downgrade(Config(str(ALEMBIC_INI)), "a71c9e3d602b")
    assert svc.get(comparison) == value
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "plan_comparisons", svc)
    with TestClient(api.app) as client:
        assert client.get(f"/api/v1/comparisons/{comparison}").json() == value
        assert "owner_token" not in client.get(f"/api/v1/comparisons/{comparison}").text
        assert client.get(f"/api/v1/scenarios/{scenario}/comparisons").json()["total"] == 1
        assert client.get(f"/api/v1/scenarios/{scenario}/comparisons?limit=0").status_code == 422
        path = f"/api/v1/scenarios/{scenario}/revisions/1/comparisons"
        assert client.post(path, json={"manual_routes": []}).status_code == 422
        assert (
            client.post(
                path, headers={"Idempotency-Key": "same"}, json={"manual_routes": manual_rows(rows)}
            ).json()
            == value
        )
        assert (
            client.post(
                path, headers={"Idempotency-Key": "same"}, json={"manual_routes": []}
            ).status_code
            == 409
        )
        assert (
            client.post(
                path,
                headers={"Idempotency-Key": "new"},
                json={"manual_routes": [], "historical_run_id": str(uuid4())},
            ).status_code
            == 422
        )


def test_recovery_fences_late_owner_atomic_outcome_and_lost_response(
    database, tmp_path, monkeypatch
):
    rows = case_rows("b2b-feasible")
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database, lease_seconds=1)
    queued, _ = svc.submit(scenario, 1, "recovery", manual_rows(rows))
    first = svc.claim()
    assert first
    time.sleep(1.05)
    second = svc.claim()
    assert second and first[0] == second[0] and first[1] != second[1]
    assert not svc.heartbeat(*first)
    svc.lease_seconds = 120
    assert svc.heartbeat(*second)
    result = svc.evaluate(second[0])
    assert not svc.finish(*first, result)
    assert not svc.fail(*first, "STALE")
    with pytest.raises(DBAPIError, match="comparison owner fenced"), svc.sessions.begin() as s:
        s.add(
            ComparisonResultModel(
                comparison_id=first[0],
                context_sha256=queued["context_sha256"],
                content_sha256=_hash(result),
                document=result,
                owner_token=first[1],
                attempt_no=1,
                created_at=datetime.now(UTC),
            )
        )
    original = svc._transition

    def broken(s, job, target, reason):
        if target == "READY":
            raise OSError("synthetic fault after output flush")
        return original(s, job, target, reason)

    monkeypatch.setattr(svc, "_transition", broken)
    with pytest.raises(OSError):
        svc.finish(*second, result)
    with svc.sessions() as s:
        assert s.get(ComparisonResultModel, second[0]) is None
    monkeypatch.setattr(svc, "_transition", original)
    assert svc.finish(*second, result)
    assert not svc.finish(*second, result)
    assert not svc.fail(*second, "RESPONSE_LOST")
    recovered, _ = svc.submit(scenario, 1, "recovery", manual_rows(rows))
    assert recovered["comparison_id"] == queued["comparison_id"]
    assert recovered["status"] == "READY" and recovered["attempts"] == 2
    assert len([e for e in recovered["history"] if e["to"] == "READY"]) == 1


def test_capture_keeps_operational_holds_across_a_new_revision_without_mutating_stock(
    database, tmp_path
):
    rows = case_rows("b2b-feasible")
    scenario, storage, services = _published_allocation_fixture(database, tmp_path, rows)
    allocation = OperationalAllocationService(
        create_session_factory(database), FixedAllocationTravel()
    )
    allocation.allocate(scenario, 1, "operational-hold")
    batch = _validated_publication_batch(scenario, "revision-two", rows, services, storage)
    services[2].publish(scenario, batch)
    svc = service(database)
    before = stock_facts(database)
    queued, _ = svc.submit(scenario, 2, "preserve", manual_rows(rows))
    assert sorted(p["routeops_reserved"] for p in queued["context"]["inventory"]) == [8, 12]
    assert all(p["externally_reserved"] == 5 for p in queued["context"]["inventory"])
    with pytest.raises(ComparisonError, match="REQUIRES_CURRENT_REVISION"):
        svc.submit(scenario, 1, "old", [])
    assert svc.process_once()
    assert svc.get(UUID(queued["comparison_id"]))["status"] == "READY"
    assert stock_facts(database) == before


def test_no_inventory_locks_during_external_calls_and_retry_uses_frozen_context(database, tmp_path):
    rows = case_rows("b2b-feasible")
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database, retry_seconds=0)
    queued, _ = svc.submit(scenario, 1, "outage", manual_rows(rows))
    before = stock_facts(database)
    original = svc.solver

    class ProbeSolver:
        calls = 0

        def solve(self, problem):
            with database.begin() as c:
                c.execute(text("SET LOCAL lock_timeout='100ms'"))
                c.execute(
                    text("SELECT id FROM scenarios WHERE id=:id FOR UPDATE NOWAIT"),
                    {"id": scenario},
                )
            self.calls += 1
            if self.calls == 1:
                from routeops.application.ports.errors import SolverDependencyError

                raise SolverDependencyError("synthetic dependency interruption")
            return original.solve(problem)

    svc.solver = ProbeSolver()
    assert svc.process_once()
    assert svc.get(UUID(queued["comparison_id"]))["status"] == "QUEUED"
    assert svc.process_once()
    final = svc.get(UUID(queued["comparison_id"]))
    assert final["status"] == "READY" and final["attempts"] == 2
    assert final["context_sha256"] == queued["context_sha256"]
    assert stock_facts(database) == before


def test_workload_rejected_before_context_and_empty_migration_preserves_postgis(monkeypatch):
    with temporary_database() as url:
        migrate(monkeypatch, url, "a71c9e3d602b")
        from sqlalchemy import create_engine

        engine = create_engine(url)
        try:
            with engine.connect() as c:
                oid = c.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'"))
            migrate(monkeypatch, url, "head")
            command.downgrade(Config(str(ALEMBIC_INI)), "a71c9e3d602b")
            with engine.connect() as c:
                assert c.scalar(text("SELECT oid FROM pg_extension WHERE extname='postgis'")) == oid
                assert (
                    c.scalar(text("SELECT to_regprocedure('routeops_guard_comparison_history()')"))
                    is None
                )
                assert c.scalar(text("SELECT to_regclass('planning_diagnostics')")) is not None
        finally:
            engine.dispose()


def test_workload_rejected_without_creating_comparison_or_reserves(database, tmp_path):
    rows = case_rows("b2b-feasible")
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database)
    svc.limits = replace(WorkloadLimits(), max_orders=1)
    before = stock_facts(database)
    with pytest.raises(Exception, match="SOLVER_WORKLOAD_LIMIT"):
        svc.submit(scenario, 1, "oversized", [])
    with svc.sessions() as s:
        assert list(s.scalars(select(PlanComparisonModel))) == []
    assert stock_facts(database) == before


def test_retry_limit_and_state_event_consistency_are_durable(database, tmp_path):
    rows = case_rows("b2b-feasible")
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database, max_attempts=1)
    submitted, _ = svc.submit(scenario, 1, "failure", manual_rows(rows))
    claimed = svc.claim()
    assert claimed
    assert svc.fail(*claimed, "SOLVER_DEPENDENCY_FAILED")
    value = svc.get(claimed[0])
    assert value["status"] == "FAILED" and value["result"] is None
    assert value["history"][-1]["reason"] == "SOLVER_DEPENDENCY_FAILED"
    assert not svc.process_once()
    again, _ = svc.submit(scenario, 1, "failure", manual_rows(rows))
    assert again == value and again["comparison_id"] == submitted["comparison_id"]
    with pytest.raises(DBAPIError), svc.sessions.begin() as s:
        job = s.get(ComparisonJobModel, claimed[0])
        job.status = "QUEUED"
    second, _ = svc.submit(scenario, 1, "event-rollback", [])
    before = svc.get(UUID(second["comparison_id"]))
    with pytest.raises(DBAPIError, match="event state mismatch"), svc.sessions.begin() as s:
        job = s.get(ComparisonJobModel, UUID(second["comparison_id"]))
        job.status, job.version, job.attempts = "RUNNING", 1, 1
        job.lease_token, job.lease_until = uuid4(), datetime.now(UTC) + timedelta(seconds=120)
        job.transitioned_at = datetime.now(UTC)
    assert svc.get(UUID(second["comparison_id"])) == before
