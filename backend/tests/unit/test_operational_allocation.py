from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from routeops.domain.models import Coordinate, OrderLine
from routeops.domain.policies.operational_allocation import (
    OperationalAllocationPolicy,
    OperationalVehicle,
)
from routeops.infrastructure.data.allocation_demos import AllocationDemo, allocation_demos


class DemoTravelTimes:
    def __init__(self, demo: AllocationDemo) -> None:
        self.durations = {
            center.location.longitude: demo.durations_by_center[center.id]
            for center in demo.centers
        }

    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int:
        return self.durations[origin.longitude]


def evaluate(name: str, version: str = OperationalAllocationPolicy.ALTERNATIVES):
    demo = allocation_demos()[name]
    return OperationalAllocationPolicy(DemoTravelTimes(demo), version).allocate(
        demo.centers, demo.orders, demo.vehicles, demo.stock
    )


def test_exclusive_stock_never_splits_an_order() -> None:
    decisions = evaluate("exclusive_stock")
    assert [(row.order_id, row.center_id, row.reason_code) for row in decisions] == [
        ("ORD-A", "CD-A", None),
        ("ORD-B", "CD-B", None),
        ("ORD-MIX", None, "STOCK_NO_FULL_COVERAGE"),
    ]
    mixed = decisions[-1].evidence["candidates"]
    assert mixed[0]["missing_stock"] == {"SKU-B": 1}
    assert mixed[1]["missing_stock"] == {"SKU-A": 1}


def test_multiple_centers_records_choice_and_rejected_candidate() -> None:
    decision = evaluate("choice_between_centers")[0]
    assert decision.center_id == "CD-B"
    candidates = decision.evidence["candidates"]
    assert [item["duration_seconds"] for item in candidates] == [200, 100]
    assert candidates[0]["discard_reason"] == "TIE_BREAK_LOSS"
    assert candidates[1]["discard_reason"] is None


def test_alternatives_policy_rescues_restricted_order_without_claiming_optimality() -> None:
    greedy = evaluate("shared_stock_restricted", OperationalAllocationPolicy.GREEDY)
    improved = evaluate("shared_stock_restricted")
    assert [(row.order_id, row.center_id) for row in greedy] == [
        ("ORD-FLEX", "CD-A"),
        ("ORD-RESTRICTED", None),
    ]
    assert greedy[1].reason_code == "STOCK_NO_FULL_COVERAGE"
    assert [(row.order_id, row.center_id) for row in improved] == [
        ("ORD-FLEX", "CD-B"),
        ("ORD-RESTRICTED", "CD-A"),
    ]
    flex_candidates = improved[0].evidence["candidates"]
    assert [item["remaining_orders_with_alternative"] for item in flex_candidates] == [0, 1]
    assert improved[0].evidence["policy_version"] == "alternatives-v2"


def test_fleet_skills_and_capacity_are_distinct_from_stock_shortage() -> None:
    decisions = evaluate("fleet_restrictions")
    assert [row.reason_code for row in decisions] == [
        "NO_COMPATIBLE_VEHICLE",
        "NO_COMPATIBLE_VEHICLE",
    ]
    checks = [row.evidence["candidates"][0]["vehicle_checks"][0] for row in decisions]
    assert checks[0]["skills_compatible"] is False and checks[0]["capacity_compatible"] is True
    assert checks[1]["skills_compatible"] is True and checks[1]["capacity_compatible"] is False
    assert all(not row.evidence["candidates"][0]["missing_stock"] for row in decisions)


def test_fleet_capacity_uses_exact_published_precision() -> None:
    demo = allocation_demos()["choice_between_centers"]
    order = replace(
        demo.orders[0],
        lines=(OrderLine("SKU-X", 2, Decimal("0.001"), Decimal("0.000001")),),
    )
    too_small = OperationalVehicle(
        "V-A", "CD-A", 2, Decimal("0.001"), Decimal("0.000002"), frozenset()
    )
    exact_fit = OperationalVehicle(
        "V-B", "CD-B", 2, Decimal("0.002"), Decimal("0.000002"), frozenset()
    )
    decision = OperationalAllocationPolicy(DemoTravelTimes(demo)).allocate(
        demo.centers, (order,), (too_small, exact_fit), demo.stock
    )[0]
    assert decision.center_id == "CD-B"
    assert decision.evidence["candidates"][0]["vehicle_checks"][0]["capacity_compatible"] is False
    assert decision.evidence["candidates"][1]["vehicle_checks"][0]["capacity_compatible"] is True
