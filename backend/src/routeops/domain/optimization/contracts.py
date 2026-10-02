from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from routeops.domain.models import Capacity, Coordinate


class SolutionQuality(StrEnum):
    FAST = "FAST"
    BALANCED = "BALANCED"
    THOROUGH = "THOROUGH"


class ResultStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class StepKind(StrEnum):
    START = "START"
    DELIVERY = "DELIVERY"
    END = "END"
    BREAK = "BREAK"


class Certainty(StrEnum):
    PROVEN = "PROVEN"
    INFERRED = "INFERRED"


@dataclass(frozen=True, slots=True)
class VehicleCost:
    """Scaled input rates, not an estimate of the total business operating cost.

    `per_duty_hour_units` is the historical name. The existing VROOM mapping
    uses it for driving only; operating-cost-v1 separately prices whole duty.
    """

    currency: str
    scale: int
    fixed_units: int
    per_duty_hour_units: int
    per_km_units: int


@dataclass(frozen=True, slots=True)
class DeliveryTask:
    task_id: UUID
    order_id: str
    distribution_center_id: str
    location: Coordinate
    demand: Capacity
    service_seconds: int
    time_window_start: datetime
    time_window_end: datetime
    priority: int
    required_skills: frozenset[str]


@dataclass(frozen=True, slots=True)
class OptimizationVehicle:
    vehicle_id: UUID
    source_vehicle_id: str
    distribution_center_id: str
    vehicle_type: str
    start: Coordinate
    end: Coordinate
    shift_start: datetime
    shift_end: datetime
    capacity: Capacity
    skills: frozenset[str]
    costs: VehicleCost
    max_route_distance_meters: int | None = None
    max_driving_seconds: int | None = None
    max_delivery_tasks: int | None = None


@dataclass(frozen=True, slots=True)
class OptimizationOptions:
    timeout_seconds: int = 15
    solution_quality: SolutionQuality = SolutionQuality.BALANCED
    request_geometry: bool = True


@dataclass(frozen=True, slots=True)
class OptimizationProblem:
    contract_version: str
    problem_id: UUID
    scenario_id: UUID
    horizon_start: datetime
    horizon_end: datetime
    timezone: str
    tasks: tuple[DeliveryTask, ...]
    vehicles: tuple[OptimizationVehicle, ...]
    options: OptimizationOptions


@dataclass(frozen=True, slots=True)
class RouteStep:
    sequence: int
    kind: StepKind
    task_id: UUID | None
    order_id: str | None
    location: Coordinate
    arrival_at: datetime
    service_start_at: datetime
    departure_at: datetime
    travel_seconds_from_previous: int
    distance_meters_from_previous: int
    waiting_seconds: int
    service_seconds: int
    load_after: Capacity
    time_window_status: str


@dataclass(frozen=True, slots=True)
class RouteTotals:
    distance_meters: int
    driving_seconds: int
    service_seconds: int
    waiting_seconds: int
    total_duration_seconds: int
    objective_cost_units: int


@dataclass(frozen=True, slots=True)
class OptimizedRoute:
    vehicle_id: UUID
    source_vehicle_id: str
    distribution_center_id: str
    steps: tuple[RouteStep, ...]
    geometry: tuple[Coordinate, ...]
    totals: RouteTotals


@dataclass(frozen=True, slots=True)
class UnassignedReason:
    code: str
    certainty: Certainty
    detail: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UnassignedTask:
    task_id: UUID | None
    order_id: str
    stage: str
    reasons: tuple[UnassignedReason, ...]


@dataclass(frozen=True, slots=True)
class SolverMetadata:
    engine: str
    engine_version: str
    adapter_version: str
    routing_engine: str
    routing_engine_version: str
    solve_duration_ms: int


@dataclass(frozen=True, slots=True)
class OptimizationSummary:
    route_count: int
    assigned_task_count: int
    unassigned_task_count: int
    distance_meters: int
    driving_seconds: int
    service_seconds: int
    waiting_seconds: int
    total_duration_seconds: int
    objective_cost_units: int
    cost_scale: int
    currency: str


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    contract_version: str
    problem_id: UUID
    status: ResultStatus
    solver: SolverMetadata
    summary: OptimizationSummary
    routes: tuple[OptimizedRoute, ...]
    unassigned: tuple[UnassignedTask, ...]
    warnings: tuple[str, ...] = ()
