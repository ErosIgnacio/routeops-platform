"""Build and reconcile solver-neutral problems from immutable published rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from routeops.application.import_context import local_instant
from routeops.application.optimization_reconciliation import reconcile_result as reconcile_result
from routeops.domain.models import Capacity, Coordinate, DistributionCenter, Order, OrderLine
from routeops.domain.optimization import (
    DeliveryTask,
    OptimizationOptions,
    OptimizationProblem,
    OptimizationVehicle,
    SolutionQuality,
    VehicleCost,
)
from routeops.domain.optimization.validation import instant
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
            contract_version=(
                "1.1"
                if any(
                    vehicle.max_route_distance_meters is not None
                    or vehicle.max_driving_seconds is not None
                    or vehicle.max_delivery_tasks is not None
                    for vehicle in self.vehicles
                )
                else "1.0"
            ),
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
                max_route_distance_meters=vehicle_row.max_route_distance_meters,
                max_driving_seconds=vehicle_row.max_driving_seconds,
                max_delivery_tasks=vehicle_row.max_delivery_tasks,
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
    try:
        for value in (
            revision.horizon_start_at,
            revision.horizon_end_at,
            *(
                value
                for order in orders
                for value in (
                    order.time_window_start,
                    order.time_window_end,
                )
            ),
            *(value for vehicle in vehicles for value in (vehicle.shift_start, vehicle.shift_end)),
        ):
            instant(value)
    except ValueError as exc:
        raise RunInputError("RUN_TIME_PRECISION_INVALID") from exc
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
