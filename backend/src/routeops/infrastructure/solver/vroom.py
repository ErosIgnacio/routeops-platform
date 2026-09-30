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
from routeops.infrastructure.routing.polyline import decode_polyline
from routeops.infrastructure.solver.errors import (
    SolverDependencyError,
    SolverInputError,
    SolverResponseError,
)


class VroomAdapter:
    """Translate stable RouteOps contracts to the VROOM HTTP boundary."""

    adapter_version = "1.0.0"
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
        return self._from_vroom(
            problem,
            response_payload,
            task_ids,
            vehicle_ids,
            elapsed_ms,
        )

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
                try:
                    code = int(result.get("code", -1))
                except (TypeError, ValueError) as exc:
                    raise SolverResponseError("VROOM response code must be an integer") from exc
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
            {skill for task in tasks for skill in task.required_skills}
            | {skill for vehicle in vehicles for skill in vehicle.skills}
            | {self._center_skill(task.distribution_center_id) for task in tasks}
            | {self._center_skill(vehicle.distribution_center_id) for vehicle in vehicles}
        )
        skill_ids = {skill: index for index, skill in enumerate(skills, start=1)}

        jobs: list[dict[str, Any]] = []
        for task in tasks:
            task_skills = set(task.required_skills)
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
            vehicle_skills = set(vehicle.skills)
            vehicle_skills.add(self._center_skill(vehicle.distribution_center_id))
            vroom_vehicles.append(
                {
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
            )
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
                if raw_type not in {"start", "job", "end", "break"}:
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
                    job_key = self._as_int(raw_step.get("job"), "step.job")
                    task_id = task_ids.get(job_key)
                    if task_id is None or task_id in seen_tasks:
                        raise SolverResponseError("VROOM returned an unknown or duplicate task")
                    task = task_by_id[task_id]
                    if task.distribution_center_id != vehicle.distribution_center_id:
                        raise SolverResponseError("VROOM crossed an allocated center boundary")
                    seen_tasks.add(task_id)
                    order_id = task.order_id

                location = self._coordinate(raw_step.get("location"))
                cumulative_duration = self._as_int(raw_step.get("duration", 0), "step.duration")
                cumulative_distance = self._as_int(raw_step.get("distance", 0), "step.distance")
                travel_seconds = max(0, cumulative_duration - previous_duration)
                distance_meters = max(0, cumulative_distance - previous_distance)
                previous_duration = cumulative_duration
                previous_distance = cumulative_distance
                arrival_offset = self._as_int(raw_step.get("arrival", 0), "step.arrival")
                waiting_seconds = self._as_int(
                    raw_step.get("waiting_time", raw_step.get("waiting", 0)),
                    "step.waiting_time",
                )
                service_seconds = self._as_int(raw_step.get("service", 0), "step.service")
                arrival = problem.horizon_start + timedelta(seconds=arrival_offset)
                service_start = arrival + timedelta(seconds=waiting_seconds)
                departure = service_start + timedelta(seconds=service_seconds)
                load = raw_step.get("load", [0, 0, 0])
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
                distance_meters=self._as_int(raw_route.get("distance", 0), "route.distance"),
                driving_seconds=self._as_int(raw_route.get("duration", 0), "route.duration"),
                service_seconds=self._as_int(raw_route.get("service", 0), "route.service"),
                waiting_seconds=self._as_int(
                    raw_route.get("waiting_time", 0), "route.waiting_time"
                ),
                total_duration_seconds=self._as_int(raw_route.get("duration", 0), "route.duration")
                + self._as_int(raw_route.get("service", 0), "route.service")
                + self._as_int(raw_route.get("waiting_time", 0), "route.waiting_time"),
                objective_cost_units=self._as_int(raw_route.get("cost", 0), "route.cost"),
            )
            geometry_value = raw_route.get("geometry")
            geometry = decode_polyline(geometry_value) if isinstance(geometry_value, str) else ()
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
    def _center_skill(center_id: str) -> str:
        return f"__routeops_center__:{center_id}"

    @staticmethod
    def _relative_seconds(problem: OptimizationProblem, value: datetime) -> int:
        if value.tzinfo is None:
            raise SolverInputError("All optimization timestamps must be timezone-aware")
        offset = int((value - problem.horizon_start).total_seconds())
        if offset < 0:
            raise SolverInputError("Optimization timestamp precedes the planning horizon")
        return offset

    @staticmethod
    def _coordinate(value: Any) -> Coordinate:
        if not isinstance(value, list) or len(value) != 2:
            raise SolverResponseError("VROOM location must be [longitude, latitude]")
        return Coordinate(latitude=float(value[1]), longitude=float(value[0]))

    @staticmethod
    def _as_list(value: Any, field: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise SolverResponseError(f"VROOM {field} must be a list of objects")
        return value

    @staticmethod
    def _as_int(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise SolverResponseError(f"VROOM {field} must be an integer")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise SolverResponseError(f"VROOM {field} must be an integer") from exc
        if result < 0:
            raise SolverResponseError(f"VROOM {field} cannot be negative")
        return result

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
