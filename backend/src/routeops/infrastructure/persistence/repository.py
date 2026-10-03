from __future__ import annotations

from datetime import datetime
from time import perf_counter_ns
from typing import Any
from uuid import UUID

from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.diagnostics import failure_document, result_document
from routeops.application.processing_times import AttemptTimer
from routeops.infrastructure.persistence.diagnostics import persist_diagnostics
from routeops.infrastructure.persistence.models import (
    OptimizedRouteModel,
    PlanningRunModel,
    UnassignedOrderModel,
)
from routeops.infrastructure.persistence.operating_costs import _hash
from routeops.infrastructure.persistence.processing_times import append_timing


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
        *,
        timer: AttemptTimer | None = None,
        timing_token: UUID | None = None,
    ) -> None:
        persist_start = perf_counter_ns()
        if timer is not None:
            timer.emit(
                {
                    "kind": "PHASE_STARTED",
                    "phase": "PERSIST_RESULT",
                    "occurred_at": datetime.now().astimezone(),
                }
            )
        try:
            self._complete(run_id, completed_at, result, kpis, timer, timing_token, persist_start)
        except Exception:
            if timer is not None:
                timer.emit(
                    {
                        "kind": "PHASE_FINISHED",
                        "phase": "PERSIST_RESULT",
                        "occurred_at": datetime.now().astimezone(),
                        "outcome": "FAILED",
                        "duration_ns": perf_counter_ns() - persist_start,
                        "details": {"scope": "failed_persistence_call_including_rollback"},
                    }
                )
            raise

    def _complete(
        self,
        run_id: UUID,
        completed_at: datetime,
        result: dict[str, Any],
        kpis: dict[str, Any],
        timer: AttemptTimer | None,
        timing_token: UUID | None,
        persist_start: int,
    ) -> None:
        with self._sessions.begin() as session:
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                raise LookupError(f"planning run {run_id} does not exist")
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

            session.flush()
            if timing_token is not None:
                persist_diagnostics(
                    session,
                    run_id,
                    result_document(
                        result,
                        {
                            "run_id": str(run_id),
                            "input_sha256": _hash(run.input_data),
                            "source": "persisted_original_demo_result",
                            "solver": result.get("solver"),
                        },
                    ),
                    timing_token,
                    1,
                )
            if timer is not None and timing_token is not None:
                append_timing(
                    session,
                    run_id,
                    timing_token,
                    1,
                    {
                        "kind": "PHASE_FINISHED",
                        "phase": "PERSIST_RESULT",
                        "occurred_at": datetime.now().astimezone(),
                        "outcome": "SUCCEEDED",
                        "duration_ns": perf_counter_ns() - persist_start,
                        "details": {"scope": "through_flush_excludes_final_commit"},
                    },
                )
                append_timing(session, run_id, timing_token, 1, timer.finish("READY"))
            run.status = str(result["status"])
            run.completed_at = datetime.now().astimezone() if timer is not None else completed_at

    def record_timing(self, run_id: UUID, token: UUID, event: dict[str, Any]) -> None:
        with self._sessions.begin() as session:
            append_timing(session, run_id, token, 1, event)

    def fail(
        self,
        run_id: UUID,
        completed_at: datetime,
        error: str,
        *,
        timer: AttemptTimer | None = None,
        timing_token: UUID | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                return
            run.error = error
            if timing_token is not None:
                persist_diagnostics(
                    session,
                    run_id,
                    failure_document(
                        error,
                        {
                            "run_id": str(run_id),
                            "input_sha256": _hash(run.input_data),
                            "source": "synchronous_demo_failure",
                        },
                    ),
                    timing_token,
                    1,
                )
            if timer is not None and timing_token is not None:
                append_timing(session, run_id, timing_token, 1, timer.finish("FAILED"))
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
