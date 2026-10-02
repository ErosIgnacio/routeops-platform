from decimal import Decimal

import pytest

from routeops.application.operating_cost import (
    OperatingCostError,
    VehicleRate,
    estimate_operating_cost,
)


def route(
    distance: int = 2500, driving: int = 1200, service: int = 600, waiting: int = 1800
) -> dict:
    return {
        "vehicle_id": "internal",
        "source_vehicle_id": "V1",
        "distribution_center_id": "CD-A",
        "totals": {
            "distance_meters": distance,
            "driving_seconds": driving,
            "service_seconds": service,
            "waiting_seconds": waiting,
            "total_duration_seconds": driving + service + waiting,
        },
    }


def test_cost_prices_wait_and_service_once_and_excludes_unused_vehicle() -> None:
    rates = {
        "V1": VehicleRate(Decimal("10"), Decimal("36"), Decimal("2")),
        "UNUSED": VehicleRate(Decimal("999"), Decimal("999"), Decimal("999")),
    }
    cost = estimate_operating_cost([route()], rates, "CLP")
    assert cost["total"] == "51.0000"  # 10 + 36*(1200+600+1800)/3600 + 2*2500/1000
    assert cost["routes"][0]["duty_cost"] == "36.0000"
    assert cost["routes"][0]["distance_cost"] == "5.0000"
    assert len(cost["routes"]) == 1
    assert cost["calculation_version"] == "operating-cost-v1"
    assert estimate_operating_cost([], rates, "CLP")["total"] == "0.0000"


def test_decimal_precision_rounds_each_component_once_without_binary_float() -> None:
    rates = {"V1": VehicleRate(Decimal("0.1234"), Decimal("0.1234"), Decimal("0.1234"))}
    cost = estimate_operating_cost([route(1, 1, 0, 0)], rates, "CLP")
    assert cost["routes"][0]["duty_cost"] == "0.0000"
    assert cost["routes"][0]["distance_cost"] == "0.0001"
    assert cost["total"] == "0.1235"
    half = {"V1": VehicleRate(Decimal(0), Decimal("0.1800"), Decimal("0.0500"))}
    assert estimate_operating_cost([route(1, 1, 0, 0)], half, "CLP")["total"] == "0.0002"


def test_zero_cost_and_maximum_representable_rates_and_facts() -> None:
    zero = VehicleRate(Decimal(0), Decimal(0), Decimal(0))
    assert estimate_operating_cost([route()], {"V1": zero}, "CLP")["total"] == "0.0000"
    maximum = Decimal("214748.3647")
    rates = {"V1": VehicleRate(maximum, maximum, maximum)}
    cost = estimate_operating_cost([route(2_147_483_647, 2_147_483_647, 0, 0)], rates, "CLP")
    assert cost["total"] == "589271205443.0629"


def test_original_demo_scale_retains_its_own_numeric_range() -> None:
    rate = VehicleRate(Decimal("21474836.47"), Decimal(0), Decimal(0), input_scale=100)
    assert estimate_operating_cost([route()], {"V1": rate}, "CLP")["total"] == "21474836.4700"
    with pytest.raises(OperatingCostError, match="COST_RATE_INVALID"):
        VehicleRate(Decimal("21474836.48"), Decimal(0), Decimal(0), input_scale=100)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "0.00001", "214748.3648", "1e999999"])
def test_invalid_or_unrepresentable_money_is_rejected(value: str) -> None:
    with pytest.raises(OperatingCostError, match="COST_RATE_INVALID"):
        VehicleRate(Decimal(value), Decimal(0), Decimal(0))


@pytest.mark.parametrize(
    "field,value",
    [
        ("distance_meters", 1.5),
        ("waiting_seconds", -1),
        ("total_duration_seconds", 1),
        ("distance_meters", 2_147_483_648),
    ],
)
def test_invalid_route_facts_are_not_priced(field: str, value: int | float) -> None:
    invalid = route()
    invalid["totals"][field] = value
    with pytest.raises(OperatingCostError, match="COST_ROUTE_FACTS_INVALID"):
        estimate_operating_cost(
            [invalid], {"V1": VehicleRate(Decimal(0), Decimal(0), Decimal(0))}, "CLP"
        )


def test_missing_rate_and_duplicate_vehicle_fail_explicitly() -> None:
    rates = {"V1": VehicleRate(Decimal(0), Decimal(0), Decimal(0))}
    with pytest.raises(OperatingCostError, match="COST_ROUTE_RATE_MISMATCH"):
        estimate_operating_cost([route()], {}, "CLP")
    with pytest.raises(OperatingCostError, match="COST_ROUTE_RATE_MISMATCH"):
        estimate_operating_cost([route(), route()], rates, "CLP")
