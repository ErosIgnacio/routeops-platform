from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from routeops.application.planning import DemoPlanningService
from routeops.application.processing_times import AttemptTimer
from routeops.domain.models import Coordinate
from routeops.domain.optimization import (
    OptimizationProblem,
    OptimizationResult,
    OptimizationSummary,
    OptimizedRoute,
    ResultStatus,
    RouteTotals,
    SolverMetadata,
)
from routeops.domain.policies.allocation import DeterministicAllocationPolicy
from routeops.infrastructure.data import SyntheticScenarioLoader


class ConstantTravelTimes:
    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int:
        return 300


class StubSolver:
    problem: OptimizationProblem | None = None

    def solve(self, problem: OptimizationProblem) -> OptimizationResult:
        self.problem = problem
        from datetime import timedelta

        from routeops.domain.models import Capacity
        from routeops.domain.optimization import RouteStep, StepKind

        routes = []
        for center in sorted({task.distribution_center_id for task in problem.tasks}):
            tasks = [task for task in problem.tasks if task.distribution_center_id == center]
            vehicle = next(
                v
                for v in problem.vehicles
                if v.distribution_center_id == center
                and all(t.required_skills <= v.skills for t in tasks)
            )
            load = [sum(t.demand.as_vroom_array()[i] for t in tasks) for i in range(3)]
            departure = vehicle.shift_start
            steps = [
                RouteStep(
                    0,
                    StepKind.START,
                    None,
                    None,
                    vehicle.start,
                    departure,
                    departure,
                    departure,
                    0,
                    0,
                    0,
                    0,
                    Capacity(*load),
                    "NOT_APPLICABLE",
                )
            ]
            for i, task in enumerate(tasks, 1):
                arrival = departure + timedelta(seconds=300)
                service_start = max(arrival, task.time_window_start)
                waiting = int((service_start - arrival).total_seconds())
                departure = service_start + timedelta(seconds=task.service_seconds)
                load = [
                    left - used
                    for left, used in zip(load, task.demand.as_vroom_array(), strict=True)
                ]
                steps.append(
                    RouteStep(
                        i,
                        StepKind.DELIVERY,
                        task.task_id,
                        task.order_id,
                        task.location,
                        arrival,
                        service_start,
                        departure,
                        300,
                        1000,
                        waiting,
                        task.service_seconds,
                        Capacity(*load),
                        "EARLY_WAIT" if waiting else "ON_TIME",
                    )
                )
            end = departure + timedelta(seconds=300)
            steps.append(
                RouteStep(
                    len(steps),
                    StepKind.END,
                    None,
                    None,
                    vehicle.end,
                    end,
                    end,
                    end,
                    300,
                    1000,
                    0,
                    0,
                    Capacity(*load),
                    "NOT_APPLICABLE",
                )
            )
            totals = RouteTotals(
                (len(tasks) + 1) * 1000,
                (len(tasks) + 1) * 300,
                sum(t.service_seconds for t in tasks),
                sum(step.waiting_seconds for step in steps),
                int((end - vehicle.shift_start).total_seconds()),
                2500,
            )
            routes.append(
                OptimizedRoute(
                    vehicle.vehicle_id,
                    vehicle.source_vehicle_id,
                    center,
                    tuple(steps),
                    tuple(step.location for step in steps),
                    totals,
                )
            )
        return OptimizationResult(
            contract_version=problem.contract_version,
            problem_id=problem.problem_id,
            status=ResultStatus.SUCCEEDED,
            solver=SolverMetadata("stub", "1", "1", "stub", "1", 25),
            summary=OptimizationSummary(
                len(routes),
                len(problem.tasks),
                0,
                *(
                    sum(getattr(route.totals, field) for route in routes)
                    for field in (
                        "distance_meters",
                        "driving_seconds",
                        "service_seconds",
                        "waiting_seconds",
                        "total_duration_seconds",
                        "objective_cost_units",
                    )
                ),
                100,
                "CLP",
            ),
            routes=tuple(routes),
            unassigned=(),
        )


class MemoryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, dict[str, Any]] = {}

    def start(
        self, run_id: UUID, scenario_name: str, started_at: datetime, input_data: dict[str, Any]
    ) -> None:
        self.runs[run_id] = {
            "run_id": str(run_id),
            "scenario_name": scenario_name,
            "status": "RUNNING",
            "started_at": started_at.isoformat(),
            "completed_at": None,
            "input": input_data,
            "result": None,
            "kpis": None,
            "error": None,
        }

    def complete(
        self,
        run_id: UUID,
        completed_at: datetime,
        result: dict[str, Any],
        kpis: dict[str, Any],
        *,
        timer: AttemptTimer | None = None,
        timing_token: UUID | None = None,
    ) -> None:
        self.runs[run_id].update(
            status=result["status"],
            completed_at=completed_at.isoformat(),
            result=result,
            kpis=kpis,
        )

    def record_timing(self, run_id: UUID, token: UUID, event: dict[str, Any]) -> None:
        self.runs[run_id].setdefault("timings", []).append(event)

    def fail(
        self,
        run_id: UUID,
        completed_at: datetime,
        error: str,
        *,
        timer: AttemptTimer | None = None,
        timing_token: UUID | None = None,
    ) -> None:
        self.runs[run_id].update(
            status="FAILED", completed_at=completed_at.isoformat(), error=error
        )

    def get(self, run_id: UUID) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def latest(self) -> dict[str, Any] | None:
        return next(reversed(self.runs.values()), None)


def test_demo_service_merges_allocation_exceptions_and_derives_kpis() -> None:
    dataset = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "demo.json"
    solver = StubSolver()
    repository = MemoryRunRepository()
    service = DemoPlanningService(
        SyntheticScenarioLoader(dataset),
        DeterministicAllocationPolicy(ConstantTravelTimes()),
        solver,
        repository,
        solver_timeout_seconds=15,
        map_dataset_sha256="test-map-sha256",
    )

    run = service.run()

    assert run["status"] == "PARTIAL"
    assert run["kpis"]["assigned_orders"] == 4
    assert run["kpis"]["unassigned_orders"] == 1
    assert run["kpis"]["routes"] == 2
    assert run["kpis"]["vehicles_used"] == 2
    assert run["kpis"]["distance_km"] == 6.0
    assert run["kpis"]["driving_hours"] == 0.5
    assert run["kpis"]["service_hours"] == 0.75
    assert run["kpis"]["solver_time_ms"] == 25
    assert run["kpis"]["estimated_cost"] == 50.0
    assert run["result"]["unassigned"][0]["order_id"] == "ORD-003"
    assert run["input"]["map_dataset_sha256"] == "test-map-sha256"
    assert solver.problem is not None
    assert all(vehicle.start == vehicle.end for vehicle in solver.problem.vehicles)
