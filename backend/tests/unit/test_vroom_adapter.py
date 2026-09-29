from __future__ import annotations

from datetime import datetime
from uuid import UUID

import httpx
import respx

from routeops.domain.models import Capacity, Coordinate
from routeops.domain.optimization import (
    DeliveryTask,
    OptimizationOptions,
    OptimizationProblem,
    OptimizationVehicle,
    VehicleCost,
)
from routeops.infrastructure.solver import VroomAdapter


def problem() -> OptimizationProblem:
    start = datetime.fromisoformat("2026-10-15T08:00:00-03:00")
    end = datetime.fromisoformat("2026-10-15T18:00:00-03:00")
    task = DeliveryTask(
        task_id=UUID("00000000-0000-0000-0000-000000000010"),
        order_id="ORD-1",
        distribution_center_id="DC-A",
        location=Coordinate(-33.44, -70.65),
        demand=Capacity(2, 2000, 4000),
        service_seconds=600,
        time_window_start=datetime.fromisoformat("2026-10-15T09:00:00-03:00"),
        time_window_end=datetime.fromisoformat("2026-10-15T12:00:00-03:00"),
        priority=80,
        required_skills=frozenset({"fragile"}),
    )
    vehicle = OptimizationVehicle(
        vehicle_id=UUID("00000000-0000-0000-0000-000000000020"),
        source_vehicle_id="VEH-1",
        distribution_center_id="DC-A",
        vehicle_type="van",
        start=Coordinate(-33.45, -70.66),
        end=Coordinate(-33.45, -70.66),
        shift_start=start,
        shift_end=end,
        capacity=Capacity(20, 20_000, 100_000),
        skills=frozenset({"fragile"}),
        costs=VehicleCost("CLP", 100, 1000, 500, 100),
    )
    return OptimizationProblem(
        contract_version="1.0",
        problem_id=UUID("00000000-0000-0000-0000-000000000001"),
        scenario_id=UUID("00000000-0000-0000-0000-000000000002"),
        horizon_start=start,
        horizon_end=end,
        timezone="America/Santiago",
        tasks=(task,),
        vehicles=(vehicle,),
        options=OptimizationOptions(),
    )


@respx.mock
def test_health_proves_vroom_can_reach_osrm() -> None:
    respx.get("http://vroom:3000/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    routing_probe = respx.post("http://vroom:3000").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "routes": [{"vehicle": 1, "steps": []}],
                "unassigned": [],
            },
        )
    )

    status = VroomAdapter("http://vroom:3000").health()

    assert routing_probe.called
    assert status == {
        "status": "ready",
        "version": "1.15.0",
        "routing_engine": "osrm",
    }


@respx.mock
def test_maps_and_reconciles_vroom_response() -> None:
    captured: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "routes": [
                    {
                        "vehicle": 1,
                        "cost": 1234,
                        "distance": 4000,
                        "duration": 1200,
                        "service": 600,
                        "waiting_time": 0,
                        "steps": [
                            {
                                "type": "start",
                                "location": [-70.66, -33.45],
                                "arrival": 0,
                                "duration": 0,
                                "distance": 0,
                                "load": [2, 2000, 4000],
                            },
                            {
                                "type": "job",
                                "job": 1,
                                "location": [-70.65, -33.44],
                                "arrival": 900,
                                "duration": 600,
                                "distance": 2000,
                                "service": 600,
                                "waiting_time": 0,
                                "load": [0, 0, 0],
                            },
                            {
                                "type": "end",
                                "location": [-70.66, -33.45],
                                "arrival": 1800,
                                "duration": 1200,
                                "distance": 4000,
                                "load": [0, 0, 0],
                            },
                        ],
                    }
                ],
                "unassigned": [],
            },
        )

    respx.post(url__regex=r"http://vroom:3000/.*").mock(side_effect=responder)

    result = VroomAdapter("http://vroom:3000").solve(problem())

    assert captured["jobs"][0]["delivery"] == [2, 2000, 4000]  # type: ignore[index]
    assert captured["jobs"][0]["time_windows"] == [[3600, 14400]]  # type: ignore[index]
    assert captured["vehicles"][0]["end"] == [-70.66, -33.45]  # type: ignore[index]
    assert result.summary.assigned_task_count == 1
    assert result.summary.distance_meters == 4000
    assert result.routes[0].steps[1].order_id == "ORD-1"
