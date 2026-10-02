"""Durable planning jobs for immutable imported revisions."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter_ns
from typing import Any
from typing import cast as type_cast
from uuid import UUID, uuid4

from geoalchemy2.elements import WKTElement
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.diagnostics import diagnose_result, failure_document, result_document
from routeops.application.planning import DemoPlanningService
from routeops.application.ports.gateways import SolverGateway
from routeops.application.processing_times import AttemptTimer
from routeops.application.revision_problem import (
    PreparedRevision,
    RunInputError,
    WorkloadLimits,
    check_revision_size,
    load_prepared_revision,
    reconcile_result,
)
from routeops.application.serialization import to_primitive
from routeops.domain.models import Coordinate
from routeops.domain.optimization import (
    Certainty,
    OptimizationProblem,
    OptimizationResult,
    OptimizationSummary,
    ResultStatus,
    SolutionQuality,
    SolverMetadata,
    UnassignedReason,
    UnassignedTask,
)
from routeops.domain.policies.allocation import TravelTimeProvider
from routeops.domain.policies.operational_allocation import OperationalAllocationPolicy
from routeops.infrastructure.persistence.diagnostics import persist_diagnostics
from routeops.infrastructure.persistence.models import (
    AllocationAttemptModel,
    AllocationDecisionSnapshotModel,
    AllocationOrderDecisionModel,
    OptimizedRouteModel,
    OrderModel,
    PlanningRunModel,
    RevisionRunEventModel,
    RevisionRunJobModel,
    RunOrderReservationModel,
    RunReservationEventModel,
    ScenarioModel,
    ScenarioRevisionModel,
    UnassignedOrderModel,
)
from routeops.infrastructure.persistence.operating_costs import _hash
from routeops.infrastructure.persistence.operational_allocation import (
    AllocationError,
    OperationalAllocationService,
)
from routeops.infrastructure.persistence.processing_times import append_timing, interrupt_attempt
from routeops.infrastructure.routing.errors import RoutingCoverageError, RoutingDependencyError
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.errors import (
    SolverDependencyError,
    SolverInputError,
    SolverResponseError,
)

logger = logging.getLogger("routeops.planning")


class PlanningRunError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class FrozenTravelTimes(TravelTimeProvider):
    def __init__(
        self,
        centers: tuple[Coordinate, ...],
        orders: tuple[Coordinate, ...],
        matrix: tuple[tuple[int, ...], ...],
    ) -> None:
        self._durations = {
            (center, order): matrix[i][j]
            for i, center in enumerate(centers)
            for j, order in enumerate(orders)
        }

    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int:
        try:
            return self._durations[(origin, destination)]
        except KeyError as exc:
            raise RoutingDependencyError("OSRM allocation matrix is incomplete") from exc


class RevisionRunService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        allocation: OperationalAllocationService,
        osrm: OsrmClient,
        solver: SolverGateway,
        limits: WorkloadLimits,
        *,
        timeout_seconds: int = 15,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        max_snap_distance_m: float = 250,
    ) -> None:
        self.sessions = sessions
        self.allocation = allocation
        self.osrm = osrm
        self.solver = solver
        self.limits = limits
        self.timeout_seconds = timeout_seconds
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.max_snap_distance_m = max_snap_distance_m

    @staticmethod
    def _locked_job(session: Session, run_id: UUID) -> RevisionRunJobModel | None:
        return session.scalar(
            select(RevisionRunJobModel)
            .where(RevisionRunJobModel.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def submit(
        self,
        scenario_id: UUID,
        revision_no: int,
        client_key: str,
        quality: SolutionQuality = SolutionQuality.BALANCED,
        policy_version: str = OperationalAllocationPolicy.ALTERNATIVES,
    ) -> tuple[dict[str, Any], bool]:
        if not 1 <= len(client_key) <= 100:
            raise PlanningRunError("RUN_IDEMPOTENCY_KEY_INVALID", 422)
        if policy_version not in (
            OperationalAllocationPolicy.GREEDY,
            OperationalAllocationPolicy.ALTERNATIVES,
        ):
            raise PlanningRunError("ALLOCATION_POLICY_UNKNOWN", 422)
        run_id: UUID
        created = False
        with self.sessions.begin() as session:
            scenario = session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == scenario_id).with_for_update()
            )
            if scenario is None:
                raise PlanningRunError("SCENARIO_NOT_FOUND", 404)
            revision = session.scalar(
                select(ScenarioRevisionModel).where(
                    ScenarioRevisionModel.scenario_id == scenario_id,
                    ScenarioRevisionModel.revision_no == revision_no,
                )
            )
            if revision is None:
                raise PlanningRunError("REVISION_NOT_FOUND", 404)
            existing = session.scalar(
                select(RevisionRunJobModel).where(
                    RevisionRunJobModel.scenario_revision_id == revision.id,
                    RevisionRunJobModel.client_key == client_key,
                )
            )
            if existing is not None:
                prior_run = session.get(PlanningRunModel, existing.run_id)
                assert prior_run is not None
                prior = prior_run.input_data
                if (
                    prior["solution_quality"] != quality.value
                    or prior["allocation_policy"] != policy_version
                    or prior["revision_id"] != str(revision.id)
                ):
                    raise PlanningRunError("RUN_IDEMPOTENCY_CONFLICT")
                run_id = existing.run_id
            else:
                if scenario.status != "ACTIVE":
                    raise PlanningRunError("SCENARIO_NOT_ACTIVE", 404)
                check_revision_size(session, revision.id, self.limits)
                prepared = load_prepared_revision(session, revision.id)
                self.limits.check(
                    len(prepared.orders),
                    prepared.line_count,
                    len(prepared.vehicles),
                    len(prepared.centers),
                )
                request_data = {
                    "revision_id": str(revision.id),
                    "content_sha256": prepared.content_sha256,
                    "context_sha256": prepared.context_sha256,
                    "snapshot_id": str(prepared.snapshot_id),
                    "solution_quality": quality.value,
                    "allocation_policy": policy_version,
                    "contract_version": (
                        "1.1" if any(
                            vehicle.max_route_distance_meters is not None
                            or vehicle.max_driving_seconds is not None
                            or vehicle.max_delivery_tasks is not None
                            for vehicle in prepared.vehicles
                        ) else "1.0"
                    ),
                }
                request_sha256 = hashlib.sha256(
                    json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                latest = session.scalar(
                    select(func.max(ScenarioRevisionModel.revision_no)).where(
                        ScenarioRevisionModel.scenario_id == scenario_id
                    )
                )
                if latest != revision_no:
                    raise PlanningRunError("REVISION_NOT_CURRENT")
                now = datetime.now(UTC)
                run_id = uuid4()
                session.add(
                    PlanningRunModel(
                        id=run_id,
                        scenario_name=scenario.name,
                        status="QUEUED",
                        started_at=now,
                        input_data=request_data,
                        scenario_revision_id=revision.id,
                        inventory_snapshot_id=prepared.snapshot_id,
                    )
                )
                session.flush()
                session.add(
                    RevisionRunJobModel(
                        run_id=run_id,
                        scenario_id=scenario_id,
                        scenario_revision_id=revision.id,
                        client_key=client_key,
                        request_sha256=request_sha256,
                        policy_version=policy_version,
                        status="QUEUED",
                        version=0,
                        attempts=0,
                        created_at=now,
                        transitioned_at=now,
                    )
                )
                session.flush()
                session.add(
                    RevisionRunEventModel(
                        id=uuid4(),
                        run_id=run_id,
                        sequence=0,
                        from_status=None,
                        to_status="QUEUED",
                        occurred_at=now,
                    )
                )
                created = True
        return self.get(run_id), created

    @staticmethod
    def _transition(
        session: Session, job: RevisionRunJobModel, target: str, reason: str | None = None
    ) -> None:
        now = datetime.now(UTC)
        before = job.status
        job.status = target
        job.version += 1
        job.transitioned_at = now
        run = session.get(PlanningRunModel, job.run_id)
        assert run is not None
        run.status = target
        if target in ("READY", "ACCEPTED", "CANCELED", "FAILED") and run.completed_at is None:
            run.completed_at = now
        session.add(
            RevisionRunEventModel(
                id=uuid4(),
                run_id=job.run_id,
                sequence=job.version,
                from_status=before,
                to_status=target,
                occurred_at=now,
                reason=reason,
            )
        )

    def claim(self) -> tuple[UUID, UUID] | None:
        with self.sessions.begin() as session:
            now = datetime.now(UTC)
            job = session.scalar(
                select(RevisionRunJobModel)
                .where(
                    (RevisionRunJobModel.status == "QUEUED")
                    | (
                        (RevisionRunJobModel.status == "RUNNING")
                        & (RevisionRunJobModel.lease_until < now)
                    )
                )
                .order_by(RevisionRunJobModel.created_at, RevisionRunJobModel.run_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= self.max_attempts:
                # The normal failure path releases linked reservations under the scenario lock.
                run_id = job.run_id
            else:
                interrupt_attempt(session, job, "LEASE_RECOVERED")
                job.attempts += 1
                token = uuid4()
                job.lease_token = token
                job.lease_until = now + timedelta(seconds=self.lease_seconds)
                self._transition(session, job, "RUNNING", "LEASE_CLAIMED")
                append_timing(session, job.run_id, token, job.attempts, {
                    "kind": "ATTEMPT_STARTED", "occurred_at": now,
                })
                return job.run_id, token
        self._exhausted(run_id)
        return None

    def heartbeat(self, run_id: UUID, token: UUID) -> bool:
        with self.sessions.begin() as session:
            job = self._locked_job(session, run_id)
            now = datetime.now(UTC)
            if (
                job is None
                or job.status != "RUNNING"
                or job.lease_token != token
                or job.lease_until is None
                or job.lease_until < now
            ):
                return False
            job.lease_until = now + timedelta(seconds=self.lease_seconds)
            return True

    @contextmanager
    def _heartbeat(self, run_id: UUID, token: UUID) -> Iterator[None]:
        stopped = threading.Event()

        def beat() -> None:
            while not stopped.wait(max(1, self.lease_seconds // 3)):
                try:
                    if not self.heartbeat(run_id, token):
                        return
                except Exception:
                    logger.exception("planning_heartbeat_failed", extra={"run_id": str(run_id)})
                    return

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=2)

    def process_once(self) -> bool:
        claim = self.claim()
        if claim is None:
            self.recover_orphans()
            return False
        run_id, token = claim
        timer = AttemptTimer(lambda event: self._record_timing(run_id, token, event))
        try:
            with self._heartbeat(run_id, token):
                self._process(run_id, token, timer)
        except (
            AllocationError,
            RunInputError,
            RoutingDependencyError,
            RoutingCoverageError,
            SolverDependencyError,
            SolverInputError,
            SolverResponseError,
        ) as exc:
            logger.warning(
                "planning_run_failed", extra={"run_id": str(run_id), "kind": type(exc).__name__}
            )
            if isinstance(exc, RoutingCoverageError):
                code = exc.code
            elif isinstance(exc, RoutingDependencyError):
                code = "ROUTING_DEPENDENCY_FAILED"
            elif isinstance(exc, SolverDependencyError):
                code = "SOLVER_DEPENDENCY_FAILED"
            elif isinstance(exc, SolverResponseError):
                code = "SOLVER_RESPONSE_INVALID"
            elif isinstance(exc, SolverInputError):
                code = "SOLVER_INPUT_INVALID"
            else:
                code = exc.code
            self._fail(
                run_id, token, code,
                exc.evidence if isinstance(exc, RoutingCoverageError) else None,
                timer=timer,
            )
        except Exception:
            logger.exception("planning_run_failed_unexpected", extra={"run_id": str(run_id)})
            self._fail(run_id, token, "PLANNING_INFRASTRUCTURE_FAILED", timer=timer)
        self.recover_orphans()
        return True

    def _active(self, run_id: UUID, token: UUID) -> bool:
        with self.sessions() as session:
            job = session.get(RevisionRunJobModel, run_id)
            return bool(
                job is not None
                and job.status == "RUNNING"
                and job.lease_token == token
                and job.lease_until is not None
                and job.lease_until >= datetime.now(UTC)
            )

    def _record_timing(self, run_id: UUID, token: UUID, event: dict[str, Any]) -> None:
        with self.sessions.begin() as session:
            job = self._locked_job(session, run_id)
            if (job is not None and job.status == "RUNNING" and job.lease_token == token
                    and job.lease_until is not None and job.lease_until >= datetime.now(UTC)):
                append_timing(session, run_id, token, job.attempts, event)

    def _process(
        self, run_id: UUID, token: UUID, timer: AttemptTimer | None = None,
    ) -> None:
        timer = timer or AttemptTimer(lambda event: self._record_timing(run_id, token, event))
        with timer.phase("VALIDATE_RUN"):
            prepared, existing_attempt, quality, policy_version = self._prepare_run(run_id)
        if not self._active(run_id, token):
            return
        snap_evidence: list[dict[str, object]] = []
        if existing_attempt is None:
            origins = tuple(center.location for center in prepared.centers)
            destinations = tuple(order.location for order in prepared.orders)
            with timer.phase("PREPARE_OSRM"):
                try:
                    checked = self.osrm.checked_duration_matrix(
                        origins, destinations, self.max_snap_distance_m
                    )
                except RoutingCoverageError as exc:
                    self._label_snaps(exc.evidence, prepared.centers, prepared.orders)
                    raise
                matrix = checked.durations
                snap_evidence = checked.snaps
                self._label_snaps(snap_evidence, prepared.centers, prepared.orders)
                for item in snap_evidence:
                    item["max_snap_distance_m"] = self.max_snap_distance_m
            if not self._active(run_id, token):
                return
            cached = FrozenTravelTimes(origins, destinations, matrix)
            with timer.phase("ALLOCATE_RESERVE"):
                allocation = self.allocation.allocate(
                    prepared.scenario_id,
                    self._revision_number(prepared.revision_id),
                    str(run_id),
                    policy_version=policy_version,
                    travel_times=cached,
                    network_evidence=snap_evidence,
                )
                attempt_id = UUID(allocation["id"])
                if not self._link_allocation(run_id, token, attempt_id):
                    self._release_orphan(run_id, attempt_id)
                    return
        else:
            attempt_id = existing_attempt
            with timer.phase("RECOVER_ALLOCATION"), self.sessions() as session:
                snapshot = session.get(AllocationDecisionSnapshotModel, attempt_id)
                if snapshot is not None:
                    snap_evidence = snapshot.inventory_data.get("network_coverage", [])
        if not self._active(run_id, token):
            return
        with timer.phase("PREPARE_SOLVER"):
            assigned, allocation_unassigned = self._decisions(attempt_id)
            problem = prepared.problem(run_id, assigned, quality, self.timeout_seconds)
        with timer.phase("SOLVER"):
            solved = self.solver.solve(problem) if problem.tasks else self._empty_result(problem)
        with timer.phase("RECONCILE"):
            reconcile_result(problem, solved, self.max_snap_distance_m)
            result = self._merge_unassigned(solved, allocation_unassigned)
            result = diagnose_result(result, problem, self._diagnostic_allocations(attempt_id), {
                "run_id": str(run_id), "revision_id": str(prepared.revision_id),
                "content_sha256": prepared.content_sha256,
                "context_sha256": prepared.context_sha256,
                "normalized_problem_sha256": _hash(to_primitive(problem)),
                "allocation_attempt_id": str(attempt_id), "policy_version": policy_version,
                "source": "immutable_revision_and_allocation_decisions",
            })
        self._finish(run_id, token, result, problem, snap_evidence, timer=timer)

    def _prepare_run(
        self, run_id: UUID,
    ) -> tuple[PreparedRevision, UUID | None, SolutionQuality, str]:
        with self.sessions() as session:
            job = session.get(RevisionRunJobModel, run_id)
            run = session.get(PlanningRunModel, run_id)
            assert job is not None and run is not None
            check_revision_size(session, job.scenario_revision_id, self.limits)
            prepared = load_prepared_revision(session, job.scenario_revision_id)
            existing_attempt = job.allocation_attempt_id
            request_data = run.input_data
            self.limits.check(
                len(prepared.orders),
                prepared.line_count,
                len(prepared.vehicles),
                len(prepared.centers),
            )
            if (
                request_data["content_sha256"] != prepared.content_sha256
                or request_data["context_sha256"] != prepared.context_sha256
                or request_data["snapshot_id"] != str(prepared.snapshot_id)
            ):
                raise RunInputError("REVISION_CONTEXT_CHANGED")
            quality = SolutionQuality(request_data["solution_quality"])
            policy_version = job.policy_version
        return prepared, existing_attempt, quality, policy_version

    @staticmethod
    def _label_snaps(
        snaps: list[dict[str, object]], centers: tuple[Any, ...], orders: tuple[Any, ...]
    ) -> None:
        for item in snaps:
            index = type_cast(int, item["index"])
            item["business_id"] = (
                centers[index].id if item["role"] == "center" else orders[index].id
            )

    def _revision_number(self, revision_id: UUID) -> int:
        with self.sessions() as session:
            revision = session.get(ScenarioRevisionModel, revision_id)
            assert revision is not None
            return revision.revision_no

    def _link_allocation(self, run_id: UUID, token: UUID, attempt_id: UUID) -> bool:
        with self.sessions.begin() as session:
            job = session.get(RevisionRunJobModel, run_id)
            assert job is not None
            session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == job.scenario_id).with_for_update()
            )
            job = self._locked_job(session, run_id)
            assert job is not None
            if (
                job.status != "RUNNING"
                or job.lease_token != token
                or job.lease_until is None
                or job.lease_until < datetime.now(UTC)
            ):
                return False
            if job.allocation_attempt_id is not None:
                return job.allocation_attempt_id == attempt_id
            attempt = session.get(AllocationAttemptModel, attempt_id)
            if attempt is None or attempt.scenario_revision_id != job.scenario_revision_id:
                raise PlanningRunError("RUN_ALLOCATION_MISMATCH")
            job.allocation_attempt_id = attempt_id
            now = datetime.now(UTC)
            selected = list(
                session.scalars(
                    select(AllocationOrderDecisionModel).where(
                        AllocationOrderDecisionModel.attempt_id == attempt_id,
                        AllocationOrderDecisionModel.center_source_id.is_not(None),
                    )
                )
            )
            for decision in selected:
                session.add(
                    RunOrderReservationModel(
                        run_id=run_id,
                        decision_id=decision.id,
                        status="HELD",
                        version=0,
                        transitioned_at=now,
                    )
                )
            session.flush()
            for decision in selected:
                session.add(
                    RunReservationEventModel(
                        id=uuid4(),
                        run_id=run_id,
                        decision_id=decision.id,
                        sequence=0,
                        from_status=None,
                        to_status="HELD",
                        occurred_at=now,
                        reason="ALLOCATION_HELD",
                    )
                )
        return True

    def _diagnostic_allocations(self, attempt_id: UUID) -> dict[str, dict[str, Any]]:
        with self.sessions() as session:
            return {
                source_id: {**decision.evidence, "allocation_decision_id": str(decision.id)}
                for decision, source_id in session.execute(
                    select(AllocationOrderDecisionModel, OrderModel.source_id)
                    .join(OrderModel, AllocationOrderDecisionModel.order_id == OrderModel.id)
                    .where(AllocationOrderDecisionModel.attempt_id == attempt_id)
                )
            }

    def _decisions(self, attempt_id: UUID) -> tuple[dict[str, str], tuple[UnassignedTask, ...]]:
        with self.sessions() as session:
            rows = list(
                session.execute(
                    select(AllocationOrderDecisionModel, OrderModel.source_id)
                    .join(OrderModel, AllocationOrderDecisionModel.order_id == OrderModel.id)
                    .where(AllocationOrderDecisionModel.attempt_id == attempt_id)
                    .order_by(AllocationOrderDecisionModel.sequence)
                )
            )
            assigned = {
                source_id: decision.center_source_id
                for decision, source_id in rows
                if decision.center_source_id is not None
            }
            unassigned = tuple(
                UnassignedTask(
                    task_id=None,
                    order_id=source_id,
                    stage="ALLOCATION",
                    reasons=(
                        UnassignedReason(
                            code=decision.reason_code or "ALLOCATION_UNASSIGNED",
                            certainty=Certainty.PROVEN,
                            detail="No center passed the recorded stock and fleet checks.",
                            evidence={"allocation_decision_id": str(decision.id)},
                        ),
                    ),
                )
                for decision, source_id in rows
                if decision.center_source_id is None
            )
            return assigned, unassigned

    @staticmethod
    def _empty_result(problem: OptimizationProblem) -> OptimizationResult:
        return OptimizationResult(
            contract_version=problem.contract_version,
            problem_id=problem.problem_id,
            status=ResultStatus.SUCCEEDED,
            solver=SolverMetadata("skipped", "", "", "osrm", "", 0),
            summary=OptimizationSummary(
                0, 0, 0, 0, 0, 0, 0, 0, 0, 10_000, problem.vehicles[0].costs.currency
            ),
            routes=(),
            unassigned=(),
        )

    @staticmethod
    def _merge_unassigned(
        result: OptimizationResult, allocation_unassigned: tuple[UnassignedTask, ...]
    ) -> OptimizationResult:
        all_unassigned = result.unassigned + allocation_unassigned
        return replace(
            result,
            status=ResultStatus.PARTIAL if all_unassigned else ResultStatus.SUCCEEDED,
            summary=replace(result.summary, unassigned_task_count=len(all_unassigned)),
            unassigned=all_unassigned,
        )

    @staticmethod
    def _reservation_rows(session: Session, run_id: UUID) -> list[RunOrderReservationModel]:
        return list(
            session.scalars(
                select(RunOrderReservationModel)
                .join(
                    AllocationOrderDecisionModel,
                    RunOrderReservationModel.decision_id == AllocationOrderDecisionModel.id,
                )
                .where(RunOrderReservationModel.run_id == run_id)
                .order_by(
                    AllocationOrderDecisionModel.center_source_id,
                    RunOrderReservationModel.decision_id,
                )
                .with_for_update()
            )
        )

    @staticmethod
    def _change_reservations(
        session: Session,
        run_id: UUID,
        target: str,
        reason: str,
        *,
        only_order_ids: set[UUID] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        rows = RevisionRunService._reservation_rows(session, run_id)
        for row in rows:
            if row.status != "HELD" or (
                only_order_ids is not None and row.decision_id not in only_order_ids
            ):
                continue
            row.status = target
            row.version += 1
            row.transitioned_at = now
            session.add(
                RunReservationEventModel(
                    id=uuid4(),
                    run_id=run_id,
                    decision_id=row.decision_id,
                    sequence=row.version,
                    from_status="HELD",
                    to_status=target,
                    occurred_at=now,
                    reason=reason,
                )
            )
            session.flush()

    @staticmethod
    def _geometry(coordinates: list[dict[str, float]]) -> WKTElement | None:
        if len(coordinates) < 2:
            return None
        points = ", ".join(
            f"{float(point['longitude'])} {float(point['latitude'])}" for point in coordinates
        )
        return WKTElement(f"LINESTRING ({points})", srid=4326)

    def _finish(
        self, run_id: UUID, token: UUID, result: OptimizationResult,
        problem: OptimizationProblem, snap_evidence: list[dict[str, object]],
        *, timer: AttemptTimer | None = None,
    ) -> bool:
        if timer is not None:
            timer.emit({"kind": "PHASE_STARTED", "phase": "PERSIST_RESULT",
                        "occurred_at": datetime.now(UTC)})
        persist_start = perf_counter_ns()
        try:
            return self._persist_result(
                run_id, token, result, problem, snap_evidence, timer, persist_start,
            )
        except Exception:
            if timer is not None:
                timer.emit({"kind": "PHASE_FINISHED", "phase": "PERSIST_RESULT",
                            "occurred_at": datetime.now(UTC), "outcome": "FAILED",
                            "duration_ns": perf_counter_ns() - persist_start,
                            "details": {"scope": "failed_persistence_call_including_rollback"}})
            raise

    def _persist_result(
        self, run_id: UUID, token: UUID, result: OptimizationResult,
        problem: OptimizationProblem, snap_evidence: list[dict[str, object]],
        timer: AttemptTimer | None, persist_start: int,
    ) -> bool:
        data = to_primitive(result)
        kpis = DemoPlanningService._kpis(result)
        solver_unassigned_ids = {
            item.order_id for item in result.unassigned if item.stage == "OPTIMIZATION"
        }
        with self.sessions.begin() as session:
            job = session.get(RevisionRunJobModel, run_id)
            assert job is not None
            session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == job.scenario_id).with_for_update()
            )
            job = self._locked_job(session, run_id)
            assert job is not None
            if (
                job.status != "RUNNING"
                or job.lease_token != token
                or job.lease_until is None
                or job.lease_until < datetime.now(UTC)
            ):
                return False
            run = session.get(PlanningRunModel, run_id)
            assert run is not None
            run.result_data = data
            run.kpis = {**kpis, "network_coverage": {
                "max_snap_distance_m": self.max_snap_distance_m,
                "points": snap_evidence,
            }}
            decisions = list(
                session.execute(
                    select(AllocationOrderDecisionModel.id, OrderModel.source_id)
                    .join(OrderModel, AllocationOrderDecisionModel.order_id == OrderModel.id)
                    .where(AllocationOrderDecisionModel.attempt_id == job.allocation_attempt_id)
                )
            )
            release_ids = {
                decision_id
                for decision_id, source_id in decisions
                if source_id in solver_unassigned_ids
            }
            self._change_reservations(
                session, run_id, "RELEASED", "SOLVER_UNASSIGNED", only_order_ids=release_ids
            )
            for sequence, route in enumerate(data["routes"]):
                session.add(
                    OptimizedRouteModel(
                        run_id=run_id,
                        vehicle_id=UUID(route["vehicle_id"]),
                        source_vehicle_id=route["source_vehicle_id"],
                        distribution_center_id=route["distribution_center_id"],
                        sequence=sequence,
                        distance_meters=int(route["totals"]["distance_meters"]),
                        total_duration_seconds=int(route["totals"]["total_duration_seconds"]),
                        geometry=self._geometry(route["geometry"]),
                        payload=route,
                    )
                )
            for item in data["unassigned"]:
                session.add(
                    UnassignedOrderModel(
                        run_id=run_id,
                        order_id=item["order_id"],
                        stage=item["stage"],
                        reasons=item["reasons"],
                    )
                )
            session.flush()
            persist_diagnostics(session, run_id, result_document(data, {
                "run_id": str(run_id), "input_sha256": _hash(run.input_data),
                "scenario_revision_id": str(run.scenario_revision_id),
                "allocation_attempt_id": str(job.allocation_attempt_id),
                "solver": data["solver"], "source": "committed_result_and_recorded_decisions",
            }), token, job.attempts)
            if timer is not None:
                append_timing(session, run_id, token, job.attempts, {
                    "kind": "PHASE_FINISHED", "phase": "PERSIST_RESULT",
                    "occurred_at": datetime.now(UTC), "outcome": "SUCCEEDED",
                    "duration_ns": perf_counter_ns() - persist_start,
                    "details": {"scope": "through_flush_excludes_final_commit"},
                })
                append_timing(session, run_id, token, job.attempts, timer.finish("READY"))
            job.lease_token = None
            job.lease_until = None
            self._transition(session, job, "READY", "SOLVER_RECONCILED")
        return True

    def _fail(
        self, run_id: UUID, token: UUID, code: str,
        snap_evidence: list[dict[str, object]] | None = None,
        *, timer: AttemptTimer | None = None,
    ) -> None:
        with self.sessions.begin() as session:
            job = session.get(RevisionRunJobModel, run_id)
            if job is None:
                return
            session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == job.scenario_id).with_for_update()
            )
            job = self._locked_job(session, run_id)
            if (
                job is None
                or job.status != "RUNNING"
                or job.lease_token != token
                or job.lease_until is None
                or job.lease_until < datetime.now(UTC)
            ):
                return
            self._change_reservations(session, run_id, "RELEASED", "RUN_FAILED")
            run = session.get(PlanningRunModel, run_id)
            assert run is not None
            run.error = code
            persist_diagnostics(session, run_id, failure_document(code, {
                "run_id": str(run_id), "input_sha256": _hash(run.input_data),
                "scenario_revision_id": str(run.scenario_revision_id),
                "source": "fenced_worker_failure",
            }, snap_evidence), token, job.attempts)
            if timer is not None:
                append_timing(session, run_id, token, job.attempts, timer.finish("FAILED"))
            else:
                interrupt_attempt(session, job, "FAILURE_WITHOUT_TIMER")
            job.lease_token = None
            job.lease_until = None
            run = session.get(PlanningRunModel, run_id)
            assert run is not None
            run.error = code
            if snap_evidence is not None:
                run.kpis = {"network_coverage": {
                    "max_snap_distance_m": self.max_snap_distance_m,
                    "points": snap_evidence,
                }}
            self._transition(session, job, "FAILED", code)

    def _exhausted(self, run_id: UUID) -> None:
        with self.sessions.begin() as session:
            job = session.get(RevisionRunJobModel, run_id)
            if job is None:
                return
            session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == job.scenario_id).with_for_update()
            )
            job = self._locked_job(session, run_id)
            if job is None or job.status not in ("QUEUED", "RUNNING"):
                return
            if job.lease_until is not None and job.lease_until >= datetime.now(UTC):
                return
            self._change_reservations(session, run_id, "RELEASED", "RETRY_LIMIT")
            run = session.get(PlanningRunModel, run_id)
            assert run is not None
            run.error = "RUN_RETRY_LIMIT"
            persist_diagnostics(session, run_id, failure_document("RUN_RETRY_LIMIT", {
                "run_id": str(run_id), "input_sha256": _hash(run.input_data),
                "source": "lease_recovery_retry_limit",
            }), None, job.attempts)
            interrupt_attempt(session, job, "RETRY_LIMIT")
            job.lease_token = None
            job.lease_until = None
            run = session.get(PlanningRunModel, run_id)
            assert run is not None
            run.error = "RUN_RETRY_LIMIT"
            self._transition(session, job, "FAILED", "RUN_RETRY_LIMIT")

    def accept(self, run_id: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(RevisionRunJobModel, run_id)
            if job is None:
                raise PlanningRunError("RUN_NOT_FOUND", 404)
            session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == job.scenario_id).with_for_update()
            )
            job = self._locked_job(session, run_id)
            assert job is not None
            if job.status == "ACCEPTED":
                return self.get(run_id)
            if job.status != "READY":
                raise PlanningRunError("RUN_TRANSITION_CONFLICT")
            self._change_reservations(session, run_id, "CONFIRMED", "RUN_ACCEPTED")
            self._transition(session, job, "ACCEPTED", "USER_ACCEPTED")
        return self.get(run_id)

    def cancel(self, run_id: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(RevisionRunJobModel, run_id)
            if job is None:
                raise PlanningRunError("RUN_NOT_FOUND", 404)
            session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == job.scenario_id).with_for_update()
            )
            job = self._locked_job(session, run_id)
            assert job is not None
            if job.status == "CANCELED":
                return self.get(run_id)
            if job.status not in ("QUEUED", "RUNNING", "READY"):
                raise PlanningRunError("RUN_TRANSITION_CONFLICT")
            self._change_reservations(session, run_id, "RELEASED", "RUN_CANCELED")
            interrupt_attempt(session, job, "USER_CANCELED")
            job.lease_token = None
            job.lease_until = None
            self._transition(session, job, "CANCELED", "USER_CANCELED")
        self.recover_orphans()
        return self.get(run_id)

    def _release_orphan(self, run_id: UUID, attempt_id: UUID) -> None:
        with self.sessions() as session:
            job = session.get(RevisionRunJobModel, run_id)
            if job is None or job.allocation_attempt_id == attempt_id:
                return
            if job.status not in ("CANCELED", "FAILED"):
                return
        try:
            self.allocation.release(attempt_id)
        except AllocationError:
            logger.exception("planning_orphan_release_failed", extra={"run_id": str(run_id)})

    def recover_orphans(self) -> None:
        with self.sessions() as session:
            rows = list(
                session.execute(
                    select(RevisionRunJobModel.run_id, AllocationAttemptModel.id)
                    .join(
                        AllocationAttemptModel,
                        AllocationAttemptModel.client_key
                        == cast(RevisionRunJobModel.run_id, String),
                    )
                    .where(
                        RevisionRunJobModel.status.in_(("CANCELED", "FAILED")),
                        RevisionRunJobModel.allocation_attempt_id.is_(None),
                        AllocationAttemptModel.status == "HELD",
                    )
                )
            )
        for run_id, attempt_id in rows:
            self._release_orphan(run_id, attempt_id)

    def get(self, run_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(RevisionRunJobModel, run_id)
            run = session.get(PlanningRunModel, run_id)
            if job is None or run is None:
                raise PlanningRunError("RUN_NOT_FOUND", 404)
            decisions: list[dict[str, Any]] = []
            if job.allocation_attempt_id is not None:
                decisions = [
                    {
                        "order_id": source_id,
                        "center_id": decision.center_source_id,
                        "reason_code": decision.reason_code,
                        "evidence": decision.evidence,
                        "reservation_status": status,
                    }
                    for decision, source_id, status in session.execute(
                        select(
                            AllocationOrderDecisionModel,
                            OrderModel.source_id,
                            RunOrderReservationModel.status,
                        )
                        .join(OrderModel, AllocationOrderDecisionModel.order_id == OrderModel.id)
                        .outerjoin(
                            RunOrderReservationModel,
                            (
                                RunOrderReservationModel.decision_id
                                == AllocationOrderDecisionModel.id
                            )
                            & (RunOrderReservationModel.run_id == run_id),
                        )
                        .where(AllocationOrderDecisionModel.attempt_id == job.allocation_attempt_id)
                        .order_by(AllocationOrderDecisionModel.sequence)
                    )
                ]
            events = [
                {"sequence": row.sequence, "from": row.from_status, "to": row.to_status}
                for row in session.scalars(
                    select(RevisionRunEventModel)
                    .where(RevisionRunEventModel.run_id == run_id)
                    .order_by(RevisionRunEventModel.sequence)
                )
            ]
            return {
                "run_id": str(run.id),
                "scenario_id": str(job.scenario_id),
                "scenario_revision_id": str(job.scenario_revision_id),
                "inventory_snapshot_id": str(run.inventory_snapshot_id),
                "allocation_attempt_id": str(job.allocation_attempt_id)
                if job.allocation_attempt_id
                else None,
                "scenario_name": run.scenario_name,
                "status": job.status,
                "attempts": job.attempts,
                "max_attempts": self.max_attempts,
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "input": run.input_data,
                "result": run.result_data,
                "kpis": run.kpis,
                "error": run.error,
                "decisions": decisions,
                "events": events,
            }

    def lookup(self, scenario_id: UUID, revision_no: int, key: str) -> dict[str, Any]:
        with self.sessions() as session:
            revision = session.scalar(
                select(ScenarioRevisionModel).where(
                    ScenarioRevisionModel.scenario_id == scenario_id,
                    ScenarioRevisionModel.revision_no == revision_no,
                )
            )
            if revision is None:
                raise PlanningRunError("REVISION_NOT_FOUND", 404)
            job = session.scalar(
                select(RevisionRunJobModel).where(
                    RevisionRunJobModel.scenario_revision_id == revision.id,
                    RevisionRunJobModel.client_key == key,
                )
            )
            if job is None:
                raise PlanningRunError("RUN_NOT_FOUND", 404)
            return self.get(job.run_id)

    def list(self, scenario_id: UUID, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise PlanningRunError("PAGINATION_INVALID", 422)
        with self.sessions() as session:
            ids = list(
                session.scalars(
                    select(RevisionRunJobModel.run_id)
                    .where(RevisionRunJobModel.scenario_id == scenario_id)
                    .order_by(
                        RevisionRunJobModel.created_at.desc(), RevisionRunJobModel.run_id.desc()
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            )
        return {
            "items": [self.get(run_id) for run_id in ids[:limit]],
            "next_offset": offset + limit if len(ids) > limit else None,
        }
