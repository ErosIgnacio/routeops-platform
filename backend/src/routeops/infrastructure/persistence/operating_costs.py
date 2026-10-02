"""Read-only derivation from stored route payloads and immutable rate sources."""

import hashlib
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.operating_cost import (
    OperatingCostError,
    VehicleRate,
    estimate_operating_cost,
)
from routeops.infrastructure.persistence.models import (
    OptimizedRouteModel,
    PlanningRunModel,
    ScenarioRevisionModel,
    VehicleModel,
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


class OperatingCostQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(self, run_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                raise OperatingCostError("RUN_NOT_FOUND", 404)
            if run.result_data is None:
                raise OperatingCostError("COST_RESULT_NOT_AVAILABLE")
            rows = list(
                session.scalars(
                    select(OptimizedRouteModel)
                    .where(
                        OptimizedRouteModel.run_id == run_id,
                    )
                    .order_by(OptimizedRouteModel.sequence)
                )
            )
            routes = [row.payload for row in rows]
            if routes != run.result_data["routes"] or any(
                row.distance_meters != row.payload["totals"]["distance_meters"]
                or row.total_duration_seconds != row.payload["totals"]["total_duration_seconds"]
                for row in rows
            ):
                raise OperatingCostError("COST_ROUTE_FACTS_INVALID")
            provenance: dict[str, Any] = {"run_id": str(run_id)}
            if run.scenario_revision_id is not None:
                revision = session.get(ScenarioRevisionModel, run.scenario_revision_id)
                assert revision is not None
                vehicles = session.scalars(
                    select(VehicleModel)
                    .where(
                        VehicleModel.scenario_revision_id == revision.id,
                    )
                    .order_by(VehicleModel.source_id)
                )
                rate_data = {
                    vehicle.source_id: {
                        "fixed": str(vehicle.fixed_cost),
                        "per_duty_hour": str(vehicle.cost_per_hour),
                        "per_km": str(vehicle.cost_per_km),
                    }
                    for vehicle in vehicles
                }
                currency = revision.currency
                provenance.update(
                    rate_source="immutable_scenario_revision",
                    scenario_revision_id=str(revision.id),
                    revision_no=revision.revision_no,
                    content_sha256=revision.content_sha256,
                    context_sha256=run.input_data["context_sha256"],
                )
            else:
                snapshot = run.input_data.get("operating_cost_rates")
                if snapshot is None:
                    raise OperatingCostError("COST_RATES_NOT_RECORDED")
                rate_data = snapshot["vehicles"]
                currency = snapshot["currency"]
                provenance["rate_source"] = "persisted_demo_rate_snapshot"
            summary = run.result_data["summary"]
            if summary["currency"] != currency:
                raise OperatingCostError("COST_ROUTE_RATE_MISMATCH")
            rates = {
                key: VehicleRate(
                    Decimal(value["fixed"]),
                    Decimal(value["per_duty_hour"]),
                    Decimal(value["per_km"]),
                    input_scale=summary["cost_scale"],
                )
                for key, value in rate_data.items()
            }
            estimate = estimate_operating_cost(routes, rates, currency)
            return {
                **estimate,
                "provenance": {
                    **provenance,
                    "route_facts_sha256": _hash(routes),
                    "rates_sha256": _hash(rate_data),
                },
                "solver_objective": {
                    "units": summary["objective_cost_units"],
                    "scale": summary["cost_scale"],
                    "currency": summary["currency"],
                    "semantics": "legacy VROOM fixed + driving + distance; not business duty cost",
                },
            }
