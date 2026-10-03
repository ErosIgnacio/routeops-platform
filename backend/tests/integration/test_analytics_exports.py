import importlib
import io
import json
import xml.etree.ElementTree as ET
from uuid import UUID
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from test_persistence_migration import _published_allocation_fixture
from test_persistence_migration import database as database
from test_plan_comparisons import manual_rows, service, stock_facts

from routeops.application.analytics_export import MAIN, scalar, tables
from routeops.application.operating_cost import OperatingCostError
from routeops.application.revision_problem import WorkloadLimits
from routeops.infrastructure.data.operation_cases import case_rows
from routeops.infrastructure.persistence.analytics_exports import AnalyticsExports
from routeops.infrastructure.persistence.operational_allocation import OperationalAllocationService
from routeops.infrastructure.persistence.revision_runs import RevisionRunService
from routeops.infrastructure.persistence.session import create_session_factory

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", ["b2b-feasible", "b2c-task-pressure"])
def test_persisted_comparison_exports_api_metrics_context_manual_violations_and_stock_unchanged(
    database, tmp_path, monkeypatch, name
):
    rows = case_rows(name)
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    svc = service(database)
    query = AnalyticsExports(create_session_factory(database), svc)
    pending, _ = svc.submit(scenario, 1, "export", manual_rows(rows))
    identity = UUID(pending["comparison_id"])
    with pytest.raises(OperatingCostError, match="EXPORT_NOT_READY"):
        query.export("comparison", identity, "xlsx")
    assert svc.process_once()
    facts = svc.get(identity)
    before = stock_facts(database)
    expected = tables(facts)
    payload, _, _ = query.export("comparison", identity, "xlsx")
    archive = ZipFile(io.BytesIO(payload))
    for index, values in enumerate(expected.values(), 1):
        sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
        actual = [
            [(c.find(f"{{{MAIN}}}is/{{{MAIN}}}t").text or "") for c in row]
            for row in sheet.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row")
        ]
        assert actual == [[scalar(v) for v in row] for row in values]
    assert facts["result"]["alternatives"]["manual"]["feasible"] == (name == "b2b-feasible")
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "analytics_exports", query)
    monkeypatch.setattr(api, "plan_comparisons", svc)
    with TestClient(api.app) as client:
        assert client.get(f"/api/v1/scenarios/{scenario}/revisions/1/comparison-input").json()[
            "orders"
        ] == sorted(o["order_id"] for o in rows["orders"])
        for format_name in ("csv", "xlsx"):
            response = client.get(f"/api/v1/comparisons/{identity}/exports/{format_name}")
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert str(identity) in response.headers["content-disposition"]
        assert client.get(f"/api/v1/comparisons/{identity}/exports/pdf").status_code == 422
    assert svc.get(identity) == facts
    assert stock_facts(database) == before
    assert "owner_token" not in json.dumps(facts)


@pytest.mark.parametrize("action", ["accept", "cancel"])
def test_operational_exports_preserve_routes_metrics_review_state_and_reservations(
    database, tmp_path, monkeypatch, action
):
    rows = case_rows("b2b-feasible")
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, rows)
    comparisons = service(database)
    sessions = create_session_factory(database)
    runs = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, comparisons.osrm),
        comparisons.osrm,
        comparisons.solver,
        WorkloadLimits(),
    )
    pending, _ = runs.submit(scenario, 1, "review")
    identity = UUID(pending["run_id"])
    assert runs.process_once()
    assert runs.get(identity)["status"] == "READY"
    query = AnalyticsExports(sessions, comparisons)
    before = query.run_document(identity)
    assert {r["status"] for r in before["metrics"]["current_reservations"]} == {"HELD"}
    getattr(runs, action)(identity)
    state = "CONFIRMED" if action == "accept" else "RELEASED"
    facts = stock_facts(database)
    after = query.run_document(identity)
    assert after["metrics"]["plan"] == before["metrics"]["plan"]
    assert after["run"]["result"] == before["run"]["result"]
    assert {r["status"] for r in after["metrics"]["current_reservations"]} == {state}
    for fmt in ("csv", "xlsx"):
        content, _, _ = query.export("run", identity, fmt)
        assert content
    api = importlib.import_module("routeops.api.main")
    monkeypatch.setattr(api, "analytics_exports", query)
    with TestClient(api.app) as client:
        assert client.get(f"/api/v1/runs/{identity}/analytics").json() == after
        assert client.get(f"/api/v1/runs/{identity}/exports/xlsx").status_code == 200
    assert stock_facts(database) == facts


def test_export_uses_one_repeatable_snapshot_during_concurrent_cancellation(
    database, tmp_path, monkeypatch
):
    scenario, _, _ = _published_allocation_fixture(database, tmp_path, case_rows("b2b-feasible"))
    comparisons = service(database)
    sessions = create_session_factory(database)
    runs = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, comparisons.osrm),
        comparisons.osrm,
        comparisons.solver,
        WorkloadLimits(),
    )
    pending, _ = runs.submit(scenario, 1, "snapshot")
    identity = UUID(pending["run_id"])
    assert runs.process_once() and runs.get(identity)["status"] == "READY"
    query = AnalyticsExports(sessions, comparisons)
    original = query.metrics.get

    def cancel_between_reads(run_id, **kw):
        value = original(run_id, **kw)
        runs.cancel(identity)
        return value

    monkeypatch.setattr(query.metrics, "get", cancel_between_reads)
    snapshot = query.run_document(identity)
    assert snapshot["run"]["status"] == snapshot["metrics"]["current_status"] == "READY"
    assert snapshot["diagnostics"]["current_status"] == "READY"
    assert {r["status"] for r in snapshot["metrics"]["current_reservations"]} == {"HELD"}
    assert runs.get(identity)["status"] == "CANCELED"
    monkeypatch.setattr(query.metrics, "get", original)
    later = query.run_document(identity)
    assert later["run"]["status"] == "CANCELED"
    assert {r["status"] for r in later["metrics"]["current_reservations"]} == {"RELEASED"}
