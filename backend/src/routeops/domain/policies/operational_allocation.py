"""Deterministic, solver-neutral allocation over operational availability."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from routeops.domain.models import DistributionCenter, Order
from routeops.domain.policies.allocation import TravelTimeProvider


@dataclass(frozen=True, slots=True)
class OperationalStock:
    center_id: str
    sku: str
    on_hand: int
    externally_reserved: int
    safety_stock: int
    routeops_reserved: int

    @property
    def available(self) -> int:
        return self.on_hand - self.externally_reserved - self.safety_stock - self.routeops_reserved


@dataclass(frozen=True, slots=True)
class OrderAllocation:
    order_id: str
    center_id: str | None
    reason_code: str | None
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OperationalVehicle:
    id: str
    distribution_center_id: str
    capacity_units: int
    capacity_weight_kg: Decimal
    capacity_volume_m3: Decimal
    skills: frozenset[str]

    def fits(self, order: Order) -> bool:
        return (
            sum(line.quantity for line in order.lines) <= self.capacity_units
            and sum((line.quantity * line.unit_weight_kg for line in order.lines), Decimal(0))
            <= self.capacity_weight_kg
            and sum((line.quantity * line.unit_volume_m3 for line in order.lines), Decimal(0))
            <= self.capacity_volume_m3
        )


class OperationalAllocationPolicy:
    """Priority order with a one-step alternatives lookahead, not a global optimum."""

    GREEDY = "greedy-v1"
    ALTERNATIVES = "alternatives-v2"

    def __init__(self, travel_times: TravelTimeProvider, version: str = ALTERNATIVES) -> None:
        if version not in (self.GREEDY, self.ALTERNATIVES):
            raise ValueError("unknown allocation policy")
        self.travel_times = travel_times
        self.version = version

    def allocate(
        self,
        centers: tuple[DistributionCenter, ...],
        orders: tuple[Order, ...],
        vehicles: tuple[OperationalVehicle, ...],
        stock: tuple[OperationalStock, ...],
    ) -> tuple[OrderAllocation, ...]:
        ordered = sorted(
            orders, key=lambda order: (-order.priority, order.time_window_end, order.id)
        )
        available = {(row.center_id, row.sku): row.available for row in stock}
        stock_by_key = {(row.center_id, row.sku): row for row in stock}
        results: list[OrderAllocation] = []
        for sequence, order in enumerate(ordered, start=1):
            candidates: list[dict[str, Any]] = []
            ranked: list[tuple[tuple[int | str, ...], str]] = []
            for center in sorted(centers, key=lambda item: item.id):
                required = {line.sku: line.quantity for line in order.lines}
                considered = {
                    sku: {
                        "on_hand": row.on_hand if row else 0,
                        "externally_reserved": row.externally_reserved if row else 0,
                        "safety_stock": row.safety_stock if row else 0,
                        "routeops_reserved": row.routeops_reserved if row else 0,
                        "available_before": available.get((center.id, sku), 0),
                    }
                    for sku in sorted(required)
                    for row in (stock_by_key.get((center.id, sku)),)
                }
                missing = {
                    sku: quantity - available.get((center.id, sku), 0)
                    for sku, quantity in required.items()
                    if available.get((center.id, sku), 0) < quantity
                }
                vehicle_checks = [
                    {
                        "vehicle_id": vehicle.id,
                        "skills_compatible": order.required_skills.issubset(vehicle.skills),
                        "skills": sorted(vehicle.skills),
                        "capacity_compatible": vehicle.fits(order),
                        "capacity_units": vehicle.capacity_units,
                        "capacity_weight_kg": str(vehicle.capacity_weight_kg),
                        "capacity_volume_m3": str(vehicle.capacity_volume_m3),
                    }
                    for vehicle in sorted(vehicles, key=lambda item: item.id)
                    if vehicle.distribution_center_id == center.id
                ]
                matching_vehicles = [
                    item["vehicle_id"]
                    for item in vehicle_checks
                    if item["skills_compatible"] and item["capacity_compatible"]
                ]
                candidate: dict[str, Any] = {
                    "center_id": center.id,
                    "required": required,
                    "stock": considered,
                    "missing_stock": missing,
                    "compatible_vehicles": matching_vehicles,
                    "vehicle_checks": vehicle_checks,
                    "duration_seconds": None,
                    "inventory_slack": None,
                    "remaining_orders_with_alternative": None,
                    "rank": None,
                    "discard_reason": "STOCK_INSUFFICIENT" if missing else "NO_COMPATIBLE_VEHICLE",
                }
                if not missing and matching_vehicles:
                    duration = self.travel_times.duration_seconds(center.location, order.location)
                    slack = sum(
                        available[(center.id, sku)] - quantity for sku, quantity in required.items()
                    )
                    feasible_after = self._remaining_feasible(
                        ordered[sequence:], centers, vehicles, available, center.id, required
                    )
                    rank: tuple[int | str, ...]
                    if self.version == self.ALTERNATIVES:
                        rank = (-feasible_after, duration, -slack, center.id)
                    else:
                        rank = (duration, -slack, center.id)
                    candidate.update(
                        duration_seconds=duration,
                        inventory_slack=slack,
                        remaining_orders_with_alternative=feasible_after,
                        rank=list(rank),
                        discard_reason="TIE_BREAK_LOSS",
                    )
                    ranked.append((rank, center.id))
                candidates.append(candidate)
            chosen = min(ranked)[1] if ranked else None
            if chosen is not None:
                for line in order.lines:
                    available[(chosen, line.sku)] -= line.quantity
                for candidate in candidates:
                    if candidate["center_id"] == chosen:
                        candidate["discard_reason"] = None
            reason = (
                None
                if chosen
                else (
                    "NO_COMPATIBLE_VEHICLE"
                    if any(not candidate["missing_stock"] for candidate in candidates)
                    else "STOCK_NO_FULL_COVERAGE"
                )
            )
            results.append(
                OrderAllocation(
                    order_id=order.id,
                    center_id=chosen,
                    reason_code=reason,
                    evidence={
                        "policy_version": self.version,
                        "sequence": sequence,
                        "order_priority": order.priority,
                        "window_end": order.time_window_end.isoformat(),
                        "required_skills": sorted(order.required_skills),
                        "required": required,
                        "demand": {
                            "units": sum(line.quantity for line in order.lines),
                            "weight_kg": str(
                                sum(
                                    (line.quantity * line.unit_weight_kg for line in order.lines),
                                    Decimal(0),
                                )
                            ),
                            "volume_m3": str(
                                sum(
                                    (line.quantity * line.unit_volume_m3 for line in order.lines),
                                    Decimal(0),
                                )
                            ),
                        },
                        "chosen_center_id": chosen,
                        "reason_code": reason,
                        "candidates": candidates,
                    },
                )
            )
        return tuple(results)

    @staticmethod
    def _remaining_feasible(
        remaining: list[Order],
        centers: tuple[DistributionCenter, ...],
        vehicles: tuple[OperationalVehicle, ...],
        available: dict[tuple[str, str], int],
        selected: str,
        required: dict[str, int],
    ) -> int:
        count = 0
        for order in remaining:
            for center in centers:
                if all(
                    available.get((center.id, line.sku), 0)
                    - (required.get(line.sku, 0) if center.id == selected else 0)
                    >= line.quantity
                    for line in order.lines
                ) and any(
                    vehicle.distribution_center_id == center.id
                    and order.required_skills.issubset(vehicle.skills)
                    and vehicle.fits(order)
                    for vehicle in vehicles
                ):
                    count += 1
                    break
        return count
