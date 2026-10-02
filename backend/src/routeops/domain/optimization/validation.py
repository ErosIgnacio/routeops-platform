"""Executable v1 invariants, independent of the chosen solver."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from routeops.domain.optimization.contracts import OptimizationProblem

INTEGER_MAX = 2_147_483_647


def integer(value: int, name: str, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= INTEGER_MAX:
        raise ValueError(f"{name} must be an integer in [{minimum}, {INTEGER_MAX}]")


def instant(value: datetime) -> datetime:
    if value.utcoffset() is None or value.microsecond:
        raise ValueError("Optimization instants require an offset and whole seconds")
    return value.astimezone(UTC)


def validate_problem(problem: OptimizationProblem) -> None:
    if problem.contract_version not in {"1.0", "1.1"}:
        raise ValueError("Unsupported optimization contract")
    ZoneInfo(problem.timezone)
    start, end = instant(problem.horizon_start), instant(problem.horizon_end)
    integer(int((end - start).total_seconds()), "horizon duration", minimum=1)
    integer(problem.options.timeout_seconds, "timeout", minimum=1)
    for items, key in ((problem.tasks, "task_id"), (problem.vehicles, "vehicle_id")):
        if len({getattr(item, key) for item in items}) != len(items):
            raise ValueError("Optimization identifiers must be unique")
    if len({task.order_id for task in problem.tasks}) != len(problem.tasks):
        raise ValueError("An order must be represented by one complete task")
    if len({vehicle.source_vehicle_id for vehicle in problem.vehicles}) != len(problem.vehicles):
        raise ValueError("Vehicle business identifiers must be unique")
    if len({(v.costs.currency, v.costs.scale) for v in problem.vehicles}) > 1:
        raise ValueError("All vehicles require the same currency and cost scale")
    for task in problem.tasks:
        integer(task.priority, "priority")
        if task.priority > 100:
            raise ValueError("Priority must be in [0, 100]")
        integer(task.service_seconds, "service")
        for value in task.demand.as_vroom_array():
            integer(value, "demand")
        left, right = instant(task.time_window_start), instant(task.time_window_end)
        if not start <= left < right <= end:
            raise ValueError("Order window must be within the horizon")
    for vehicle in problem.vehicles:
        limits = (
            vehicle.max_route_distance_meters,
            vehicle.max_driving_seconds,
            vehicle.max_delivery_tasks,
        )
        if problem.contract_version == "1.0" and any(value is not None for value in limits):
            raise ValueError("Vehicle limits require optimization contract 1.1")
        for name, optional_value in zip(
            ("route distance", "driving time", "delivery tasks"), limits, strict=True
        ):
            if optional_value is not None:
                integer(optional_value, name, minimum=1)
        left, right = instant(vehicle.shift_start), instant(vehicle.shift_end)
        if not start <= left < right <= end:
            raise ValueError("Vehicle shift must be within the horizon")
        if vehicle.start != vehicle.end:
            raise ValueError("V1 requires closed routes at the allocated center")
        for value in vehicle.capacity.as_vroom_array():
            integer(value, "capacity", minimum=1)
        costs = vehicle.costs
        integer(costs.scale, "cost scale", minimum=1)
        for value in (costs.fixed_units, costs.per_duty_hour_units, costs.per_km_units):
            integer(value, "cost rate")
        if (
            len(costs.currency) != 3
            or not costs.currency.isascii()
            or not (costs.currency.isalpha() and costs.currency.isupper())
        ):
            raise ValueError("Currency must be an uppercase three-letter code")
