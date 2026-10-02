"""Leased analytical simulations. This module never writes operational inventory."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.diagnostics import DIAGNOSTIC_VERSION, diagnose_result
from routeops.application.operating_cost import COST_VERSION
from routeops.application.plan_comparison import (
    COMPARISON_VERSION,
    MANUAL_VERSION,
    OPTIMIZED_DEPARTURE,
    ComparisonError,
    differences,
    evaluate_manual,
    metrics_for_plan,
)
from routeops.application.plan_metrics import METRICS_VERSION
from routeops.application.ports.errors import operational_failure_code
from routeops.application.ports.gateways import SolverGateway
from routeops.application.revision_problem import (
    PreparedRevision,
    WorkloadLimits,
    check_revision_size,
    load_prepared_revision,
    reconcile_result,
)
from routeops.application.serialization import to_primitive
from routeops.domain.optimization import (
    Certainty,
    ResultStatus,
    SolutionQuality,
    UnassignedReason,
    UnassignedTask,
)
from routeops.domain.policies.operational_allocation import (
    OperationalAllocationPolicy,
    OperationalStock,
    OperationalVehicle,
)
from routeops.infrastructure.persistence.models import (
    ComparisonEventModel,
    ComparisonJobModel,
    ComparisonResultModel,
    DistributionCenterModel,
    ImportValidationReportModel,
    InventorySnapshotLineModel,
    OperationalInventoryPositionModel,
    PlanComparisonModel,
    ScenarioModel,
    ScenarioRevisionModel,
)
from routeops.infrastructure.persistence.operating_costs import _hash
from routeops.infrastructure.persistence.revision_runs import FrozenTravelTimes, RevisionRunService
from routeops.infrastructure.routing.osrm import OsrmClient

logger = logging.getLogger("routeops.comparison")
POLICIES = (OperationalAllocationPolicy.GREEDY, OperationalAllocationPolicy.ALTERNATIVES)


def _prepared(session: Session, revision_id: UUID) -> PreparedRevision:
    p = load_prepared_revision(session, revision_id)
    return replace(
        p,
        centers=tuple(sorted(p.centers, key=lambda c: c.id)),
        orders=tuple(sorted(p.orders, key=lambda o: o.id)),
        vehicles=tuple(sorted(p.vehicles, key=lambda v: v.source_vehicle_id)),
    )


class PlanComparisonService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        osrm: OsrmClient,
        solver: SolverGateway,
        limits: WorkloadLimits,
        *,
        map_dataset_sha256: str,
        timeout_seconds: int = 15,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        max_snap_distance_m: float = 250,
        retry_seconds: int = 5,
        solver_version: str = "1.15.0",
        adapter_version: str = "1.2.0",
    ) -> None:
        self.sessions, self.osrm, self.solver, self.limits = sessions, osrm, solver, limits
        self.timeout_seconds, self.lease_seconds = timeout_seconds, lease_seconds
        self.max_attempts, self.max_snap_distance_m = max_attempts, max_snap_distance_m
        self.retry_seconds = retry_seconds
        self.versions = {
            "comparison": COMPARISON_VERSION,
            "manual": MANUAL_VERSION,
            "metrics": METRICS_VERSION,
            "cost": COST_VERSION,
            "diagnostics": DIAGNOSTIC_VERSION,
            "policies": list(POLICIES),
            "solver": solver_version,
            "adapter": adapter_version,
            "osrm": osrm.version,
            "osrm_profile": "car",
            "map_dataset_sha256": map_dataset_sha256,
        }

    def submit(
        self,
        scenario_id: UUID,
        revision_no: int,
        key: str,
        manual_routes: list[dict[str, Any]],
        quality: SolutionQuality = SolutionQuality.BALANCED,
    ) -> tuple[dict[str, Any], bool]:
        if not 1 <= len(key) <= 100:
            raise ComparisonError("COMPARISON_KEY_INVALID", 422)
        if (
            len(manual_routes) > self.limits.max_vehicles
            or sum(len(r["order_ids"]) for r in manual_routes) > self.limits.max_orders
        ):
            raise ComparisonError("COMPARISON_WORKLOAD_LIMIT", 413)
        request = {
            "revision_no": revision_no,
            "manual_routes": manual_routes,
            "solution_quality": quality.value,
        }
        request_hash = _hash(request)
        created = False
        with self.sessions.begin() as session:
            # Same parent lock as publication/reservation mutations; no external work here.
            scenario = session.scalar(
                select(ScenarioModel).where(ScenarioModel.id == scenario_id).with_for_update()
            )
            if scenario is None:
                raise ComparisonError("SCENARIO_NOT_FOUND", 404)
            existing = session.scalar(
                select(PlanComparisonModel).where(
                    PlanComparisonModel.scenario_id == scenario_id,
                    PlanComparisonModel.client_key == key,
                )
            )
            if existing:
                if existing.request_sha256 != request_hash:
                    raise ComparisonError("COMPARISON_IDEMPOTENCY_CONFLICT")
                comparison_id = existing.id
            else:
                if scenario.status != "ACTIVE":
                    raise ComparisonError("SCENARIO_NOT_ACTIVE")
                revision = session.scalar(
                    select(ScenarioRevisionModel).where(
                        ScenarioRevisionModel.scenario_id == scenario_id,
                        ScenarioRevisionModel.revision_no == revision_no,
                    )
                )
                if revision is None:
                    raise ComparisonError("REVISION_NOT_FOUND", 404)
                latest = session.scalar(
                    select(func.max(ScenarioRevisionModel.revision_no)).where(
                        ScenarioRevisionModel.scenario_id == scenario_id
                    )
                )
                if latest != revision_no:
                    raise ComparisonError("COMPARISON_REQUIRES_CURRENT_REVISION")
                check_revision_size(session, revision.id, self.limits)
                prepared = _prepared(session, revision.id)
                validation = session.get(ImportValidationReportModel, revision.import_batch_id)
                if validation is None:
                    raise ComparisonError("REVISION_CONTEXT_INCOMPLETE")
                frozen_stock = self._stock(session, prepared)
                captured_at = session.scalar(select(func.clock_timestamp()))
                assert isinstance(captured_at, datetime)
                context = {
                    "revision_id": str(revision.id),
                    "normalized_revision": to_primitive(prepared),
                    "inventory": frozen_stock,
                    "versions": self.versions,
                    "source_validation": {
                        "import_batch_id": str(revision.import_batch_id),
                        "file_contract_version": validation.contract_version,
                        "validator_version": validation.validator_version,
                        "package_sha256": validation.package_sha256,
                        "report_sha256": validation.report_sha256,
                        "optimizer_contract_version": prepared.problem(
                            UUID(int=0), {}, quality, self.timeout_seconds
                        ).contract_version,
                    },
                    "parameters": {
                        "timeout_seconds": self.timeout_seconds,
                        "max_snap_distance_m": self.max_snap_distance_m,
                        "solution_quality": quality.value,
                    },
                    "simulation_only": True,
                    "captured_at": captured_at.isoformat(),
                }
                comparison_id, now = uuid4(), datetime.now(UTC)
                session.add(
                    PlanComparisonModel(
                        id=comparison_id,
                        scenario_id=scenario_id,
                        scenario_revision_id=revision.id,
                        client_key=key,
                        request_sha256=request_hash,
                        request_data=request,
                        context_sha256=_hash(context),
                        context_data=context,
                        created_at=now,
                    )
                )
                session.flush()
                session.add(
                    ComparisonJobModel(
                        comparison_id=comparison_id,
                        status="QUEUED",
                        version=0,
                        attempts=0,
                        next_attempt_at=now,
                        transitioned_at=now,
                    )
                )
                session.flush()
                session.add(
                    ComparisonEventModel(
                        id=uuid4(),
                        comparison_id=comparison_id,
                        sequence=0,
                        from_status=None,
                        to_status="QUEUED",
                        attempt_no=0,
                        reason="CONTEXT_CAPTURED",
                        occurred_at=now,
                    )
                )
                created = True
        return self.get(comparison_id), created

    @staticmethod
    def _stock(session: Session, prepared: PreparedRevision) -> list[dict[str, Any]]:
        positions = {
            (p.center_source_id, p.sku): p
            for p in session.scalars(
                select(OperationalInventoryPositionModel).where(
                    OperationalInventoryPositionModel.scenario_id == prepared.scenario_id
                )
            )
        }
        rows = session.execute(
            select(InventorySnapshotLineModel, DistributionCenterModel.source_id)
            .join(
                DistributionCenterModel,
                InventorySnapshotLineModel.distribution_center_id == DistributionCenterModel.id,
            )
            .where(InventorySnapshotLineModel.snapshot_id == prepared.snapshot_id)
            .order_by(DistributionCenterModel.source_id, InventorySnapshotLineModel.sku)
        )
        result = []
        for line, center in rows:
            position = positions.get((center, line.sku))
            held = position.routeops_reserved_quantity if position else 0
            # Follow activation semantics without performing activation or any stock write.
            stock = OperationalStock(
                center,
                line.sku,
                line.on_hand_quantity,
                line.externally_reserved_quantity,
                line.safety_stock_quantity,
                held,
            )
            if stock.available < 0:
                raise ComparisonError("INVENTORY_RECONCILIATION_CONFLICT")
            result.append(
                {
                    **to_primitive(stock),
                    "available": stock.available,
                    "source_snapshot_id": str(prepared.snapshot_id),
                    "source_snapshot_line_id": str(line.id),
                    "source_row_sha256": line.source_row_sha256,
                    "reservation_source_revision_id": str(position.source_revision_id)
                    if position
                    else None,
                    "reservation_position_updated_at": position.updated_at.isoformat()
                    if position
                    else None,
                }
            )
        return result

    def get(self, comparison_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            root = session.get(PlanComparisonModel, comparison_id)
            job = session.get(ComparisonJobModel, comparison_id)
            if root is None or job is None:
                raise ComparisonError("COMPARISON_NOT_FOUND", 404)
            if _hash(root.context_data) != root.context_sha256 or (
                _hash(root.request_data) != root.request_sha256
            ):
                raise ComparisonError("COMPARISON_FACTS_INVALID")
            result = session.get(ComparisonResultModel, comparison_id)
            if result and (
                _hash(result.document) != result.content_sha256
                or result.context_sha256 != root.context_sha256
            ):
                raise ComparisonError("COMPARISON_FACTS_INVALID")
            events = session.scalars(
                select(ComparisonEventModel)
                .where(ComparisonEventModel.comparison_id == comparison_id)
                .order_by(ComparisonEventModel.sequence)
            )
            return {
                "comparison_id": str(comparison_id),
                "scenario_id": str(root.scenario_id),
                "revision_id": str(root.scenario_revision_id),
                "status": job.status,
                "attempts": job.attempts,
                "context_sha256": root.context_sha256,
                "context": root.context_data,
                "manual_input": root.request_data,
                "result": result.document if result else None,
                "result_sha256": result.content_sha256 if result else None,
                "history": [
                    {
                        "sequence": e.sequence,
                        "from": e.from_status,
                        "to": e.to_status,
                        "attempt_no": e.attempt_no,
                        "reason": e.reason,
                        "at": e.occurred_at.isoformat(),
                    }
                    for e in events
                ],
            }

    def list(self, scenario_id: UUID, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ComparisonError("PAGINATION_INVALID", 422)
        with self.sessions() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            base = select(PlanComparisonModel).where(PlanComparisonModel.scenario_id == scenario_id)
            total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = session.execute(
                select(PlanComparisonModel, ComparisonJobModel.status)
                .join(
                    ComparisonJobModel, PlanComparisonModel.id == ComparisonJobModel.comparison_id
                )
                .where(PlanComparisonModel.scenario_id == scenario_id)
                .order_by(PlanComparisonModel.created_at.desc(), PlanComparisonModel.id)
                .offset(offset)
                .limit(limit)
            )
            return {
                "total": total,
                "next_offset": offset + limit if offset + limit < total else None,
                "items": [
                    {
                        "comparison_id": str(row.id),
                        "status": state,
                        "revision_id": str(row.scenario_revision_id),
                        "context_sha256": row.context_sha256,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row, state in rows
                ],
            }

    @staticmethod
    def _transition(session: Session, job: ComparisonJobModel, target: str, reason: str) -> None:
        before = job.status
        job.status, job.version = target, job.version + 1
        job.transitioned_at = datetime.now(UTC)
        session.add(
            ComparisonEventModel(
                id=uuid4(),
                comparison_id=job.comparison_id,
                sequence=job.version,
                from_status=before,
                to_status=target,
                attempt_no=job.attempts,
                occurred_at=job.transitioned_at,
                reason=reason,
            )
        )

    def claim(self) -> tuple[UUID, UUID] | None:
        with self.sessions.begin() as session:
            now = datetime.now(UTC)
            job = session.scalar(
                select(ComparisonJobModel)
                .where(
                    (
                        (ComparisonJobModel.status == "QUEUED")
                        & (ComparisonJobModel.next_attempt_at <= now)
                    )
                    | (
                        (ComparisonJobModel.status == "RUNNING")
                        & (ComparisonJobModel.lease_until < now)
                    )
                )
                .order_by(ComparisonJobModel.next_attempt_at, ComparisonJobModel.comparison_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= self.max_attempts:
                self._transition(session, job, "FAILED", "COMPARISON_RETRY_LIMIT")
                job.lease_token, job.lease_until = None, None
                return None
            reason = "LEASE_RECOVERED" if job.status == "RUNNING" else "LEASE_CLAIMED"
            job.attempts += 1
            token = uuid4()
            job.lease_token, job.lease_until = token, now + timedelta(seconds=self.lease_seconds)
            self._transition(session, job, "RUNNING", reason)
            return job.comparison_id, token

    @staticmethod
    def _owned(job: ComparisonJobModel | None, token: UUID) -> bool:
        return bool(
            job is not None
            and job.status == "RUNNING"
            and job.lease_token == token
            and job.lease_until is not None
            and job.lease_until >= datetime.now(UTC)
        )

    @staticmethod
    def _lock(session: Session, comparison_id: UUID) -> ComparisonJobModel | None:
        return session.scalar(
            select(ComparisonJobModel)
            .where(ComparisonJobModel.comparison_id == comparison_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def heartbeat(self, comparison_id: UUID, token: UUID) -> bool:
        with self.sessions.begin() as session:
            job = self._lock(session, comparison_id)
            if not self._owned(job, token):
                return False
            assert job is not None
            job.lease_until = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            return True

    @contextmanager
    def _heartbeat(self, comparison_id: UUID, token: UUID) -> Iterator[None]:
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(max(1, self.lease_seconds // 3)):
                try:
                    if not self.heartbeat(comparison_id, token):
                        return
                except Exception:
                    logger.exception("comparison_heartbeat_failed")
                    return

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)

    def process_once(self) -> bool:
        claimed = self.claim()
        if claimed is None:
            return False
        comparison_id, token = claimed
        try:
            with self._heartbeat(comparison_id, token):
                document = self.evaluate(comparison_id)
                self.finish(comparison_id, token, document)
        except Exception as exc:
            logger.exception(
                "comparison_attempt_failed", extra={"comparison_id": str(comparison_id)}
            )
            code = exc.code if isinstance(exc, ComparisonError) else operational_failure_code(exc)
            self.fail(comparison_id, token, code)
        return True

    def finish(self, comparison_id: UUID, token: UUID, document: dict[str, Any]) -> bool:
        with self.sessions.begin() as session:
            job = self._lock(session, comparison_id)
            if not self._owned(job, token):
                return False
            assert job is not None
            root = session.get(PlanComparisonModel, comparison_id)
            assert root is not None
            session.add(
                ComparisonResultModel(
                    comparison_id=comparison_id,
                    context_sha256=root.context_sha256,
                    content_sha256=_hash(document),
                    document=document,
                    owner_token=token,
                    attempt_no=job.attempts,
                    created_at=datetime.now(UTC),
                )
            )
            # DB fence before transition; a later fault rolls back the whole outcome.
            session.flush()
            self._transition(session, job, "READY", "RESULT_COMMITTED")
            job.lease_token, job.lease_until = None, None
            return True

    def fail(self, comparison_id: UUID, token: UUID, code: str) -> bool:
        with self.sessions.begin() as session:
            job = self._lock(session, comparison_id)
            if not self._owned(job, token):
                return False
            assert job is not None
            terminal = job.attempts >= self.max_attempts or code in (
                "COMPARISON_CONTEXT_VERSION_UNSUPPORTED",
                "COMPARISON_FACTS_INVALID",
                "SOLVER_RESPONSE_INVALID",
                "SOLVER_INPUT_INVALID",
            )
            self._transition(session, job, "FAILED" if terminal else "QUEUED", code)
            job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=self.retry_seconds)
            job.lease_token, job.lease_until = None, None
            return True

    def evaluate(self, comparison_id: UUID) -> dict[str, Any]:
        # Materialize immutable inputs, then close the read transaction before OSRM/VROOM.
        with self.sessions() as session:
            root = session.get(PlanComparisonModel, comparison_id)
            if root is None:
                raise ComparisonError("COMPARISON_NOT_FOUND", 404)
            context, request, context_hash = (
                root.context_data,
                root.request_data,
                root.context_sha256,
            )
            prepared = _prepared(session, root.scenario_revision_id)
            if (
                _hash(context) != context_hash
                or _hash(request) != root.request_sha256
                or (_hash(to_primitive(prepared)) != _hash(context["normalized_revision"]))
            ):
                raise ComparisonError("COMPARISON_FACTS_INVALID")
        if context["versions"] != self.versions:
            raise ComparisonError("COMPARISON_CONTEXT_VERSION_UNSUPPORTED")
        parameters = context["parameters"]
        stock = tuple(
            OperationalStock(
                **{
                    k: row[k]
                    for k in (
                        "center_id",
                        "sku",
                        "on_hand",
                        "externally_reserved",
                        "safety_stock",
                        "routeops_reserved",
                    )
                }
            )
            for row in context["inventory"]
        )
        source = {
            "comparison_id": str(comparison_id),
            "context_sha256": context_hash,
            "revision_id": str(prepared.revision_id),
            "simulation_only": True,
            "source": "immutable_comparison_context",
        }
        manual = evaluate_manual(
            prepared,
            request["manual_routes"],
            stock,
            self.osrm,
            source,
            parameters["max_snap_distance_m"],
        )
        origins = tuple(c.location for c in prepared.centers)
        targets = tuple(o.location for o in prepared.orders)
        matrix = self.osrm.checked_duration_matrix(
            origins, targets, parameters["max_snap_distance_m"]
        )
        times = FrozenTravelTimes(origins, targets, matrix.durations)
        vehicles = tuple(
            OperationalVehicle(
                v.source_vehicle_id,
                v.distribution_center_id,
                v.capacity.units,
                Decimal(v.capacity.weight_grams) / 1000,
                Decimal(v.capacity.volume_cm3) / 1_000_000,
                v.skills,
            )
            for v in prepared.vehicles
        )
        alternatives: dict[str, Any] = {"manual": manual}
        for policy in POLICIES:
            decisions = OperationalAllocationPolicy(times, policy).allocate(
                prepared.centers, prepared.orders, vehicles, stock
            )
            assigned = {d.order_id: d.center_id for d in decisions if d.center_id is not None}
            problem = prepared.problem(
                comparison_id,
                assigned,
                SolutionQuality(parameters["solution_quality"]),
                parameters["timeout_seconds"],
            )
            result = (
                self.solver.solve(problem)
                if problem.tasks
                else RevisionRunService._empty_result(problem)
            )
            reconcile_result(problem, result, parameters["max_snap_distance_m"])
            omitted = tuple(
                UnassignedTask(
                    None,
                    d.order_id,
                    "ALLOCATION",
                    (
                        UnassignedReason(
                            d.reason_code or "STOCK_NO_FULL_COVERAGE",
                            Certainty.PROVEN,
                            "Recorded simulated allocation exclusion.",
                            d.evidence,
                        ),
                    ),
                )
                for d in decisions
                if d.center_id is None
            )
            result = replace(
                result,
                unassigned=result.unassigned + omitted,
                status=ResultStatus.PARTIAL
                if result.unassigned or omitted
                else ResultStatus.SUCCEEDED,
                summary=replace(
                    result.summary, unassigned_task_count=len(result.unassigned) + len(omitted)
                ),
            )
            result = diagnose_result(
                result,
                problem,
                {d.order_id: d.evidence for d in decisions},
                {**source, "policy_version": policy},
            )
            if problem.tasks and (
                result.solver.engine_version != self.versions["solver"]
                or result.solver.adapter_version != self.versions["adapter"]
                or result.solver.routing_engine_version != self.versions["osrm"]
            ):
                raise ComparisonError("COMPARISON_CONTEXT_VERSION_UNSUPPORTED")
            data = to_primitive(result)
            for route in data["routes"]:
                route["departure_condition"] = {
                    "policy": OPTIMIZED_DEPARTURE,
                    "departure_at": route["steps"][0]["departure_at"],
                    "scope": "optimized_plan_condition",
                }
            objective = {
                "units": result.summary.objective_cost_units,
                "scale": result.summary.cost_scale,
                "currency": result.summary.currency,
                "semantics": "VROOM fixed + driving + distance, not full operating cost",
            }
            routed = sorted(
                s["order_id"] for r in data["routes"] for s in r["steps"] if s["kind"] == "DELIVERY"
            )
            metrics = metrics_for_plan(
                prepared,
                data["routes"],
                set(assigned),
                objective if problem.tasks else None,
                source,
            )
            alternatives[policy] = {
                "kind": "OPTIMIZED",
                "departure_policy": OPTIMIZED_DEPARTURE,
                "feasible": True,
                "policy_version": policy,
                "routes": data["routes"],
                "result": data,
                "decisions": to_primitive(decisions),
                "routed_order_ids": routed,
                "metrics": metrics,
                "solver_objective": objective if problem.tasks else None,
                "provenance": source,
                "inventory_initial_sha256": _hash(context["inventory"]),
            }
        manual["inventory_initial_sha256"] = _hash(context["inventory"])
        return {
            "calculation_version": COMPARISON_VERSION,
            "context_sha256": context_hash,
            "simulation_only": True,
            "alternatives": alternatives,
            "allocation_matrix": {
                "durations": to_primitive(matrix.durations),
                "snaps": matrix.snaps,
            },
            "comparisons": {
                f"{a}__{b}": differences(alternatives[a], alternatives[b])
                for a, b in (("manual", POLICIES[0]), ("manual", POLICIES[1]), POLICIES)
            },
        }
