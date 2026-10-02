"""Small deterministic allocation fixtures for 2.4 and later demo selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from routeops.domain.models import (
    Coordinate,
    DistributionCenter,
    Order,
    OrderLine,
)
from routeops.domain.policies.operational_allocation import OperationalStock, OperationalVehicle


@dataclass(frozen=True, slots=True)
class AllocationDemo:
    name: str
    centers: tuple[DistributionCenter, ...]
    orders: tuple[Order, ...]
    vehicles: tuple[OperationalVehicle, ...]
    stock: tuple[OperationalStock, ...]
    durations_by_center: dict[str, int]


def _time(hour: int) -> datetime:
    return datetime.fromisoformat(f"2026-10-15T{hour:02d}:00:00-03:00")


def _order(
    name: str,
    *lines: tuple[str, int],
    priority: int = 50,
    skills: frozenset[str] = frozenset(),
    location: Coordinate | None = None,
) -> Order:
    return Order(
        id=name,
        customer_reference=f"DEMO-{name}",
        location=location or Coordinate(-33.443, -70.648),
        priority=priority,
        time_window_start=_time(9),
        time_window_end=_time(14),
        service_seconds=300,
        required_skills=skills,
        lines=tuple(
            OrderLine(sku, quantity, Decimal("1"), Decimal("0.001")) for sku, quantity in lines
        ),
    )


def _vehicle(
    center: str, *, capacity: int = 20, skills: frozenset[str] = frozenset()
) -> OperationalVehicle:
    return OperationalVehicle(
        id=f"VEH-{center}",
        distribution_center_id=center,
        capacity_units=capacity,
        capacity_weight_kg=Decimal(capacity),
        capacity_volume_m3=Decimal(capacity) * Decimal("0.001"),
        skills=skills,
    )


def allocation_demos() -> dict[str, AllocationDemo]:
    centers = (
        DistributionCenter("CD-A", "A", Coordinate(-33.4445, -70.6635)),
        DistributionCenter("CD-B", "B", Coordinate(-33.438, -70.638)),
    )
    vehicles = (_vehicle("CD-A"), _vehicle("CD-B"))
    return {
        "exclusive_stock": AllocationDemo(
            "exclusive_stock",
            centers,
            (
                _order("ORD-A", ("SKU-A", 2), priority=90,
                       location=Coordinate(-33.446, -70.660)),
                _order("ORD-B", ("SKU-B", 2), priority=80,
                       location=Coordinate(-33.439, -70.632)),
                _order("ORD-MIX", ("SKU-A", 1), ("SKU-B", 1), priority=70),
            ),
            vehicles,
            (
                OperationalStock("CD-A", "SKU-A", 5, 0, 0, 0),
                OperationalStock("CD-B", "SKU-B", 5, 0, 0, 0),
            ),
            {"CD-A": 100, "CD-B": 200},
        ),
        "choice_between_centers": AllocationDemo(
            "choice_between_centers",
            centers,
            (_order("ORD-CHOICE", ("SKU-X", 2),
                    location=Coordinate(-33.439, -70.632)),),
            vehicles,
            (
                OperationalStock("CD-A", "SKU-X", 5, 0, 0, 0),
                OperationalStock("CD-B", "SKU-X", 5, 0, 0, 0),
            ),
            {"CD-A": 200, "CD-B": 100},
        ),
        "shared_stock_restricted": AllocationDemo(
            "shared_stock_restricted",
            centers,
            (
                _order("ORD-FLEX", ("SKU-X", 5), priority=90,
                       location=Coordinate(-33.446, -70.660)),
                _order("ORD-RESTRICTED", ("SKU-X", 5), ("SKU-Y", 1), priority=80),
            ),
            vehicles,
            (
                OperationalStock("CD-A", "SKU-X", 5, 0, 0, 0),
                OperationalStock("CD-A", "SKU-Y", 1, 0, 0, 0),
                OperationalStock("CD-B", "SKU-X", 5, 0, 0, 0),
            ),
            {"CD-A": 100, "CD-B": 200},
        ),
        "fleet_restrictions": AllocationDemo(
            "fleet_restrictions",
            (centers[0],),
            (
                _order("ORD-SKILL", ("SKU-X", 2), priority=90,
                       skills=frozenset({"cold"}),
                       location=Coordinate(-33.446, -70.660)),
                _order("ORD-CAPACITY", ("SKU-X", 8), priority=80),
            ),
            (_vehicle("CD-A", capacity=5),),
            (OperationalStock("CD-A", "SKU-X", 20, 0, 0, 0),),
            {"CD-A": 100},
        ),
    }
