from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_real_stack_routes_and_persists_demo() -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")

    with httpx.Client(base_url=base_url, timeout=60) as client:
        dependencies = client.get("/health/dependencies")
        dependencies.raise_for_status()
        assert dependencies.json()["status"] == "ready"

        response = client.post(
            "/api/v1/demo/runs", json={"solution_quality": "BALANCED"}
        )
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
    assert kpis["vehicles_used"] == len(
        {route["vehicle_id"] for route in run["result"]["routes"]}
    )
    assert kpis["distance_km"] == round(summary["distance_meters"] / 1000, 3)
    assert kpis["driving_hours"] == round(summary["driving_seconds"] / 3600, 3)
    assert kpis["service_hours"] == round(summary["service_seconds"] / 3600, 3)
    assert kpis["waiting_hours"] == round(summary["waiting_seconds"] / 3600, 3)
    assert kpis["total_hours"] == round(summary["total_duration_seconds"] / 3600, 3)
    assert kpis["solver_time_ms"] == run["result"]["solver"]["solve_duration_ms"]
    assert persisted.json() == run
