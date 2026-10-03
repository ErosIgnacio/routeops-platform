from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from test_vroom_adapter import problem, response_payload

from routeops.application.optimization_reconciliation import reconcile_result
from routeops.domain.models import Capacity
from routeops.domain.optimization.validation import validate_problem
from routeops.infrastructure.solver.errors import SolverInputError, SolverResponseError
from routeops.infrastructure.solver.vroom import VroomAdapter


@pytest.mark.parametrize(
    "field",
    [
        "max_route_distance_meters",
        "max_driving_seconds",
        "max_delivery_tasks",
    ],
)
@pytest.mark.parametrize("invalid", [0, -1, 2_147_483_648, 1.5, True])
def test_vehicle_limit_rejects_invalid_input(field: str, invalid: object) -> None:
    original = problem()
    candidate = replace(
        original,
        contract_version="1.1",
        vehicles=(replace(original.vehicles[0], **{field: invalid}),),
    )
    with pytest.raises(ValueError):
        validate_problem(candidate)


@respx.mock
def test_vehicle_limits_map_and_reconcile_inclusive_bounds() -> None:
    import json

    original = problem()
    vehicle = replace(
        original.vehicles[0],
        max_route_distance_meters=4000,
        max_driving_seconds=1200,
        max_delivery_tasks=1,
    )
    candidate = replace(original, contract_version="1.1", vehicles=(vehicle,))
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=response_payload())

    respx.post("http://vroom:3000").mock(side_effect=respond)
    result = VroomAdapter("http://vroom:3000").solve(candidate)
    sent = captured["vehicles"]
    assert isinstance(sent, list)
    assert {name: sent[0][name] for name in ("max_distance", "max_travel_time", "max_tasks")} == {
        "max_distance": 4000,
        "max_travel_time": 1200,
        "max_tasks": 1,
    }
    reconcile_result(
        replace(candidate, options=replace(candidate.options, request_geometry=False)), result
    )
    for field, value in (
        ("max_route_distance_meters", 3999),
        ("max_driving_seconds", 1199),
    ):
        too_low = replace(candidate, vehicles=(replace(vehicle, **{field: value}),))
        with pytest.raises(SolverResponseError, match="exceeded vehicle"):
            reconcile_result(
                replace(too_low, options=replace(too_low.options, request_geometry=False)),
                result,
            )
    extra_task = replace(
        candidate.tasks[0],
        task_id=candidate.scenario_id,
        order_id="ORD-2",
    )
    route = result.routes[0]
    extra_step = replace(route.steps[1], task_id=extra_task.task_id, order_id="ORD-2")
    extra_result = replace(
        result,
        routes=(replace(route, steps=(*route.steps[:2], extra_step, *route.steps[2:])),),
    )
    with pytest.raises(SolverResponseError, match="delivery tasks"):
        reconcile_result(
            replace(
                candidate,
                tasks=(*candidate.tasks, extra_task),
                options=replace(candidate.options, request_geometry=False),
            ),
            extra_result,
        )


@pytest.mark.parametrize(
    "case",
    [
        "priority",
        "fractional_instant",
        "naive_instant",
        "window",
        "open_route",
        "duplicate_order",
        "duplicate_vehicle",
        "scale",
        "rate_overflow",
        "capacity_overflow",
        "mixed_currency",
        "mixed_scale",
    ],
)
def test_problem_contract_rejects_lossy_or_inconsistent_inputs(case: str) -> None:
    value = problem()
    task, vehicle = value.tasks[0], value.vehicles[0]
    match case:
        case "priority":
            value = replace(value, tasks=(replace(task, priority=101),))
        case "fractional_instant":
            value = replace(
                value,
                tasks=(
                    replace(task, time_window_start=task.time_window_start.replace(microsecond=1)),
                ),
            )
        case "naive_instant":
            value = replace(value, horizon_start=value.horizon_start.replace(tzinfo=None))
        case "window":
            value = replace(
                value,
                tasks=(replace(task, time_window_end=value.horizon_end + timedelta(seconds=1)),),
            )
        case "open_route":
            value = replace(value, vehicles=(replace(vehicle, end=task.location),))
        case "duplicate_order":
            value = replace(value, tasks=(task, task))
        case "duplicate_vehicle":
            value = replace(value, vehicles=(vehicle, vehicle))
        case "scale":
            value = replace(
                value, vehicles=(replace(vehicle, costs=replace(vehicle.costs, scale=0)),)
            )
        case "rate_overflow":
            value = replace(
                value,
                vehicles=(
                    replace(vehicle, costs=replace(vehicle.costs, per_km_units=2_147_483_648)),
                ),
            )
        case "capacity_overflow":
            value = replace(
                value, vehicles=(replace(vehicle, capacity=Capacity(2_147_483_648, 1, 1)),)
            )
        case "mixed_currency" | "mixed_scale":
            other = replace(
                vehicle,
                vehicle_id=value.scenario_id,
                source_vehicle_id="OTHER",
                costs=replace(
                    vehicle.costs,
                    currency="USD" if case == "mixed_currency" else "CLP",
                    scale=101 if case == "mixed_scale" else 100,
                ),
            )
            value = replace(value, vehicles=(vehicle, other))
    with pytest.raises(ValueError):
        validate_problem(value)
    with pytest.raises(SolverInputError):
        VroomAdapter("http://unused")._to_vroom(value)


def test_center_skills_cannot_collide_with_user_skills() -> None:
    value = problem()
    task, vehicle = value.tasks[0], value.vehicles[0]
    other = replace(
        vehicle,
        vehicle_id=value.scenario_id,
        source_vehicle_id="OTHER",
        distribution_center_id="DC-B",
        skills=frozenset({"fragile", "__routeops_center__:DC-A"}),
    )
    payload, _, _ = VroomAdapter("http://unused")._to_vroom(
        replace(value, vehicles=(vehicle, other))
    )
    required = set(payload["jobs"][0]["skills"])
    assert required <= set(
        next(v for v in payload["vehicles"] if v["description"] == "VEH-1")["skills"]
    )
    assert not required <= set(
        next(v for v in payload["vehicles"] if v["description"] == "OTHER")["skills"]
    )
    assert task.distribution_center_id == "DC-A"


@pytest.mark.parametrize(
    "case",
    [
        "load",
        "capacity",
        "capacity_weight",
        "capacity_volume",
        "skills",
        "arrival",
        "service",
        "window",
        "distance",
        "duration",
        "route_total",
        "missing_cost",
        "missing_distance",
        "summary",
        "fractional_metric",
        "string_metric",
        "boolean_metric",
        "coordinates",
        "center",
        "geometry",
        "corrupt_polyline",
        "break",
        "setup",
        "violations",
        "unknown_job",
        "missing_task",
        "extra_route",
        "shift",
        "window_status",
    ],
)
@respx.mock
def test_malformed_response_cannot_become_persisted_route_facts(case: str) -> None:
    payload = response_payload()
    value = problem()
    route: dict[str, Any] = payload["routes"][0]
    job = route["steps"][1]
    match case:
        case "load":
            job["load"] = [1, 0, 0]
        case "capacity":
            value = replace(
                value, vehicles=(replace(value.vehicles[0], capacity=Capacity(1, 2000, 4000)),)
            )
        case "capacity_weight":
            value = replace(
                value,
                vehicles=(
                    replace(
                        value.vehicles[0],
                        capacity=Capacity(20, 1999, 4000),
                    ),
                ),
            )
        case "capacity_volume":
            value = replace(
                value,
                vehicles=(
                    replace(
                        value.vehicles[0],
                        capacity=Capacity(20, 2000, 3999),
                    ),
                ),
            )
        case "skills":
            value = replace(value, vehicles=(replace(value.vehicles[0], skills=frozenset()),))
        case "arrival":
            job["arrival"] = 3499
        case "service":
            job["service"] = 601
        case "window":
            job["waiting_time"] = 0
        case "distance":
            route["steps"][-1]["distance"] = 1999
        case "duration":
            route["steps"][-1]["duration"] = 599
        case "route_total":
            route["service"] = 599
            payload["summary"]["service"] = 599
        case "missing_cost":
            del route["cost"]
        case "missing_distance":
            del route["distance"]
        case "summary":
            payload["summary"]["cost"] = 1235
        case "fractional_metric":
            job["duration"] = 600.9
        case "string_metric":
            job["duration"] = "600"
        case "boolean_metric":
            job["duration"] = True
        case "coordinates":
            job["location"] = [181, 0]
        case "center":
            route["steps"][0]["location"] = [-70, -33]
        case "geometry":
            route["geometry"] = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        case "corrupt_polyline":
            route["geometry"] = "~"
        case "break":
            job["type"] = "break"
        case "setup":
            job["setup"] = 1
        case "violations":
            route["violations"] = [{"cause": "load"}]
        case "unknown_job":
            job["id"] = 9
        case "missing_task":
            route["steps"].pop(1)
        case "extra_route":
            payload["routes"].append(route.copy())
        case "shift":
            value = replace(
                value,
                vehicles=(
                    replace(
                        value.vehicles[0], shift_start=value.horizon_start + timedelta(seconds=3000)
                    ),
                ),
            )
        case "window_status":
            job["waiting_time"] = 100_000
    respx.post("http://vroom:3000").mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(SolverResponseError):
        VroomAdapter("http://vroom:3000").solve(value)


@respx.mock
def test_failed_status_is_not_accepted_as_a_successful_solution() -> None:
    from routeops.application.optimization_reconciliation import reconcile_result
    from routeops.domain.optimization import ResultStatus

    respx.post("http://vroom:3000").mock(return_value=httpx.Response(200, json=response_payload()))
    value = problem()
    result = VroomAdapter("http://vroom:3000").solve(value)
    with pytest.raises(SolverResponseError, match="status contradicts"):
        reconcile_result(value, replace(result, status=ResultStatus.FAILED))
