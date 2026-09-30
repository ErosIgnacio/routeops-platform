from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

from routeops.application.import_contract import DATASETS
from routeops.application.import_templates import csv_template, xlsx_template

pytestmark = pytest.mark.integration


def test_real_stack_routes_and_persists_demo() -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")

    with httpx.Client(base_url=base_url, timeout=60) as client:
        dependencies = client.get("/health/dependencies")
        dependencies.raise_for_status()
        assert dependencies.json()["status"] == "ready"

        response = client.post("/api/v1/demo/runs", json={"solution_quality": "BALANCED"})
        response.raise_for_status()
        run = response.json()

        persisted = client.get(f"/api/v1/runs/{run['run_id']}")
        persisted.raise_for_status()

    assert run["status"] in {"SUCCEEDED", "PARTIAL"}
    assert run["kpis"]["routes"] >= 1
    assert run["kpis"]["assigned_orders"] >= 1
    assert run["kpis"]["unassigned_orders"] >= 1
    assert sum(len(route["geometry"]) for route in run["result"]["routes"]) >= 2
    summary = run["result"]["summary"]
    kpis = run["kpis"]
    assert kpis["routes"] == summary["route_count"]
    assert kpis["vehicles_used"] == len({route["vehicle_id"] for route in run["result"]["routes"]})
    assert kpis["distance_km"] == round(summary["distance_meters"] / 1000, 3)
    assert kpis["driving_hours"] == round(summary["driving_seconds"] / 3600, 3)
    assert kpis["service_hours"] == round(summary["service_seconds"] / 3600, 3)
    assert kpis["waiting_hours"] == round(summary["waiting_seconds"] / 3600, 3)
    assert kpis["total_hours"] == round(summary["total_duration_seconds"] / 3600, 3)
    assert kpis["solver_time_ms"] == run["result"]["solver"]["solve_duration_ms"]
    assert persisted.json() == run


def test_real_stack_private_provisional_upload() -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Upload integration"})
        scenario.raise_for_status()
        scenario_id = scenario.json()["id"]
        url = f"/api/v1/scenarios/{scenario_id}/imports"
        files = [("files", (f"{name}.csv", csv_template(name), "text/csv")) for name in DATASETS]
        key = uuid4().hex
        first = client.post(url, files=files, headers={"Idempotency-Key": key})
        assert first.status_code == 201
        payload = first.json()
        assert payload["status"] == "RECEIVED"
        assert len(payload["files"]) == 5
        assert all("storage_key" not in file for file in payload["files"])
        read_back = client.get(f"{url}/{payload['id']}")
        assert read_back.status_code == 200
        assert read_back.json() == payload
        retry = client.post(url, files=files, headers={"Idempotency-Key": key})
        assert retry.status_code == 200
        assert retry.json() == payload
        conflict = client.post(
            url,
            files=[("files", ("book.xlsx", xlsx_template(), "application/octet-stream"))],
            headers={"Idempotency-Key": key},
        )
        assert conflict.status_code == 409
        workbook = client.post(
            url,
            files=[("files", ("book.xlsx", xlsx_template(), "application/octet-stream"))],
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert workbook.status_code == 201
        assert [file["dataset"] for file in workbook.json()["files"]] == ["workbook"]
