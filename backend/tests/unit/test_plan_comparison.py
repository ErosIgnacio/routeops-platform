from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import respx
from test_vroom_adapter import problem

from routeops.application.plan_comparison import differences, evaluate_manual
from routeops.application.ports.errors import RoutingCoverageError, RoutingDependencyError
from routeops.application.ports.fixed_route import FixedRoadRoute
from routeops.application.revision_problem import PreparedRevision
from routeops.domain.models import Coordinate, DistributionCenter, Order, OrderLine
from routeops.domain.policies.operational_allocation import OperationalStock
from routeops.infrastructure.routing.osrm import OsrmClient


def prepared() -> PreparedRevision:
    p = problem()
    task = p.tasks[0]
    order = Order(
        task.order_id,
        "synthetic",
        task.location,
        task.priority,
        task.time_window_start,
        task.time_window_end,
        task.service_seconds,
        task.required_skills,
        (OrderLine("SKU", 2, Decimal("1"), Decimal("0.002")),),
    )
    return PreparedRevision(
        uuid4(),
        p.scenario_id,
        uuid4(),
        "Synthetic",
        "a" * 64,
        "b" * 64,
        p.timezone,
        "CLP",
        p.horizon_start.date(),
        p.horizon_start,
        p.horizon_end,
        (DistributionCenter("DC-A", "A", p.vehicles[0].start),),
        (order,),
        p.vehicles,
        1,
    )


class Roads:
    def __init__(self) -> None:
        self.points = ()

    def route_sequence(self, points, maximum):
        self.points = points
        return FixedRoadRoute((100,) * (len(points) - 1), (1000,) * (len(points) - 1), points, ())


def evaluate(p=None, routes=None, stock=None):
    p = p or prepared()
    return evaluate_manual(
        p,
        routes
        if routes is not None
        else [{"vehicle_id": "VEH-1", "center_id": "DC-A", "order_ids": ["ORD-1"]}],
        stock or (OperationalStock("DC-A", "SKU", 20, 2, 1, 0),),
        Roads(),
        {},
        250,
    )


def test_manual_chronology_prices_wait_and_keeps_load_until_service_finishes():
    value = evaluate()
    assert value["feasible"]
    route = value["routes"][0]
    assert [s["kind"] for s in route["steps"]] == ["START", "DELIVERY", "END"]
    assert route["steps"][1]["waiting_seconds"] == 3500
    assert route["steps"][0]["load_after"]["units"] == 2
    assert route["steps"][1]["load_after"]["units"] == 0
    assert route["totals"]["total_duration_seconds"] == 4300
    assert route["totals"]["driving_seconds"] == 200
    assert route["totals"]["service_seconds"] == 600
    assert route["departure_condition"]["scope"] == "manual_plan_condition"
    assert route["departure_condition"]["departure_at"] == route["steps"][0]["departure_at"]
    assert value["evaluated_input"][0]["departure_at"] == route["steps"][0]["departure_at"]
    later = deepcopy(value)
    later["departure_policy"] = "SOLVER_CHOSEN_WITHIN_EFFECTIVE_SHIFT"
    delta = differences(value, later)
    assert delta["temporal_scope"]["baseline_departure_policy"] == "EFFECTIVE_SHIFT_START"
    assert "not sequence-only" in delta["temporal_scope"]["attribution"]
    assert value["solver_objective"] is None
    # Fixed 10 + duty 5 * 4300/3600 + distance 1 * 2 km.
    assert value["metrics"]["operating_cost"]["total"] == "17.9722"


def test_manual_sequence_is_not_reordered_even_if_the_later_window_becomes_infeasible():
    p = prepared()
    a = replace(p.orders[0], id="A", time_window_start=p.horizon_start + timedelta(hours=2))
    b = replace(p.orders[0], id="B", time_window_end=p.horizon_start + timedelta(hours=1))
    p = replace(p, orders=(a, b))
    roads = Roads()
    result = evaluate_manual(
        p,
        [{"vehicle_id": "VEH-1", "center_id": "DC-A", "order_ids": ["A", "B"]}],
        (OperationalStock("DC-A", "SKU", 20, 0, 0, 0),),
        roads,
        {},
        250,
    )
    assert [s["order_id"] for s in result["routes"][0]["steps"][1:-1]] == ["A", "B"]
    assert not result["feasible"] and result["metrics_informative_only"]
    assert any(i["code"] == "MANUAL_WINDOW" for i in result["incidences"])


@pytest.mark.parametrize(
    "change,code",
    [
        ({"order_ids": ["ORD-1", "ORD-1"]}, "MANUAL_ORDER_DUPLICATE"),
        ({"order_ids": ["OTHER"]}, "MANUAL_ORDER_UNKNOWN"),
        ({"vehicle_id": "OTHER"}, "MANUAL_VEHICLE_UNKNOWN"),
        ({"center_id": "OTHER"}, "MANUAL_CENTER_UNKNOWN"),
        ({"order_ids": []}, "MANUAL_EMPTY_ROUTE"),
    ],
)
def test_invalid_manual_identities_are_retained_and_not_repaired(change, code):
    route = {"vehicle_id": "VEH-1", "center_id": "DC-A", "order_ids": ["ORD-1"], **change}
    result = evaluate(routes=[route])
    assert not result["feasible"] and not result["routes"]
    assert any(
        i["code"] == code and i["evidence"]["submitted_route"] == route
        for i in result["incidences"]
    )


def test_omissions_are_explicit_partial_coverage_not_infeasible():
    result = evaluate(routes=[])
    assert result["feasible"]
    assert result["incidences"][0]["code"] == "MANUAL_ORDER_NOT_SERVED"
    assert result["metrics"]["metrics"]["coverage"]["value"] == "0"


@pytest.mark.parametrize("dimension", ["units", "weight_grams", "volume_cm3"])
def test_manual_individual_capacity_is_checked_in_each_dimension(dimension):
    p = prepared()
    v = replace(p.vehicles[0], capacity=replace(p.vehicles[0].capacity, **{dimension: 1}))
    result = evaluate(replace(p, vehicles=(v,)))
    assert not result["feasible"]
    assert any(i["code"] == "MANUAL_CAPACITY" for i in result["incidences"])


def test_manual_skills_stock_shift_and_limit_are_not_hidden_by_informative_metrics():
    p = prepared()
    v = replace(
        p.vehicles[0],
        skills=frozenset(),
        shift_end=p.horizon_start + timedelta(hours=1),
        max_route_distance_meters=1999,
        max_driving_seconds=199,
        max_delivery_tasks=1,
    )
    result = evaluate(
        replace(p, vehicles=(v,)), stock=(OperationalStock("DC-A", "SKU", 4, 1, 1, 1),)
    )
    codes = {i["code"] for i in result["incidences"]}
    assert {
        "MANUAL_SKILLS",
        "MANUAL_STOCK_NO_FULL_COVERAGE",
        "MANUAL_EFFECTIVE_SHIFT",
        "MANUAL_DISTANCE_LIMIT",
        "MANUAL_DRIVING_LIMIT",
    } <= codes
    assert not result["feasible"]


def test_manual_exact_distance_driving_and_tasks_limits_are_allowed():
    p = prepared()
    v = replace(
        p.vehicles[0], max_route_distance_meters=2000, max_driving_seconds=200, max_delivery_tasks=1
    )
    assert evaluate(replace(p, vehicles=(v,)))["feasible"]


def test_manual_shared_stock_is_consumed_logically_across_routes_without_mutating_source():
    p = prepared()
    second = replace(p.orders[0], id="ORD-2")
    fleet = (*p.vehicles, replace(p.vehicles[0], source_vehicle_id="VEH-2", vehicle_id=uuid4()))
    frozen = (OperationalStock("DC-A", "SKU", 5, 1, 0, 1),)
    p = replace(p, orders=(*p.orders, second), vehicles=fleet)
    value = evaluate(
        p,
        routes=[
            {"vehicle_id": "VEH-1", "center_id": "DC-A", "order_ids": ["ORD-1"]},
            {"vehicle_id": "VEH-2", "center_id": "DC-A", "order_ids": ["ORD-2"]},
        ],
        stock=frozen,
    )
    assert not value["feasible"]
    issue = next(i for i in value["incidences"] if i["code"] == "MANUAL_STOCK_NO_FULL_COVERAGE")
    assert issue["evidence"]["available_before"] == {"SKU": 1}
    assert frozen[0].available == 3 and frozen[0].routeops_reserved == 1


def test_manual_declared_center_must_match_vehicle_home_center():
    p = prepared()
    p = replace(p, centers=(*p.centers, DistributionCenter("DC-B", "B", p.centers[0].location)))
    value = evaluate(
        p, routes=[{"vehicle_id": "VEH-1", "center_id": "DC-B", "order_ids": ["ORD-1"]}]
    )
    assert not value["feasible"]
    assert any(i["code"] == "MANUAL_VEHICLE_CENTER_MISMATCH" for i in value["incidences"])
    assert value["routes"][0]["distribution_center_id"] == "DC-B"  # never silently repaired


def test_manual_service_start_window_boundary_and_one_second_excess():
    p = prepared()
    o = replace(
        p.orders[0],
        time_window_start=p.horizon_start,
        time_window_end=p.horizon_start + timedelta(seconds=100),
    )
    assert evaluate(replace(p, orders=(o,)))["feasible"]
    value = evaluate(
        replace(p, orders=(replace(o, time_window_end=p.horizon_start + timedelta(seconds=99)),))
    )
    assert not value["feasible"]
    assert any(i["code"] == "MANUAL_WINDOW" for i in value["incidences"])


def test_deltas_do_not_claim_savings_for_lower_coverage_or_invalid_manual():
    baseline = evaluate()
    candidate = evaluate(routes=[])
    result = differences(baseline, candidate)
    assert result["deltas"]["distance_meters"]["absolute"] == "-2000"
    assert result["deltas"]["distance_meters"]["percentage"] == "-100"
    assert result["comparability"] == "DIFFERENT_COVERAGE" and not result["savings_claim_allowed"]
    assert differences(candidate, baseline)["deltas"]["distance_meters"]["percentage"] is None
    invalid = deepcopy(baseline)
    invalid["feasible"] = False
    assert differences(invalid, baseline)["comparability"] == "INFEASIBLE_PLAN"
    missing = deepcopy(baseline)
    missing["metrics"]["metrics"]["operating_cost"]["value"] = None
    assert differences(missing, baseline)["deltas"]["operating_cost"]["percentage"] is None


@respx.mock
@pytest.mark.parametrize("bad", [None, "snap", "nan", "unreachable"])
def test_fixed_osrm_route_enforces_order_snaps_and_finite_leg_metrics(bad):
    points = (Coordinate(-33.45, -70.66), Coordinate(-33.44, -70.65), Coordinate(-33.45, -70.66))
    payload = {
        "code": "Ok",
        "waypoints": [
            {"distance": 300 if bad == "snap" else 1, "location": [p.longitude, p.latitude]}
            for p in points
        ],
        "routes": [
            {
                "legs": [{"distance": 1000, "duration": 100}] * 2,
                "geometry": {"coordinates": [[p.longitude, p.latitude] for p in points]},
            }
        ],
    }
    if bad == "nan":
        payload["routes"][0]["legs"][0]["duration"] = -1
    if bad == "unreachable":
        payload = {"code": "NoRoute"}
    respx.get(url__startswith="http://osrm/route/v1/driving/").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OsrmClient("http://osrm")
    if bad:
        with pytest.raises(RoutingDependencyError if bad == "nan" else RoutingCoverageError):
            client.route_sequence(points, 250)
    else:
        result = client.route_sequence(points, 250)
        assert result.durations == (100, 100) and result.geometry == points
