from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from routeops.application.planning import DemoPlanningService
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
        totals = RouteTotals(10_000, 1800, 900, 0, 2700, 2500)
        route = OptimizedRoute(
            vehicle_id=problem.vehicles[0].vehicle_id,
            source_vehicle_id=problem.vehicles[0].source_vehicle_id,
            distribution_center_id=problem.vehicles[0].distribution_center_id,
            steps=(),
            geometry=(),
            totals=totals,
        )
        return OptimizationResult(
            contract_version=problem.contract_version,
            problem_id=problem.problem_id,
            status=ResultStatus.SUCCEEDED,
            solver=SolverMetadata("stub", "1", "1", "stub", "1", 25),
            summary=OptimizationSummary(
                route_count=1,
                assigned_task_count=len(problem.tasks),
                unassigned_task_count=0,
                distance_meters=totals.distance_meters,
                driving_seconds=totals.driving_seconds,
                service_seconds=totals.service_seconds,
                waiting_seconds=totals.waiting_seconds,
                total_duration_seconds=totals.total_duration_seconds,
                objective_cost_units=totals.objective_cost_units,
                cost_scale=100,
                currency="CLP",
            ),
            routes=(route,),
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
    ) -> None:
        self.runs[run_id].update(
            status=result["status"],
            completed_at=completed_at.isoformat(),
            result=result,
            kpis=kpis,
        )

    def fail(self, run_id: UUID, completed_at: datetime, error: str) -> None:
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
    assert run["kpis"]["routes"] == 1
    assert run["kpis"]["vehicles_used"] == 1
    assert run["kpis"]["distance_km"] == 10.0
    assert run["kpis"]["driving_hours"] == 0.5
    assert run["kpis"]["service_hours"] == 0.25
    assert run["kpis"]["waiting_hours"] == 0.0
    assert run["kpis"]["total_hours"] == 0.75
    assert run["kpis"]["solver_time_ms"] == 25
    assert run["kpis"]["estimated_cost"] == 25.0
    assert run["result"]["unassigned"][0]["order_id"] == "ORD-003"
    assert run["input"]["map_dataset_sha256"] == "test-map-sha256"
    assert solver.problem is not None
    assert all(vehicle.start == vehicle.end for vehicle in solver.problem.vehicles)
