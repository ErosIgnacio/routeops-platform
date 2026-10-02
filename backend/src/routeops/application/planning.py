from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from routeops.application.diagnostics import diagnose_result
from routeops.application.optimization_reconciliation import reconcile_result
from routeops.application.ports.errors import operational_failure_code
from routeops.application.ports.gateways import RunRepository, SolverGateway
from routeops.application.processing_times import AttemptTimer
from routeops.application.serialization import to_primitive
from routeops.domain.models import ScenarioData
from routeops.domain.optimization import (
    DeliveryTask,
    OptimizationOptions,
    OptimizationProblem,
    OptimizationResult,
    OptimizationSummary,
    OptimizationVehicle,
    ResultStatus,
    SolutionQuality,
    UnassignedTask,
    VehicleCost,
)
from routeops.domain.policies.allocation import AllocationDecision, DeterministicAllocationPolicy
from routeops.infrastructure.data import SyntheticScenarioLoader


class DemoPlanningService:
    cost_scale = 100

    def __init__(
        self,
        loader: SyntheticScenarioLoader,
        allocation: DeterministicAllocationPolicy,
        solver: SolverGateway,
        repository: RunRepository,
        solver_timeout_seconds: int,
        map_dataset_sha256: str,
    ) -> None:
        self._loader = loader
        self._allocation = allocation
        self._solver = solver
        self._repository = repository
        self._solver_timeout_seconds = solver_timeout_seconds
        self._map_dataset_sha256 = map_dataset_sha256

    def run(self, quality: SolutionQuality = SolutionQuality.BALANCED) -> dict[str, object]:
        run_id = uuid4()
        started_at = datetime.now(UTC)
        scenario = self._loader.load()
        self._repository.start(
            run_id,
            scenario.name,
            started_at,
            {
                "dataset_name": scenario.dataset_name,
                "dataset_seed": scenario.dataset_seed,
                "synthetic": scenario.synthetic,
                "solution_quality": quality.value,
                "allocation_policy": DeterministicAllocationPolicy.version,
                "map_dataset_sha256": self._map_dataset_sha256,
                "metric_input_snapshot": {
                    "orders": {order.id: [order.time_window_start.isoformat(),
                                          order.time_window_end.isoformat()]
                               for order in scenario.orders},
                    "capacities": {vehicle.id: to_primitive(vehicle.capacity)
                                   for vehicle in scenario.vehicles},
                },
                "operating_cost_rates": {
                    "currency": scenario.currency,
                    "vehicles": {
                        vehicle.id: {"fixed": str(vehicle.fixed_cost),
                                     "per_duty_hour": str(vehicle.cost_per_hour),
                                     "per_km": str(vehicle.cost_per_km)}
                        for vehicle in scenario.vehicles
                    },
                },
            },
        )
        token = uuid4()
        self._repository.record_timing(run_id, token, {
            "kind": "ATTEMPT_STARTED", "occurred_at": datetime.now(UTC),
        })
        timer = AttemptTimer(lambda event: self._repository.record_timing(run_id, token, event))
        try:
            with timer.phase("ALLOCATION_OSRM_DEMO"):
                allocation = self._allocation.allocate(scenario)
            with timer.phase("PREPARE_SOLVER"):
                problem = self._build_problem(run_id, scenario, allocation.allocated, quality)
            with timer.phase("SOLVER"):
                solver_result = self._solver.solve(problem)
            with timer.phase("RECONCILE"):
                reconcile_result(problem, solver_result)
                result = self._merge_unassigned(solver_result, allocation.unassigned)
                result = diagnose_result(result, problem, {}, {
                    "run_id": str(run_id), "source": "original_demo_decision_evidence",
                    "dataset_name": scenario.dataset_name, "dataset_seed": scenario.dataset_seed,
                    "policy_version": self._allocation.version,
                    "map_dataset_sha256": self._map_dataset_sha256,
                })
            completed_at = datetime.now(UTC)
            kpis = self._kpis(result)
            self._repository.complete(
                run_id,
                completed_at,
                to_primitive(result),
                kpis,
                timer=timer, timing_token=token,
            )
        except Exception as exc:
            self._repository.fail(run_id, datetime.now(UTC), operational_failure_code(exc),
                                  timer=timer, timing_token=token)
            raise
        persisted = self._repository.get(run_id)
        if persisted is None:
            raise RuntimeError("completed planning run was not persisted")
        return persisted

    def _build_problem(
        self,
        run_id: UUID,
        scenario: ScenarioData,
        decisions: tuple[AllocationDecision, ...],
        quality: SolutionQuality,
    ) -> OptimizationProblem:
        scenario_key = f"routeops:{scenario.dataset_name}:{scenario.dataset_seed}"
        scenario_id = uuid5(NAMESPACE_URL, scenario_key)
        tasks = tuple(
            DeliveryTask(
                task_id=uuid5(scenario_id, decision.order.id),
                order_id=decision.order.id,
                distribution_center_id=decision.distribution_center.id,
                location=decision.order.location,
                demand=decision.order.demand,
                service_seconds=decision.order.service_seconds,
                time_window_start=decision.order.time_window_start,
                time_window_end=decision.order.time_window_end,
                priority=decision.order.priority,
                required_skills=decision.order.required_skills,
            )
            for decision in decisions
        )
        centers = {center.id: center for center in scenario.centers}
        vehicles = tuple(
            OptimizationVehicle(
                vehicle_id=uuid5(scenario_id, vehicle.id),
                source_vehicle_id=vehicle.id,
                distribution_center_id=vehicle.distribution_center_id,
                vehicle_type=vehicle.vehicle_type,
                start=centers[vehicle.distribution_center_id].location,
                end=centers[vehicle.distribution_center_id].location,
                shift_start=vehicle.shift_start,
                shift_end=vehicle.shift_end,
                capacity=vehicle.capacity,
                skills=vehicle.skills,
                costs=VehicleCost(
                    currency=scenario.currency,
                    scale=self.cost_scale,
                    fixed_units=self._cost_units(vehicle.fixed_cost),
                    per_duty_hour_units=self._cost_units(vehicle.cost_per_hour),
                    per_km_units=self._cost_units(vehicle.cost_per_km),
                ),
            )
            for vehicle in scenario.vehicles
        )
        return OptimizationProblem(
            contract_version="1.0",
            problem_id=run_id,
            scenario_id=scenario_id,
            horizon_start=scenario.horizon_start,
            horizon_end=scenario.horizon_end,
            timezone=scenario.timezone,
            tasks=tasks,
            vehicles=vehicles,
            options=OptimizationOptions(
                timeout_seconds=self._solver_timeout_seconds,
                solution_quality=quality,
                request_geometry=True,
            ),
        )

    def _cost_units(self, value: Decimal) -> int:
        scaled = value * self.cost_scale
        if scaled != scaled.to_integral_value():
            raise ValueError(f"cost {value} exceeds configured precision")
        return int(scaled)

    @staticmethod
    def _merge_unassigned(
        solver_result: OptimizationResult,
        allocation_unassigned: tuple[UnassignedTask, ...],
    ) -> OptimizationResult:
        all_unassigned = solver_result.unassigned + allocation_unassigned
        summary = replace(
            solver_result.summary,
            unassigned_task_count=len(all_unassigned),
        )
        return replace(
            solver_result,
            status=ResultStatus.PARTIAL if all_unassigned else ResultStatus.SUCCEEDED,
            summary=summary,
            unassigned=all_unassigned,
        )

    @staticmethod
    def _kpis(result: OptimizationResult) -> dict[str, object]:
        summary: OptimizationSummary = result.summary
        return {
            "status": result.status.value,
            "routes": summary.route_count,
            "vehicles_used": len({route.vehicle_id for route in result.routes}),
            "assigned_orders": summary.assigned_task_count,
            "unassigned_orders": summary.unassigned_task_count,
            "distance_km": round(summary.distance_meters / 1000, 3),
            "driving_hours": round(summary.driving_seconds / 3600, 3),
            "service_hours": round(summary.service_seconds / 3600, 3),
            "waiting_hours": round(summary.waiting_seconds / 3600, 3),
            "total_hours": round(summary.total_duration_seconds / 3600, 3),
            "solver_time_ms": result.solver.solve_duration_ms,
            "estimated_cost": round(summary.objective_cost_units / summary.cost_scale, 2),
            "currency": summary.currency,
        }
