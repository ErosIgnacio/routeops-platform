from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from routeops.domain.models import (
    Capacity,
    Coordinate,
    DistributionCenter,
    InventoryPosition,
    Order,
    OrderLine,
    ScenarioData,
    Vehicle,
)
from routeops.domain.policies.allocation import DeterministicAllocationPolicy


class FixedTravelTimes:
    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int:
        return 100 if origin.longitude == -70.6 else 200


def instant(hour: int) -> datetime:
    return datetime.fromisoformat(f"2026-10-15T{hour:02d}:00:00-03:00")


def make_order(order_id: str, sku: str, quantity: int, priority: int = 50) -> Order:
    return Order(
        id=order_id,
        customer_reference=f"SYNTH-{order_id}",
        location=Coordinate(-33.44, -70.65),
        priority=priority,
        time_window_start=instant(9),
        time_window_end=instant(14),
        service_seconds=300,
        required_skills=frozenset(),
        lines=(OrderLine(sku, quantity, Decimal("1"), Decimal("0.001")),),
    )


def make_scenario(orders: tuple[Order, ...]) -> ScenarioData:
    centers = (
        DistributionCenter("DC-A", "A", Coordinate(-33.44, -70.7)),
        DistributionCenter("DC-B", "B", Coordinate(-33.43, -70.6)),
    )
    return ScenarioData(
        name="test",
        timezone="America/Santiago",
        currency="CLP",
        horizon_start=instant(8),
        horizon_end=instant(18),
        centers=centers,
        inventory=(
            InventoryPosition("DC-A", "SKU-A", 20, 0, 0),
            InventoryPosition("DC-B", "SKU-A", 20, 0, 0),
        ),
        vehicles=tuple(
            Vehicle(
                id=f"VEH-{center.id}",
                distribution_center_id=center.id,
                vehicle_type="van",
                capacity=Capacity(20, 20_000, 100_000),
                shift_start=instant(8),
                shift_end=instant(18),
                skills=frozenset(),
                fixed_cost=Decimal("100"),
                cost_per_hour=Decimal("10"),
                cost_per_km=Decimal("1"),
            )
            for center in centers
        ),
        orders=orders,
        dataset_name="unit",
        dataset_seed=1,
        synthetic=True,
    )


def test_allocates_to_shortest_stock_eligible_center() -> None:
    outcome = DeterministicAllocationPolicy(FixedTravelTimes()).allocate(
        make_scenario((make_order("ORD-1", "SKU-A", 2),))
    )

    assert outcome.allocated[0].distribution_center.id == "DC-B"
    assert outcome.allocated[0].travel_seconds == 100
    assert outcome.unassigned == ()


def test_reports_proven_stock_failure_before_solver() -> None:
    outcome = DeterministicAllocationPolicy(FixedTravelTimes()).allocate(
        make_scenario((make_order("ORD-1", "SKU-X", 1),))
    )

    assert outcome.allocated == ()
    assert outcome.unassigned[0].reasons[0].code == "STOCK_NO_FULL_COVERAGE"
    assert outcome.unassigned[0].reasons[0].certainty.value == "PROVEN"


def test_consumes_stock_in_priority_order() -> None:
    scenario = make_scenario(
        (
            make_order("LOW", "SKU-A", 15, priority=10),
            make_order("HIGH", "SKU-A", 15, priority=90),
            make_order("LAST", "SKU-A", 15, priority=5),
        )
    )

    outcome = DeterministicAllocationPolicy(FixedTravelTimes()).allocate(scenario)

    assert [item.order.id for item in outcome.allocated] == ["HIGH", "LOW"]
    assert [item.order_id for item in outcome.unassigned] == ["LAST"]
