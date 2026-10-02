from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from routeops.application.operating_cost import VehicleRate, estimate_operating_cost
from routeops.application.plan_metrics import plan_metrics, utilization

START = datetime(2026, 10, 15, 12, tzinfo=UTC)
CAPACITY = {"units": 10, "weight_grams": 1000, "volume_cm3": 2000}


def route(key: str = "A", factor: int = 1, ids: tuple[str, str] = ("O1", "O2")) -> dict[str, Any]:
    steps = []
    instant = START
    for kind, order, travel, wait, service, load in (
        ("START", None, 0, 0, 0, (8, 500, 400)),
        ("DELIVERY", ids[0], 600, 300, 300, (4, 100, 200)),
        ("DELIVERY", ids[1], 600, 0, 300, (0, 0, 0)),
        ("END", None, 300, 0, 0, (0, 0, 0)),
    ):
        arrival = instant + timedelta(seconds=travel // factor)
        service_start = arrival + timedelta(seconds=wait // factor)
        instant = service_start + timedelta(seconds=service // factor)
        steps.append(
            {
                "kind": kind,
                "order_id": order,
                "service_start_at": service_start.isoformat(),
                "travel_seconds_from_previous": travel // factor,
                "waiting_seconds": wait // factor,
                "service_seconds": service // factor,
                "load_after": dict(zip(CAPACITY, load, strict=True)),
                "location": {"latitude": -33.45, "longitude": -70.66},
            }
        )
    return {
        "vehicle_id": key,
        "source_vehicle_id": key,
        "distribution_center_id": "CD",
        "steps": steps,
        "totals": {
            "distance_meters": 12000,
            "driving_seconds": 1500 // factor,
            "service_seconds": 600 // factor,
            "waiting_seconds": 300 // factor,
            "total_duration_seconds": 2400 // factor,
            "objective_cost_units": 42,
        },
    }


def test_duration_weighted_utilization_keeps_load_through_service_in_each_dimension() -> None:
    values = utilization(route(), CAPACITY)
    assert values["units"]["maximum"]["value"] == "0.8"
    assert values["units"]["time_weighted_average"]["value"] == "0.55"
    assert values["units"]["time_weighted_average"]["denominator"] == 24000
    assert values["weight_grams"]["maximum"]["value"] == "0.5"
    assert values["weight_grams"]["time_weighted_average"]["value"] == "0.2875"
    assert values["volume_cm3"]["maximum"]["value"] == "0.2"
    assert values["volume_cm3"]["time_weighted_average"]["value"] == "0.1375"


@pytest.mark.parametrize(
    "capacities,duty", [(None, 2400), (CAPACITY | {"units": 0}, 2400), (CAPACITY, 0)]
)
def test_missing_or_zero_denominators_are_null(
    capacities: dict[str, int] | None, duty: int
) -> None:
    value = route()
    value["totals"]["total_duration_seconds"] = duty
    assert utilization(value, capacities)["units"]["time_weighted_average"]["value"] is None


def test_multiroute_partial_coverage_coincident_orders_costs_windows_and_population_balance() -> (
    None
):
    routes = [route(), route("B", 2, ("O3", "O4"))]
    windows = {f"O{i}": (START, START + timedelta(hours=1)) for i in range(1, 6)}
    windows["O1"] = (START + timedelta(seconds=900), START + timedelta(seconds=1000))
    costs = estimate_operating_cost(
        routes,
        {key: VehicleRate(Decimal(1), Decimal(3600), Decimal(2)) for key in ("A", "B")},
        "CLP",
    )
    costs["solver_objective"] = {"units": 84, "scale": 100}
    value = plan_metrics(
        routes,
        set(windows),
        {"O1", "O2", "O3", "O4"},
        {"A": CAPACITY, "B": {key: cap * 2 for key, cap in CAPACITY.items()}},
        windows,
        costs,
    )
    metrics = value["metrics"]
    assert metrics["valid_input_orders"]["value"] == 5
    assert metrics["allocated_orders"]["value"] == metrics["routed_orders"]["value"] == 4
    assert metrics["unrouted_orders"]["value"] == 1
    assert metrics["coverage"]["value"] == "0.8"
    assert metrics["km_per_routed_order"]["value"] == "6"
    assert metrics["driving_seconds"]["value"] == 2250
    assert metrics["service_seconds"]["value"] == 900
    assert metrics["waiting_seconds"]["value"] == 450
    assert metrics["total_duration_seconds"]["value"] == 3600
    assert metrics["operating_cost"]["value"] == "3650.0000"
    assert metrics["operating_cost_per_routed_order"]["value"] == "912.5000"
    assert metrics["window_compliance"]["value"] == "1"
    assert metrics["window_compliance"]["denominator"] == 4
    assert value["duty_balance"]["minimum"]["value"] == "1200"
    assert value["duty_balance"]["maximum"]["value"] == "2400"
    assert value["duty_balance"]["mean"]["value"] == "1800"
    assert value["duty_balance"]["population_stddev"]["value"] == "600"
    assert Decimal(value["duty_balance"]["coefficient_of_variation"]["value"]) * 3 == Decimal(1)
    assert value["routes"][1]["utilization"]["units"]["maximum"]["value"] == "0.4"
    assert value["routes"][1]["utilization"]["units"]["time_weighted_average"]["value"] == "0.275"
    assert value["solver_objective"]["units"] == 84
    assert value["scope"] == "computed_plan_not_execution"


@pytest.mark.parametrize("inputs", [set(), {"O1"}, None])
def test_no_routes_verified_zero_is_distinct_from_missing_data(inputs: set[str] | None) -> None:
    cost = estimate_operating_cost([], {}, "CLP") | {"solver_objective": {"units": 0}}
    value = plan_metrics([], inputs, set(), {}, {}, cost)
    assert value["metrics"]["routed_orders"]["value"] == 0
    assert value["metrics"]["operating_cost"]["value"] == "0.0000"
    assert value["metrics"]["km_per_routed_order"]["value"] is None
    assert value["metrics"]["coverage"]["value"] == ("0" if inputs else None)
    assert value["duty_balance"]["coefficient_of_variation"]["value"] is None


def test_one_route_window_service_start_and_historical_unknowns_do_not_mutate_facts() -> None:
    data = route()
    original = deepcopy(data)
    value = plan_metrics([data], None, None, {}, {}, None, "COST_RATES_NOT_RECORDED")
    assert value["metrics"]["valid_input_orders"]["value"] is None
    assert value["metrics"]["operating_cost"]["value"] is None
    assert value["metrics"]["window_compliance"]["value"] is None
    assert value["duty_balance"]["population_stddev"]["value"] == "0"
    assert value["duty_balance"]["coefficient_of_variation"]["value"] is None
    assert data == original
    windows = {
        "O1": (START, START + timedelta(seconds=899)),
        "O2": (START, START + timedelta(seconds=1800)),
    }
    checked = plan_metrics([data], set(windows), set(windows), {"A": CAPACITY}, windows, None)
    assert checked["metrics"]["window_compliance"]["value"] == "0.5"
