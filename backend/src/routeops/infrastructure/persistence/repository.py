from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from routeops.infrastructure.persistence.models import (
    OptimizedRouteModel,
    PlanningRunModel,
    UnassignedOrderModel,
)


class DatabaseRunRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def start(
        self, run_id: UUID, scenario_name: str, started_at: datetime, input_data: dict[str, Any]
    ) -> None:
        with self._sessions.begin() as session:
            session.add(
                PlanningRunModel(
                    id=run_id,
                    scenario_name=scenario_name,
                    status="RUNNING",
                    started_at=started_at,
                    input_data=input_data,
                )
            )

    def complete(
        self,
        run_id: UUID,
        completed_at: datetime,
        result: dict[str, Any],
        kpis: dict[str, Any],
    ) -> None:
        with self._sessions.begin() as session:
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                raise LookupError(f"planning run {run_id} does not exist")
            run.status = str(result["status"])
            run.completed_at = completed_at
            run.result_data = result
            run.kpis = kpis
            for sequence, route in enumerate(result["routes"]):
                geometry = self._geometry(route["geometry"])
                session.add(
                    OptimizedRouteModel(
                        run_id=run_id,
                        vehicle_id=UUID(route["vehicle_id"]),
                        source_vehicle_id=route["source_vehicle_id"],
                        distribution_center_id=route["distribution_center_id"],
                        sequence=sequence,
                        distance_meters=int(route["totals"]["distance_meters"]),
                        total_duration_seconds=int(route["totals"]["total_duration_seconds"]),
                        geometry=geometry,
                        payload=route,
                    )
                )
            for item in result["unassigned"]:
                session.add(
                    UnassignedOrderModel(
                        run_id=run_id,
                        order_id=item["order_id"],
                        stage=item["stage"],
                        reasons=item["reasons"],
                    )
                )

    def fail(self, run_id: UUID, completed_at: datetime, error: str) -> None:
        with self._sessions.begin() as session:
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                return
            run.status = "FAILED"
            run.completed_at = completed_at
            run.error = error

    def get(self, run_id: UUID) -> dict[str, Any] | None:
        with self._sessions() as session:
            run = session.get(PlanningRunModel, run_id)
            return self._serialize(run) if run is not None else None

    def latest(self) -> dict[str, Any] | None:
        with self._sessions() as session:
            statement = (
                select(PlanningRunModel).order_by(PlanningRunModel.started_at.desc()).limit(1)
            )
            run = session.scalar(statement)
            return self._serialize(run) if run is not None else None

    @staticmethod
    def _serialize(run: PlanningRunModel) -> dict[str, Any]:
        return {
            "run_id": str(run.id),
            "scenario_name": run.scenario_name,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "input": run.input_data,
            "result": run.result_data,
            "kpis": run.kpis,
            "error": run.error,
        }

    @staticmethod
    def _geometry(coordinates: list[dict[str, float]]) -> WKTElement | None:
        if len(coordinates) < 2:
            return None
        points = ", ".join(
            f"{float(point['longitude'])} {float(point['latitude'])}" for point in coordinates
        )
        return WKTElement(f"LINESTRING ({points})", srid=4326)
