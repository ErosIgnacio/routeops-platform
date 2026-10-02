from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

import httpx

from routeops.domain.models import Capacity, Coordinate
from routeops.domain.optimization import (
    Certainty,
    OptimizationProblem,
    OptimizationResult,
    OptimizedRoute,
    ResultStatus,
    RouteStep,
    RouteTotals,
    SolverMetadata,
    StepKind,
    UnassignedReason,
    UnassignedTask,
)
from routeops.domain.optimization.contracts import OptimizationSummary, SolutionQuality
from routeops.domain.optimization.validation import validate_problem
from routeops.infrastructure.routing.polyline import decode_polyline
from routeops.infrastructure.solver.errors import (
    SolverDependencyError,
    SolverInputError,
    SolverResponseError,
)


class VroomAdapter:
    """Translate stable RouteOps contracts to the VROOM HTTP boundary."""

    adapter_version = "1.2.0"
    engine_version = "1.15.0"
    routing_engine_version = "26.9.0"
    _exploration: ClassVar[dict[SolutionQuality, int]] = {
        SolutionQuality.FAST: 1,
        SolutionQuality.BALANCED: 3,
        SolutionQuality.THOROUGH: 5,
    }

    def __init__(
        self,
        base_url: str,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 20.0,
        max_attempts: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._max_attempts = max_attempts

    def health(self) -> dict[str, str]:
        try:
            response = httpx.get(f"{self._base_url}/health", timeout=self._timeout)
            response.raise_for_status()
            probe = httpx.post(
                self._base_url,
                json={
                    "vehicles": [
                        {
                            "id": 1,
                            "profile": "car",
                            "start": [-70.6635, -33.4445],
                            "end": [-70.6635, -33.4445],
                        }
                    ],
                    "jobs": [{"id": 1, "location": [-70.6580, -33.4420]}],
                    "options": {"g": False},
                },
                timeout=self._timeout,
            )
            probe.raise_for_status()
            payload = probe.json()
            if payload.get("code") != 0 or not payload.get("routes"):
                raise ValueError("VROOM routing probe did not return a route")
        except (httpx.HTTPError, ValueError) as exc:
            raise SolverDependencyError(f"VROOM health check failed: {exc}") from exc
        return {
            "status": "ready",
            "version": self.engine_version,
            "routing_engine": "osrm",
        }

    def solve(self, problem: OptimizationProblem) -> OptimizationResult:
        from routeops.application.optimization_reconciliation import reconcile_result

        payload, task_ids, vehicle_ids = self._to_vroom(problem)
        payload["options"] = {
            "g": problem.options.request_geometry,
            "t": 4,
            "x": self._exploration[problem.options.solution_quality],
            "l": problem.options.timeout_seconds,
        }
        started = time.perf_counter()
        response_payload = self._post(payload)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        result = self._from_vroom(
            problem,
            response_payload,
            task_ids,
            vehicle_ids,
            elapsed_ms,
        )
        reconcile_result(problem, result)
        return result

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = httpx.post(
                    self._base_url,
                    json=payload,
                    timeout=self._timeout,
                )
                if response.status_code >= 500:
                    response.raise_for_status()
                if response.status_code >= 400:
                    raise SolverInputError(
                        f"VROOM rejected the optimization problem (HTTP {response.status_code})"
                    )
                try:
                    result = response.json()
                except ValueError as exc:
                    raise SolverResponseError("VROOM returned invalid JSON") from exc
                if not isinstance(result, dict):
                    raise SolverResponseError("VROOM response must be a JSON object")
                code = self._as_int(result.get("code"), "response.code")
                if code == 0:
                    return result
                message = str(result.get("error", "unspecified error"))[:500]
                if code == 2:
                    raise SolverInputError(f"VROOM rejected the optimization problem: {message}")
                if code == 3:
                    raise SolverDependencyError(f"VROOM routing dependency failed: {message}")
                raise SolverDependencyError(f"VROOM failed with code {code}: {message}")
            except SolverInputError:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                time.sleep(0.1 * attempt)
        raise SolverDependencyError(
            f"VROOM request failed after {self._max_attempts} attempts: {last_error}"
        ) from last_error

    def _to_vroom(
        self, problem: OptimizationProblem
    ) -> tuple[dict[str, Any], dict[int, UUID], dict[int, UUID]]:
        try:
            validate_problem(problem)
        except (ValueError, KeyError) as exc:
            raise SolverInputError(str(exc)) from exc
        tasks = sorted(problem.tasks, key=lambda task: (task.order_id, str(task.task_id)))
        vehicles = sorted(
            problem.vehicles,
            key=lambda vehicle: (vehicle.source_vehicle_id, str(vehicle.vehicle_id)),
        )
        task_ids = {index: task.task_id for index, task in enumerate(tasks, start=1)}
        vehicle_ids = {index: vehicle.vehicle_id for index, vehicle in enumerate(vehicles, start=1)}
        reverse_tasks = {task_id: key for key, task_id in task_ids.items()}
        reverse_vehicles = {vehicle_id: key for key, vehicle_id in vehicle_ids.items()}

        skills = sorted(
            {("user", skill) for task in tasks for skill in task.required_skills}
            | {("user", skill) for vehicle in vehicles for skill in vehicle.skills}
            | {self._center_skill(task.distribution_center_id) for task in tasks}
            | {self._center_skill(vehicle.distribution_center_id) for vehicle in vehicles}
        )
        skill_ids = {skill: index for index, skill in enumerate(skills, start=1)}

        jobs: list[dict[str, Any]] = []
        for task in tasks:
            task_skills = {("user", skill) for skill in task.required_skills}
            task_skills.add(self._center_skill(task.distribution_center_id))
            jobs.append(
                {
                    "id": reverse_tasks[task.task_id],
                    "description": task.order_id,
                    "location": [task.location.longitude, task.location.latitude],
                    "delivery": task.demand.as_vroom_array(),
                    "service": task.service_seconds,
                    "time_windows": [
                        [
                            self._relative_seconds(problem, task.time_window_start),
                            self._relative_seconds(problem, task.time_window_end),
                        ]
                    ],
                    "priority": task.priority,
                    "skills": sorted(skill_ids[value] for value in task_skills),
                }
            )

        vroom_vehicles: list[dict[str, Any]] = []
        for vehicle in vehicles:
            vehicle_skills = {("user", skill) for skill in vehicle.skills}
            vehicle_skills.add(self._center_skill(vehicle.distribution_center_id))
            vehicle_payload: dict[str, Any] = {
                    "id": reverse_vehicles[vehicle.vehicle_id],
                    "description": vehicle.source_vehicle_id,
                    "profile": "car",
                    "start": [vehicle.start.longitude, vehicle.start.latitude],
                    "end": [vehicle.end.longitude, vehicle.end.latitude],
                    "capacity": vehicle.capacity.as_vroom_array(),
                    "time_window": [
                        self._relative_seconds(problem, vehicle.shift_start),
                        self._relative_seconds(problem, vehicle.shift_end),
                    ],
                    "skills": sorted(skill_ids[value] for value in vehicle_skills),
                    "costs": {
                        "fixed": vehicle.costs.fixed_units,
                        "per_hour": vehicle.costs.per_duty_hour_units,
                        "per_km": vehicle.costs.per_km_units,
                    },
                }
            if vehicle.max_route_distance_meters is not None:
                vehicle_payload["max_distance"] = vehicle.max_route_distance_meters
            if vehicle.max_driving_seconds is not None:
                vehicle_payload["max_travel_time"] = vehicle.max_driving_seconds
            if vehicle.max_delivery_tasks is not None:
                vehicle_payload["max_tasks"] = vehicle.max_delivery_tasks
            vroom_vehicles.append(vehicle_payload)
        return {"jobs": jobs, "vehicles": vroom_vehicles}, task_ids, vehicle_ids

    def _from_vroom(
        self,
        problem: OptimizationProblem,
        payload: dict[str, Any],
        task_ids: dict[int, UUID],
        vehicle_ids: dict[int, UUID],
        elapsed_ms: int,
    ) -> OptimizationResult:
        task_by_id = {task.task_id: task for task in problem.tasks}
        vehicle_by_id = {vehicle.vehicle_id: vehicle for vehicle in problem.vehicles}
        seen_tasks: set[UUID] = set()
        seen_vehicles: set[UUID] = set()
        routes: list[OptimizedRoute] = []

        for raw_route in self._as_list(payload.get("routes"), "routes"):
            if raw_route.get("setup", 0) != 0 or raw_route.get("violations", []):
                raise SolverResponseError("VROOM returned setup or violations outside v1")
            vehicle_key = self._as_int(raw_route.get("vehicle"), "route.vehicle")
            vehicle_id = vehicle_ids.get(vehicle_key)
            if vehicle_id is None or vehicle_id in seen_vehicles:
                raise SolverResponseError("VROOM returned an unknown or duplicate vehicle")
            seen_vehicles.add(vehicle_id)
            vehicle = vehicle_by_id[vehicle_id]
            previous_duration = 0
            previous_distance = 0
            steps: list[RouteStep] = []

            for sequence, raw_step in enumerate(
                self._as_list(raw_route.get("steps"), "route.steps")
            ):
                raw_type = str(raw_step.get("type", ""))
                if raw_type not in {"start", "job", "end"}:
                    raise SolverResponseError(f"Unknown VROOM step type: {raw_type}")
                kind = {
                    "start": StepKind.START,
                    "job": StepKind.DELIVERY,
                    "end": StepKind.END,
                    "break": StepKind.BREAK,
                }[raw_type]
                task_id: UUID | None = None
                order_id: str | None = None
                if kind is StepKind.DELIVERY:
                    job_key = self._as_int(raw_step.get("id", raw_step.get("job")), "step.id")
                    task_id = task_ids.get(job_key)
                    if task_id is None or task_id in seen_tasks:
                        raise SolverResponseError("VROOM returned an unknown or duplicate task")
                    task = task_by_id[task_id]
                    if task.distribution_center_id != vehicle.distribution_center_id:
                        raise SolverResponseError("VROOM crossed an allocated center boundary")
                    seen_tasks.add(task_id)
                    order_id = task.order_id

                location = self._coordinate(raw_step.get("location"))
                cumulative_duration = self._as_int(raw_step.get("duration"), "step.duration")
                cumulative_distance = self._as_int(raw_step.get("distance", 0), "step.distance")
                if (
                    cumulative_duration < previous_duration
                    or cumulative_distance < previous_distance
                ):
                    raise SolverResponseError("VROOM cumulative metrics cannot decrease")
                if raw_step.get("setup", 0) != 0 or raw_step.get("violations", []):
                    raise SolverResponseError("VROOM returned setup or violations outside v1")
                travel_seconds = cumulative_duration - previous_duration
                distance_meters = cumulative_distance - previous_distance
                previous_duration = cumulative_duration
                previous_distance = cumulative_distance
                arrival_offset = self._as_int(raw_step.get("arrival"), "step.arrival")
                waiting_seconds = self._as_int(
                    raw_step.get("waiting_time", raw_step.get("waiting", 0)),
                    "step.waiting_time",
                )
                service_seconds = self._as_int(raw_step.get("service", 0), "step.service")
                horizon_seconds = int(
                    (
                        problem.horizon_end.astimezone(UTC) - problem.horizon_start.astimezone(UTC)
                    ).total_seconds()
                )
                if arrival_offset > horizon_seconds or (
                    arrival_offset + waiting_seconds + service_seconds > horizon_seconds
                ):
                    raise SolverResponseError("VROOM returned a step outside the horizon")
                arrival = problem.horizon_start.astimezone(UTC) + timedelta(seconds=arrival_offset)
                service_start = arrival + timedelta(seconds=waiting_seconds)
                departure = service_start + timedelta(seconds=service_seconds)
                load = raw_step.get("load")
                if not isinstance(load, list) or len(load) != 3:
                    raise SolverResponseError("VROOM step.load must have three dimensions")
                step_task = task_by_id.get(task_id) if task_id is not None else None
                steps.append(
                    RouteStep(
                        sequence=sequence,
                        kind=kind,
                        task_id=task_id,
                        order_id=order_id,
                        location=location,
                        arrival_at=arrival,
                        service_start_at=service_start,
                        departure_at=departure,
                        travel_seconds_from_previous=travel_seconds,
                        distance_meters_from_previous=distance_meters,
                        waiting_seconds=waiting_seconds,
                        service_seconds=service_seconds,
                        load_after=Capacity(*[self._as_int(item, "step.load") for item in load]),
                        time_window_status=self._time_window_status(
                            step_task, arrival, waiting_seconds
                        ),
                    )
                )

            totals = RouteTotals(
                distance_meters=self._as_int(raw_route.get("distance"), "route.distance"),
                driving_seconds=self._as_int(raw_route.get("duration"), "route.duration"),
                service_seconds=self._as_int(raw_route.get("service"), "route.service"),
                waiting_seconds=self._as_int(
                    raw_route.get("waiting_time"), "route.waiting_time"
                ),
                total_duration_seconds=self._as_int(raw_route.get("duration"), "route.duration")
                + self._as_int(raw_route.get("service"), "route.service")
                + self._as_int(raw_route.get("waiting_time"), "route.waiting_time"),
                objective_cost_units=self._as_int(raw_route.get("cost"), "route.cost"),
            )
            geometry_value = raw_route.get("geometry")
            try:
                geometry = (
                    decode_polyline(geometry_value) if isinstance(geometry_value, str) else ()
                )
            except (ValueError, OverflowError) as exc:
                raise SolverResponseError("VROOM route geometry is invalid") from exc
            routes.append(
                OptimizedRoute(
                    vehicle_id=vehicle_id,
                    source_vehicle_id=vehicle.source_vehicle_id,
                    distribution_center_id=vehicle.distribution_center_id,
                    steps=tuple(steps),
                    geometry=geometry,
                    totals=totals,
                )
            )

        unassigned: list[UnassignedTask] = []
        for raw_job in self._as_list(payload.get("unassigned", []), "unassigned"):
            job_key = self._as_int(raw_job.get("id"), "unassigned.id")
            task_id = task_ids.get(job_key)
            if task_id is None or task_id in seen_tasks:
                raise SolverResponseError("VROOM returned an unknown or duplicate unassigned task")
            seen_tasks.add(task_id)
            task = task_by_id[task_id]
            unassigned.append(
                UnassignedTask(
                    task_id=task_id,
                    order_id=task.order_id,
                    stage="OPTIMIZATION",
                    reasons=(
                        UnassignedReason(
                            code="SOLVER_NO_FEASIBLE_ROUTE",
                            certainty=Certainty.INFERRED,
                            detail="The solver did not include the task in a feasible route.",
                            evidence={},
                        ),
                    ),
                )
            )

        if seen_tasks != set(task_ids.values()):
            raise SolverResponseError("VROOM response does not account for every submitted task")

        summary = OptimizationSummary(
            route_count=len(routes),
            assigned_task_count=len(seen_tasks) - len(unassigned),
            unassigned_task_count=len(unassigned),
            distance_meters=sum(route.totals.distance_meters for route in routes),
            driving_seconds=sum(route.totals.driving_seconds for route in routes),
            service_seconds=sum(route.totals.service_seconds for route in routes),
            waiting_seconds=sum(route.totals.waiting_seconds for route in routes),
            total_duration_seconds=sum(route.totals.total_duration_seconds for route in routes),
            objective_cost_units=sum(route.totals.objective_cost_units for route in routes),
            cost_scale=problem.vehicles[0].costs.scale if problem.vehicles else 1,
            currency=problem.vehicles[0].costs.currency if problem.vehicles else "CLP",
        )
        raw_summary = payload.get("summary")
        if not isinstance(raw_summary, dict):
            raise SolverResponseError("VROOM summary must be an object")
        for key, expected in (
            ("routes", summary.route_count),
            ("unassigned", summary.unassigned_task_count),
            ("distance", summary.distance_meters),
            ("duration", summary.driving_seconds),
            ("service", summary.service_seconds),
            ("waiting_time", summary.waiting_seconds),
            ("cost", summary.objective_cost_units),
        ):
            if self._as_int(raw_summary.get(key), f"summary.{key}") != expected:
                raise SolverResponseError("VROOM summary does not match its routes")
        if raw_summary.get("setup", 0) != 0 or raw_summary.get("violations", []):
            raise SolverResponseError("VROOM summary contains setup or violations outside v1")
        return OptimizationResult(
            contract_version=problem.contract_version,
            problem_id=problem.problem_id,
            status=ResultStatus.PARTIAL if unassigned else ResultStatus.SUCCEEDED,
            solver=SolverMetadata(
                engine="vroom",
                engine_version=self.engine_version,
                adapter_version=self.adapter_version,
                routing_engine="osrm",
                routing_engine_version=self.routing_engine_version,
                solve_duration_ms=elapsed_ms,
            ),
            summary=summary,
            routes=tuple(routes),
            unassigned=tuple(unassigned),
        )

    @staticmethod
    def _center_skill(center_id: str) -> tuple[str, str]:
        return ("center", center_id)

    @staticmethod
    def _relative_seconds(problem: OptimizationProblem, value: datetime) -> int:
        if value.tzinfo is None:
            raise SolverInputError("All optimization timestamps must be timezone-aware")
        offset = int(
            (value.astimezone(UTC) - problem.horizon_start.astimezone(UTC)).total_seconds()
        )
        if offset < 0:
            raise SolverInputError("Optimization timestamp precedes the planning horizon")
        return offset

    @staticmethod
    def _coordinate(value: Any) -> Coordinate:
        if not isinstance(value, list) or len(value) != 2:
            raise SolverResponseError("VROOM location must be [longitude, latitude]")
        try:
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
                raise ValueError("nonnumeric coordinate")
            return Coordinate(latitude=float(value[1]), longitude=float(value[0]))
        except (ValueError, TypeError, OverflowError) as exc:
            raise SolverResponseError("VROOM location is not a finite valid coordinate") from exc

    @staticmethod
    def _as_list(value: Any, field: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise SolverResponseError(f"VROOM {field} must be a list of objects")
        return value

    @staticmethod
    def _as_int(value: Any, field: str) -> int:
        if type(value) is not int:
            raise SolverResponseError(f"VROOM {field} must be an integer")
        if value < 0 or value > 18_446_744_073_709_551_615:
            raise SolverResponseError(f"VROOM {field} must be an unsigned 64-bit integer")
        return value

    @staticmethod
    def _time_window_status(task: Any, arrival: datetime, waiting: int) -> str:
        if task is None:
            return "NOT_APPLICABLE"
        normalized = arrival.astimezone(UTC)
        if normalized > task.time_window_end.astimezone(UTC):
            return "LATE"
        if waiting > 0:
            return "EARLY_WAIT"
        return "ON_TIME"
