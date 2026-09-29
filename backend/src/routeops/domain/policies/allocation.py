from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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

        for order in sorted_orders:
            stock_eligible = [
                center
                for center in scenario.centers
                if all(
                    available.get((center.id, line.sku), 0) >= line.quantity
                    for line in order.lines
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
                                    "required": {
                                        line.sku: line.quantity for line in order.lines
                                    }
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
                                evidence={"required_skills": sorted(order.required_skills)},
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
