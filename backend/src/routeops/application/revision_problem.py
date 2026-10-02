"""Build and reconcile solver-neutral problems from immutable published rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from routeops.application.import_context import local_instant
from routeops.domain.models import Capacity, Coordinate, DistributionCenter, Order, OrderLine
from routeops.domain.optimization import (
    DeliveryTask,
    OptimizationOptions,
    OptimizationProblem,
    OptimizationResult,
    OptimizationVehicle,
    SolutionQuality,
    StepKind,
    VehicleCost,
)
from routeops.infrastructure.persistence.models import (
    DistributionCenterModel,
    ImportValidationContextModel,
    InventorySnapshotLineModel,
    InventorySnapshotModel,
    OrderLineModel,
    OrderModel,
    ScenarioRevisionModel,
    VehicleModel,
)
from routeops.infrastructure.solver.errors import SolverResponseError


class RunInputError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class WorkloadLimits:
    max_orders: int = 20
    max_lines: int = 60
    max_vehicles: int = 6
    max_matrix_cells: int = 80
    max_solver_matrix_cells: int = 1024
    max_inventory_positions: int = 10_000

    def __post_init__(self) -> None:
        if (
            min(
                self.max_orders,
                self.max_lines,
                self.max_vehicles,
                self.max_matrix_cells,
                self.max_solver_matrix_cells,
                self.max_inventory_positions,
            )
            <= 0
        ):
            raise ValueError("planning workload limits must be positive")

    def check(self, orders: int, lines: int, vehicles: int, centers: int) -> None:
        if (
            orders > self.max_orders
            or lines > self.max_lines
            or vehicles > self.max_vehicles
            or orders * centers > self.max_matrix_cells
            or (orders + 2 * vehicles) ** 2 > self.max_solver_matrix_cells
        ):
            raise RunInputError("SOLVER_WORKLOAD_LIMIT", 413)


def check_revision_size(session: Session, revision_id: UUID, limits: WorkloadLimits) -> None:
    """Reject oversized revisions before materializing rows or contacting dependencies."""

    models = (
        OrderModel,
        OrderLineModel,
        VehicleModel,
        DistributionCenterModel,
        InventorySnapshotLineModel,
    )
    counts = [
        int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.scenario_revision_id == revision_id)
            )
            or 0
        )
        for model in models
    ]

    orders, lines, vehicles, centers, inventory_positions = counts
    if orders > limits.max_orders:
        raise RunInputError("SOLVER_WORKLOAD_LIMIT", 413)
    if lines > limits.max_lines:
        raise RunInputError("SOLVER_WORKLOAD_LIMIT", 413)
    if vehicles > limits.max_vehicles:
        raise RunInputError("SOLVER_WORKLOAD_LIMIT", 413)
    if inventory_positions > limits.max_inventory_positions:
        raise RunInputError("SOLVER_WORKLOAD_LIMIT", 413)
    limits.check(orders, lines, vehicles, centers)


@dataclass(frozen=True, slots=True)
class PreparedRevision:
    revision_id: UUID
    scenario_id: UUID
    snapshot_id: UUID
    scenario_name: str
    content_sha256: str
    context_sha256: str
    timezone: str
    currency: str
    planning_date: object
    horizon_start: datetime
    horizon_end: datetime
    centers: tuple[DistributionCenter, ...]
    orders: tuple[Order, ...]
    vehicles: tuple[OptimizationVehicle, ...]
    line_count: int

    def problem(
        self,
        run_id: UUID,
        assigned_centers: dict[str, str],
        quality: SolutionQuality,
        timeout_seconds: int,
    ) -> OptimizationProblem:
        orders = {order.id: order for order in self.orders}
        tasks = tuple(
            DeliveryTask(
                task_id=uuid5(self.revision_id, order_id),
                order_id=order_id,
                distribution_center_id=center_id,
                location=orders[order_id].location,
                demand=orders[order_id].demand,
                service_seconds=orders[order_id].service_seconds,
                time_window_start=orders[order_id].time_window_start,
                time_window_end=orders[order_id].time_window_end,
                priority=orders[order_id].priority,
                required_skills=orders[order_id].required_skills,
            )
            for order_id, center_id in sorted(assigned_centers.items())
        )
        return OptimizationProblem(
            contract_version="1.0",
            problem_id=run_id,
            scenario_id=self.scenario_id,
            horizon_start=self.horizon_start,
            horizon_end=self.horizon_end,
            timezone=self.timezone,
            tasks=tasks,
            vehicles=self.vehicles,
            options=OptimizationOptions(
                timeout_seconds=timeout_seconds,
                solution_quality=quality,
                request_geometry=True,
            ),
        )


def _scaled(value: Decimal, scale: int) -> int:
    result = value * scale
    if result != result.to_integral_value() or result < 0 or result > 2_147_483_647:
        raise RunInputError("SOLVER_INTEGER_PRECISION_OR_RANGE")
    return int(result)


def load_prepared_revision(session: Session, revision_id: UUID) -> PreparedRevision:
    revision = session.get(ScenarioRevisionModel, revision_id)
    if revision is None:
        raise RunInputError("REVISION_NOT_FOUND", 404)
    snapshot = session.scalar(
        select(InventorySnapshotModel).where(
            InventorySnapshotModel.scenario_revision_id == revision_id,
            InventorySnapshotModel.kind == "IMPORTED",
        )
    )
    context = session.get(ImportValidationContextModel, revision.import_batch_id)
    if snapshot is None or context is None:
        raise RunInputError("REVISION_CONTEXT_INCOMPLETE")
    center_rows = list(
        session.execute(
            select(
                DistributionCenterModel,
                func.ST_Y(DistributionCenterModel.location),
                func.ST_X(DistributionCenterModel.location),
            ).where(DistributionCenterModel.scenario_revision_id == revision_id)
        )
    )
    centers = tuple(
        DistributionCenter(row.source_id, row.name, Coordinate(float(lat), float(lon)))
        for row, lat, lon in center_rows
    )
    center_by_id = {
        row.id: center for (row, _, _), center in zip(center_rows, centers, strict=True)
    }
    center_hours = {row.id: (row.operating_start, row.operating_end) for row, _, _ in center_rows}
    order_rows = list(
        session.execute(
            select(
                OrderModel, func.ST_Y(OrderModel.location), func.ST_X(OrderModel.location)
            ).where(OrderModel.scenario_revision_id == revision_id)
        )
    )
    lines_by_order: dict[UUID, list[OrderLine]] = {}
    line_count = 0
    for line_row in session.scalars(
        select(OrderLineModel).where(OrderLineModel.scenario_revision_id == revision_id)
    ):
        line_count += 1
        lines_by_order.setdefault(line_row.order_id, []).append(
            OrderLine(
                line_row.sku,
                line_row.quantity,
                line_row.unit_weight_kg,
                line_row.unit_volume_m3,
            )
        )
    orders = tuple(
        Order(
            id=row.source_id,
            customer_reference=row.customer_reference,
            location=Coordinate(float(lat), float(lon)),
            priority=row.priority,
            time_window_start=row.time_window_start,
            time_window_end=row.time_window_end,
            service_seconds=row.service_minutes * 60,
            required_skills=frozenset(row.required_skills),
            lines=tuple(sorted(lines_by_order.get(row.id, []), key=lambda line: line.sku)),
        )
        for row, lat, lon in order_rows
    )
    zone = ZoneInfo(revision.timezone_iana)
    vehicles: list[OptimizationVehicle] = []
    for vehicle_row in session.scalars(
        select(VehicleModel).where(VehicleModel.scenario_revision_id == revision_id)
    ):
        center = center_by_id[vehicle_row.distribution_center_id]
        open_at, close_at = center_hours[vehicle_row.distribution_center_id]
        shift_start = max(
            local_instant(revision.planning_date, vehicle_row.shift_start, zone),
            local_instant(revision.planning_date, open_at, zone),
            revision.horizon_start_at,
        )
        shift_end = min(
            local_instant(revision.planning_date, vehicle_row.shift_end, zone),
            local_instant(revision.planning_date, close_at, zone),
            revision.horizon_end_at,
        )
        if shift_start >= shift_end:
            raise RunInputError("FLEET_SHIFT_OUTSIDE_HORIZON")
        vehicles.append(
            OptimizationVehicle(
                vehicle_id=uuid5(revision_id, vehicle_row.source_id),
                source_vehicle_id=vehicle_row.source_id,
                distribution_center_id=center.id,
                vehicle_type=vehicle_row.vehicle_type,
                start=center.location,
                end=center.location,
                shift_start=shift_start,
                shift_end=shift_end,
                capacity=Capacity(
                    vehicle_row.capacity_units,
                    _scaled(vehicle_row.capacity_weight_kg, 1000),
                    _scaled(vehicle_row.capacity_volume_m3, 1_000_000),
                ),
                skills=frozenset(vehicle_row.skills),
                costs=VehicleCost(
                    currency=revision.currency,
                    scale=10_000,
                    fixed_units=_scaled(vehicle_row.fixed_cost, 10_000),
                    per_duty_hour_units=_scaled(vehicle_row.cost_per_hour, 10_000),
                    per_km_units=_scaled(vehicle_row.cost_per_km, 10_000),
                ),
            )
        )
    if not centers or not orders or not vehicles:
        raise RunInputError("SCENARIO_NOT_EXECUTABLE")
    if any(not order.lines for order in orders):
        raise RunInputError("ORDER_WITHOUT_LINES")
    for order in orders:
        try:
            demand = order.demand
        except ValueError as exc:
            raise RunInputError("SOLVER_INTEGER_PRECISION_OR_RANGE") from exc
        if max(demand.units, demand.weight_grams, demand.volume_cm3) > 2_147_483_647:
            raise RunInputError("SOLVER_INTEGER_PRECISION_OR_RANGE")
        if (
            order.time_window_start < revision.horizon_start_at
            or order.time_window_end > revision.horizon_end_at
            or order.time_window_start >= order.time_window_end
        ):
            raise RunInputError("ORDER_WINDOW_OUTSIDE_HORIZON")
    return PreparedRevision(
        revision_id=revision_id,
        scenario_id=revision.scenario_id,
        snapshot_id=snapshot.id,
        scenario_name=f"Scenario {revision.scenario_id}",
        content_sha256=revision.content_sha256,
        context_sha256=context.context_sha256,
        timezone=revision.timezone_iana,
        currency=revision.currency,
        planning_date=revision.planning_date,
        horizon_start=revision.horizon_start_at,
        horizon_end=revision.horizon_end_at,
        centers=centers,
        orders=orders,
        vehicles=tuple(vehicles),
        line_count=line_count,
    )


def _distance_m(first: Coordinate, second: Coordinate) -> float:
    latitude_delta = radians(second.latitude - first.latitude)
    longitude_delta = radians(second.longitude - first.longitude)
    arc = sin(latitude_delta / 2) ** 2 + (
        cos(radians(first.latitude)) * cos(radians(second.latitude))
        * sin(longitude_delta / 2) ** 2
    )
    return 12_742_000 * asin(min(1, sqrt(arc)))


def reconcile_result(
    problem: OptimizationProblem, result: OptimizationResult,
    max_geometry_gap_m: float = 250,
) -> None:
    if (
        result.problem_id != problem.problem_id
        or result.contract_version != problem.contract_version
    ):
        raise SolverResponseError("Solver result does not match the submitted problem")
    tasks = {task.task_id: task for task in problem.tasks}
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in problem.vehicles}
    seen: set[UUID] = set()
    used_vehicles: set[UUID] = set()
    delivered = 0
    for route in result.routes:
        vehicle = vehicles.get(route.vehicle_id)
        if vehicle is None or vehicle.vehicle_id in used_vehicles:
            raise SolverResponseError("Solver returned an unknown or duplicate vehicle")
        used_vehicles.add(vehicle.vehicle_id)
        if (
            route.distribution_center_id != vehicle.distribution_center_id
            or route.source_vehicle_id != vehicle.source_vehicle_id
            or len(route.steps) < 2
            or route.steps[0].kind != StepKind.START
            or route.steps[-1].kind != StepKind.END
        ):
            raise SolverResponseError("Solver route does not match its vehicle or center")
        if (
            route.steps[0].arrival_at < vehicle.shift_start
            or route.steps[-1].departure_at > vehicle.shift_end
        ):
            raise SolverResponseError("Solver route falls outside the vehicle shift")
        if len(route.geometry) < 2 or any(
            min(_distance_m(step.location, point) for point in route.geometry)
            > max_geometry_gap_m
            for step in route.steps
        ):
            raise SolverResponseError("Solver geometry does not cover its stops")
        if (
            _distance_m(route.geometry[0], route.steps[0].location) > max_geometry_gap_m
            or _distance_m(route.geometry[-1], route.steps[-1].location)
            > max_geometry_gap_m
        ):
            raise SolverResponseError("Solver geometry endpoints do not match route stops")
        for actual, expected in (
            (route.steps[0].location, vehicle.start),
            (route.steps[-1].location, vehicle.end),
        ):
            if (
                abs(actual.latitude - expected.latitude) > 0.00001
                or abs(actual.longitude - expected.longitude) > 0.00001
            ):
                raise SolverResponseError("Solver route is not closed at its allocated center")
        assigned = Capacity(0, 0, 0)
        for step in route.steps:
            if step.kind != StepKind.DELIVERY:
                continue
            if step.task_id is None:
                raise SolverResponseError("Solver delivery step has no task")
            task = tasks.get(step.task_id)
            if task is None or task.task_id in seen or step.order_id != task.order_id:
                raise SolverResponseError("Solver returned an unknown or duplicate order")
            seen.add(task.task_id)
            delivered += 1
            if task.distribution_center_id != vehicle.distribution_center_id or not (
                task.required_skills <= vehicle.skills
            ):
                raise SolverResponseError("Solver assigned an incompatible vehicle")
            if _distance_m(step.location, task.location) > 2:
                raise SolverResponseError("Solver delivery location differs from the order")
            assigned = Capacity(
                assigned.units + task.demand.units,
                assigned.weight_grams + task.demand.weight_grams,
                assigned.volume_cm3 + task.demand.volume_cm3,
            )
            if (
                step.service_start_at < task.time_window_start
                or step.service_start_at > task.time_window_end
                or step.service_seconds != task.service_seconds
            ):
                raise SolverResponseError("Solver violated an order window or service time")
        if not vehicle.capacity.fits(assigned):
            raise SolverResponseError("Solver exceeded vehicle capacity")
    for item in result.unassigned:
        task = next((task for task in tasks.values() if task.order_id == item.order_id), None)
        if task is None or task.task_id in seen or item.task_id != task.task_id:
            raise SolverResponseError("Solver returned an unknown or duplicate unassigned order")
        seen.add(task.task_id)
    if seen != set(tasks):
        raise SolverResponseError("Solver omitted a submitted order")
    if (
        result.summary.route_count != len(result.routes)
        or result.summary.assigned_task_count != delivered
        or result.summary.unassigned_task_count != len(result.unassigned)
    ):
        raise SolverResponseError("Solver result counts do not match its routes and exceptions")
