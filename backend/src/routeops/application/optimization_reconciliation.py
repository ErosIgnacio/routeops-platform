"""Reconcile solver facts before persistence or reservation confirmation."""

from datetime import UTC, timedelta
from math import asin, cos, radians, sin, sqrt
from uuid import UUID

from routeops.domain.models import Capacity, Coordinate
from routeops.domain.optimization import (
    DeliveryTask,
    OptimizationProblem,
    OptimizationResult,
    OptimizedRoute,
    ResultStatus,
    StepKind,
)
from routeops.infrastructure.solver.errors import SolverResponseError


def _reconcile_route_facts(
    route: OptimizedRoute,
    initial_load: Capacity,
    tasks: dict[UUID, DeliveryTask],
    verify_leg_distances: bool,
) -> None:
    if not any(step.kind == StepKind.DELIVERY for step in route.steps):
        raise SolverResponseError("Solver returned an unused vehicle as a route")
    remaining = initial_load.as_vroom_array()
    previous = None
    for index, step in enumerate(route.steps):
        values = (
            step.sequence,
            step.travel_seconds_from_previous,
            step.distance_meters_from_previous,
            step.waiting_seconds,
            step.service_seconds,
            *step.load_after.as_vroom_array(),
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise SolverResponseError("Solver route facts must be nonnegative integers")
        if step.sequence != index or (
            0 < index < len(route.steps) - 1 and (step.kind != StepKind.DELIVERY)
        ):
            raise SolverResponseError("Solver step sequence or kind is invalid for v1")
        if any(
            value.utcoffset() is None or value.microsecond
            for value in (
                step.arrival_at,
                step.service_start_at,
                step.departure_at,
            )
        ):
            raise SolverResponseError("Solver step instants require offsets and whole seconds")
        if step.service_start_at.astimezone(UTC) != step.arrival_at.astimezone(UTC) + timedelta(
            seconds=step.waiting_seconds
        ) or step.departure_at.astimezone(UTC) != step.service_start_at.astimezone(UTC) + timedelta(
            seconds=step.service_seconds
        ):
            raise SolverResponseError("Solver timestamps do not match service and waiting")
        if previous is None:
            if step.travel_seconds_from_previous or step.distance_meters_from_previous:
                raise SolverResponseError("Solver start step has a previous leg")
        elif step.arrival_at.astimezone(UTC) != previous.departure_at.astimezone(UTC) + (
            timedelta(seconds=step.travel_seconds_from_previous)
        ):
            raise SolverResponseError("Solver chronology does not match travel durations")
        if step.kind == StepKind.DELIVERY:
            assert step.task_id is not None  # checked by the contract reconciliation above
            task = tasks[step.task_id]
            remaining = [
                left - used
                for left, used in zip(remaining, task.demand.as_vroom_array(), strict=True)
            ]
            expected_status = "EARLY_WAIT" if step.waiting_seconds else "ON_TIME"
            if step.time_window_status != expected_status:
                raise SolverResponseError("Solver window status contradicts the service facts")
        elif step.task_id is not None or step.order_id is not None or step.service_seconds:
            raise SolverResponseError("Solver endpoint carries a delivery or service")
        if step.load_after.as_vroom_array() != remaining:
            raise SolverResponseError("Solver loads do not match complete deliveries")
        previous = step
    for field, step_field in (
        ("distance_meters", "distance_meters_from_previous"),
        ("driving_seconds", "travel_seconds_from_previous"),
        ("service_seconds", "service_seconds"),
        ("waiting_seconds", "waiting_seconds"),
    ):
        if field == "distance_meters" and not verify_leg_distances:
            continue
        if getattr(route.totals, field) != sum(getattr(step, step_field) for step in route.steps):
            raise SolverResponseError("Solver route totals do not match step facts")
    if (
        type(route.totals.objective_cost_units) is not int
        or route.totals.objective_cost_units < 0
        or route.totals.total_duration_seconds
        != route.totals.driving_seconds
        + route.totals.service_seconds
        + route.totals.waiting_seconds
        or route.totals.total_duration_seconds
        != (
            route.steps[-1].departure_at.astimezone(UTC) - route.steps[0].arrival_at.astimezone(UTC)
        ).total_seconds()
    ):
        raise SolverResponseError("Solver duty duration or objective is invalid")


def _distance_m(first: Coordinate, second: Coordinate) -> float:
    latitude_delta = radians(second.latitude - first.latitude)
    longitude_delta = radians(second.longitude - first.longitude)
    arc = sin(latitude_delta / 2) ** 2 + (
        cos(radians(first.latitude)) * cos(radians(second.latitude)) * sin(longitude_delta / 2) ** 2
    )
    return 12_742_000 * asin(min(1, sqrt(arc)))


def reconcile_result(
    problem: OptimizationProblem,
    result: OptimizationResult,
    max_geometry_gap_m: float = 250,
) -> None:
    if (
        result.problem_id != problem.problem_id
        or result.contract_version != problem.contract_version
    ):
        raise SolverResponseError("Solver result does not match the submitted problem")
    expected_status = ResultStatus.PARTIAL if result.unassigned else ResultStatus.SUCCEEDED
    if result.status != expected_status:
        raise SolverResponseError("Solver result status contradicts its exceptions")
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
        if problem.options.request_geometry and (
            len(route.geometry) < 2
            or any(
                min(_distance_m(step.location, point) for point in route.geometry)
                > max_geometry_gap_m
                for step in route.steps
            )
        ):
            raise SolverResponseError("Solver geometry does not cover its stops")
        if problem.options.request_geometry and (
            _distance_m(route.geometry[0], route.steps[0].location) > max_geometry_gap_m
            or _distance_m(route.geometry[-1], route.steps[-1].location) > max_geometry_gap_m
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
        if (
            vehicle.max_route_distance_meters is not None
            and route.totals.distance_meters > vehicle.max_route_distance_meters
        ):
            raise SolverResponseError("Solver exceeded vehicle route distance")
        if (
            vehicle.max_driving_seconds is not None
            and route.totals.driving_seconds > vehicle.max_driving_seconds
        ):
            raise SolverResponseError("Solver exceeded vehicle driving time")
        if vehicle.max_delivery_tasks is not None and sum(
            step.kind == StepKind.DELIVERY for step in route.steps
        ) > vehicle.max_delivery_tasks:
            raise SolverResponseError("Solver exceeded vehicle delivery tasks")
        _reconcile_route_facts(route, assigned, tasks, problem.options.request_geometry)
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
    for field in (
        "distance_meters",
        "driving_seconds",
        "service_seconds",
        "waiting_seconds",
        "total_duration_seconds",
        "objective_cost_units",
    ):
        if getattr(result.summary, field) != sum(
            getattr(route.totals, field) for route in result.routes
        ):
            raise SolverResponseError("Solver summary metrics do not match route facts")
    if problem.vehicles and (
        result.summary.cost_scale != problem.vehicles[0].costs.scale
        or result.summary.currency != problem.vehicles[0].costs.currency
    ):
        raise SolverResponseError("Solver cost units do not match the submitted rates")
