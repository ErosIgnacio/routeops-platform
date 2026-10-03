from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from routeops.domain.models import Coordinate, DistributionCenter, Order, ScenarioData
from routeops.domain.optimization.contracts import Certainty, UnassignedReason, UnassignedTask


class TravelTimeProvider(Protocol):
    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int: ...


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    order: Order
    distribution_center: DistributionCenter
    travel_seconds: int
    inventory_slack: int


@dataclass(frozen=True, slots=True)
class AllocationOutcome:
    allocated: tuple[AllocationDecision, ...]
    unassigned: tuple[UnassignedTask, ...]


class DeterministicAllocationPolicy:
    """Greedy and auditable v1 allocation; this is not a global optimum."""

    version = "greedy-v1"

    def __init__(self, travel_times: TravelTimeProvider) -> None:
        self._travel_times = travel_times

    def allocate(self, scenario: ScenarioData) -> AllocationOutcome:
        centers = {center.id: center for center in scenario.centers}
        available = {
            (item.distribution_center_id, item.sku): item.available for item in scenario.inventory
        }
        allocated: list[AllocationDecision] = []
        unassigned: list[UnassignedTask] = []

        sorted_orders = sorted(
            scenario.orders,
            key=lambda order: (-order.priority, order.time_window_end, order.id),
        )

        for sequence, order in enumerate(sorted_orders, start=1):
            stock_eligible = [
                center
                for center in scenario.centers
                if all(
                    available.get((center.id, line.sku), 0) >= line.quantity for line in order.lines
                )
            ]
            if not stock_eligible:
                unassigned.append(
                    UnassignedTask(
                        task_id=None,
                        order_id=order.id,
                        stage="ALLOCATION",
                        reasons=(
                            UnassignedReason(
                                code="STOCK_NO_FULL_COVERAGE",
                                certainty=Certainty.PROVEN,
                                detail="No distribution center can cover every order line.",
                                evidence={
                                    **self._evidence(scenario, order, available, sequence),
                                },
                            ),
                        ),
                    )
                )
                continue

            compatible = [
                center
                for center in stock_eligible
                if any(
                    vehicle.distribution_center_id == center.id
                    and order.required_skills.issubset(vehicle.skills)
                    and vehicle.capacity.fits(order.demand)
                    for vehicle in scenario.vehicles
                )
            ]
            if not compatible:
                unassigned.append(
                    UnassignedTask(
                        task_id=None,
                        order_id=order.id,
                        stage="ALLOCATION",
                        reasons=(
                            UnassignedReason(
                                code="NO_COMPATIBLE_VEHICLE",
                                certainty=Certainty.PROVEN,
                                detail="No stock-eligible center has a compatible vehicle.",
                                evidence=self._evidence(scenario, order, available, sequence),
                            ),
                        ),
                    )
                )
                continue

            ranked: list[tuple[int, int, str, DistributionCenter]] = []
            for center in compatible:
                travel_seconds = self._travel_times.duration_seconds(
                    center.location, order.location
                )
                slack = sum(
                    available[(center.id, line.sku)] - line.quantity for line in order.lines
                )
                ranked.append((travel_seconds, -slack, center.id, center))

            travel_seconds, negative_slack, _, selected = min(ranked)
            for line in order.lines:
                available[(selected.id, line.sku)] -= line.quantity
            allocated.append(
                AllocationDecision(
                    order=order,
                    distribution_center=centers[selected.id],
                    travel_seconds=travel_seconds,
                    inventory_slack=-negative_slack,
                )
            )

        return AllocationOutcome(tuple(allocated), tuple(unassigned))

    def _evidence(
        self,
        scenario: ScenarioData,
        order: Order,
        available: dict[tuple[str, str], int],
        sequence: int,
    ) -> dict[str, Any]:
        required = {line.sku: line.quantity for line in order.lines}
        stock = {(row.distribution_center_id, row.sku): row for row in scenario.inventory}
        candidates = []
        for center in sorted(scenario.centers, key=lambda item: item.id):
            considered = {
                sku: {
                    "on_hand": row.on_hand if row else 0,
                    "externally_reserved": row.reserved if row else 0,
                    "safety_stock": row.safety_stock if row else 0,
                    "routeops_reserved": 0,
                    "available_before": available.get((center.id, sku), 0),
                }
                for sku in required
                for row in (stock.get((center.id, sku)),)
            }
            candidates.append(
                {
                    "center_id": center.id,
                    "stock": considered,
                    "missing_stock": {
                        sku: quantity - available.get((center.id, sku), 0)
                        for sku, quantity in required.items()
                        if available.get((center.id, sku), 0) < quantity
                    },
                    "vehicle_checks": [
                        {
                            "vehicle_id": vehicle.id,
                            "skills_compatible": order.required_skills <= vehicle.skills,
                            "skills": sorted(vehicle.skills),
                            "capacity_compatible": vehicle.capacity.fits(order.demand),
                            "capacity_units": vehicle.capacity.units,
                            "capacity_weight_kg": str(
                                Decimal(vehicle.capacity.weight_grams) / 1000
                            ),
                            "capacity_volume_m3": str(
                                Decimal(vehicle.capacity.volume_cm3) / 1_000_000
                            ),
                        }
                        for vehicle in scenario.vehicles
                        if vehicle.distribution_center_id == center.id
                    ],
                }
            )
        return {
            "policy_version": self.version,
            "sequence": sequence,
            "required": required,
            "required_skills": sorted(order.required_skills),
            "demand": {
                "units": order.demand.units,
                "weight_grams": order.demand.weight_grams,
                "volume_cm3": order.demand.volume_cm3,
            },
            "candidates": candidates,
        }
