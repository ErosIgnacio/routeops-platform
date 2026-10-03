"""Read-only metric facts from immutable revision/result/timing records."""

from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.operating_cost import OperatingCostError, estimate_operating_cost
from routeops.application.plan_metrics import plan_metrics
from routeops.application.processing_metrics import processing_metrics
from routeops.infrastructure.persistence.models import (
    AllocationOrderDecisionModel,
    OptimizedRouteModel,
    OrderModel,
    PlanningRunModel,
    PlanningTimingEventModel,
    RevisionRunEventModel,
    RevisionRunJobModel,
    RunOrderReservationModel,
    VehicleModel,
)
from routeops.infrastructure.persistence.operating_costs import OperatingCostQuery, _hash


class PlanMetricsQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self.costs = OperatingCostQuery(sessions)

    def get(self, run_id: UUID, *, snapshot: Session | None = None) -> dict[str, Any]:
        # One repeatable snapshot prevents a simultaneous completion from mixing states.
        with (nullcontext(snapshot) if snapshot is not None else self.sessions()) as session:
            if snapshot is None:
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                raise OperatingCostError("RUN_NOT_FOUND", 404)
            job = session.get(RevisionRunJobModel, run_id)
            lifecycle = [
                {"to": row.to_status, "at": row.occurred_at}
                for row in session.scalars(
                    select(RevisionRunEventModel)
                    .where(RevisionRunEventModel.run_id == run_id)
                    .order_by(RevisionRunEventModel.sequence)
                )
            ]
            timing = [
                {
                    "attempt_no": row.attempt_no,
                    "kind": row.kind,
                    "phase": row.phase,
                    "occurred_at": row.occurred_at,
                    "duration_ns": row.duration_ns,
                    "outcome": row.outcome,
                    "details": row.details,
                }
                for row in session.scalars(
                    select(PlanningTimingEventModel)
                    .where(PlanningTimingEventModel.run_id == run_id)
                    .order_by(
                        PlanningTimingEventModel.attempt_no,
                        PlanningTimingEventModel.occurred_at,
                        PlanningTimingEventModel.id,
                    )
                )
            ]
            times = processing_metrics(
                run.started_at,
                run.completed_at,
                timing,
                lifecycle,
                synchronous=job is None,
                expected_attempts=job.attempts if job is not None else None,
            )
            base = {
                "run_id": str(run_id),
                "current_status": run.status,
                "plan_scope": "computed_plan_not_execution",
                "processing": times,
                "current_reservations": [
                    {"order_id": source_id, "status": status}
                    for source_id, status in session.execute(
                        select(OrderModel.source_id, RunOrderReservationModel.status)
                        .join(
                            AllocationOrderDecisionModel,
                            AllocationOrderDecisionModel.order_id == OrderModel.id,
                        )
                        .join(
                            RunOrderReservationModel,
                            RunOrderReservationModel.decision_id == AllocationOrderDecisionModel.id,
                        )
                        .where(RunOrderReservationModel.run_id == run_id)
                        .order_by(OrderModel.source_id)
                    )
                ]
                if job is not None
                else [],
            }
            if run.result_data is None:
                return {**base, "plan": None, "unavailable_reason": "PLAN_NOT_AVAILABLE"}
            route_rows = list(
                session.scalars(
                    select(OptimizedRouteModel)
                    .where(OptimizedRouteModel.run_id == run_id)
                    .order_by(OptimizedRouteModel.sequence)
                )
            )
            routes = [row.payload for row in route_rows]
            if routes != run.result_data["routes"] or any(
                row.distance_meters != row.payload["totals"]["distance_meters"]
                or row.total_duration_seconds != row.payload["totals"]["total_duration_seconds"]
                for row in route_rows
            ):
                raise OperatingCostError("METRIC_ROUTE_FACTS_INVALID")
            capacities: dict[str, dict[str, int]] = {}
            windows: dict[str, tuple[datetime, datetime]] = {}
            inputs: set[str] | None = None
            allocated: set[str] | None = None
            if run.scenario_revision_id is not None:
                orders = list(
                    session.scalars(
                        select(OrderModel).where(
                            OrderModel.scenario_revision_id == run.scenario_revision_id
                        )
                    )
                )
                inputs = {order.source_id for order in orders}
                windows = {
                    order.source_id: (order.time_window_start, order.time_window_end)
                    for order in orders
                }
                for vehicle in session.scalars(
                    select(VehicleModel).where(
                        VehicleModel.scenario_revision_id == run.scenario_revision_id
                    )
                ):
                    capacities[vehicle.source_id] = {
                        "units": vehicle.capacity_units,
                        "weight_grams": int(vehicle.capacity_weight_kg * Decimal(1000)),
                        "volume_cm3": int(vehicle.capacity_volume_m3 * Decimal(1_000_000)),
                    }
                if job and job.allocation_attempt_id:
                    allocated = set(
                        session.scalars(
                            select(OrderModel.source_id)
                            .join(
                                AllocationOrderDecisionModel,
                                AllocationOrderDecisionModel.order_id == OrderModel.id,
                            )
                            .where(
                                AllocationOrderDecisionModel.attempt_id
                                == job.allocation_attempt_id,
                                AllocationOrderDecisionModel.center_source_id.is_not(None),
                            )
                        )
                    )
            else:
                snapshot = run.input_data.get("metric_input_snapshot")
                if snapshot is not None:
                    inputs = set(snapshot["orders"])
                    capacities = snapshot["capacities"]
                    windows = {
                        key: (datetime.fromisoformat(value[0]), datetime.fromisoformat(value[1]))
                        for key, value in snapshot["orders"].items()
                    }
                    excluded = {
                        item["order_id"]
                        for item in run.result_data["unassigned"]
                        if item["stage"] == "ALLOCATION"
                    }
                    allocated = inputs - excluded
            cost = None
            cost_reason = None
            try:
                cost = self.costs.from_run(session, run)
            except OperatingCostError as exc:
                if exc.code != "COST_RATES_NOT_RECORDED":
                    raise
                cost_reason = exc.code
                if not routes:
                    cost = {
                        **estimate_operating_cost([], {}, run.result_data["summary"]["currency"]),
                        "solver_objective": None,
                        "provenance": {"rate_source": "no_used_vehicles"},
                    }
            calculated = plan_metrics(
                routes, inputs, allocated, capacities, windows, cost, cost_reason
            )
            summary = run.result_data["summary"]
            calculated["solver_objective"] = {
                "units": summary["objective_cost_units"],
                "unit": "scaled_currency_units",
                "scale": summary["cost_scale"],
                "currency": summary["currency"],
                "provenance": "persisted_solver_summary",
                "calculation_version": "persisted-solver-objective-v1",
                "adapter_version": run.result_data.get("solver", {}).get("adapter_version"),
                "semantics": "legacy fixed + driving + distance; not whole operating cost",
            }
            return {
                **base,
                "plan": calculated,
                "provenance": {
                    "scenario_revision_id": str(run.scenario_revision_id)
                    if run.scenario_revision_id
                    else None,
                    "input_sha256": _hash(run.input_data),
                    "result_sha256": _hash(run.result_data),
                    "route_facts_sha256": _hash(routes),
                    "timing_record_count": len(timing),
                    "input_source": "immutable_revision"
                    if run.scenario_revision_id
                    else "persisted_demo_snapshot"
                    if inputs is not None
                    else "historical_input_not_recorded",
                    "historical_derivation": not bool(timing),
                },
            }
