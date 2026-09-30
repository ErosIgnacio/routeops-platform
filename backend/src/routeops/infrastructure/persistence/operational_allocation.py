"""Transactional allocation and RouteOps reservations for published revisions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from routeops.domain.models import Coordinate, DistributionCenter, Order, OrderLine
from routeops.domain.policies.allocation import TravelTimeProvider
from routeops.domain.policies.operational_allocation import (
    OperationalAllocationPolicy,
    OperationalStock,
    OperationalVehicle,
)
from routeops.infrastructure.persistence.models import (
    AllocationAttemptEventModel,
    AllocationAttemptModel,
    AllocationDecisionSnapshotModel,
    AllocationOrderDecisionModel,
    AllocationReservationLineModel,
    DistributionCenterModel,
    InventorySnapshotLineModel,
    InventorySnapshotModel,
    OperationalInventoryPositionModel,
    OperationalInventoryStateModel,
    OrderLineModel,
    OrderModel,
    ScenarioModel,
    ScenarioRevisionModel,
    VehicleModel,
)
from routeops.infrastructure.routing.errors import RoutingDependencyError


class AllocationError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class OperationalAllocationService:
    def __init__(self, sessions: sessionmaker[Session], travel_times: TravelTimeProvider) -> None:
        self.sessions = sessions
        self.travel_times = travel_times

    def allocate(
        self,
        scenario_id: UUID,
        revision_no: int,
        client_key: str,
        *,
        policy_version: str = OperationalAllocationPolicy.ALTERNATIVES,
        actor: str = "routeops",
    ) -> dict[str, Any]:
        if not 1 <= len(client_key) <= 100 or not 1 <= len(actor) <= 100:
            raise AllocationError("ALLOCATION_KEY_INVALID", 422)
        try:
            policy = OperationalAllocationPolicy(self.travel_times, policy_version)
        except ValueError as exc:
            raise AllocationError("ALLOCATION_POLICY_UNKNOWN", 422) from exc
        attempt_id: UUID | None = None
        try:
            with self.sessions.begin() as session:
                scenario = session.scalar(
                    select(ScenarioModel).where(ScenarioModel.id == scenario_id).with_for_update()
                )
                if scenario is None or scenario.status != "ACTIVE":
                    raise AllocationError("SCENARIO_NOT_ACTIVE", 404)
                revision = session.scalar(
                    select(ScenarioRevisionModel).where(
                        ScenarioRevisionModel.scenario_id == scenario_id,
                        ScenarioRevisionModel.revision_no == revision_no,
                    )
                )
                if revision is None:
                    raise AllocationError("REVISION_NOT_FOUND", 404)
                existing = session.scalar(
                    select(AllocationAttemptModel).where(
                        AllocationAttemptModel.scenario_revision_id == revision.id,
                        AllocationAttemptModel.client_key == client_key,
                    )
                )
                if existing is not None:
                    if existing.policy_version != policy_version:
                        raise AllocationError("ALLOCATION_IDEMPOTENCY_CONFLICT")
                    attempt_id = existing.id
                else:
                    latest = session.scalar(
                        select(func.max(ScenarioRevisionModel.revision_no)).where(
                            ScenarioRevisionModel.scenario_id == scenario_id
                        )
                    )
                    if latest != revision_no:
                        raise AllocationError("REVISION_NOT_CURRENT")
                    snapshot = session.scalar(
                        select(InventorySnapshotModel).where(
                            InventorySnapshotModel.scenario_revision_id == revision.id,
                            InventorySnapshotModel.kind == "IMPORTED",
                        )
                    )
                    if snapshot is None:
                        raise AllocationError("INVENTORY_SNAPSHOT_MISSING")
                    current_keys = self._activate_inventory(
                        session, scenario_id, revision, snapshot
                    )
                    positions = list(
                        session.scalars(
                            select(OperationalInventoryPositionModel)
                            .where(OperationalInventoryPositionModel.scenario_id == scenario_id)
                            .order_by(
                                OperationalInventoryPositionModel.center_source_id,
                                OperationalInventoryPositionModel.sku,
                            )
                            .with_for_update()
                        )
                    )
                    stock = tuple(
                        OperationalStock(
                            item.center_source_id,
                            item.sku,
                            item.on_hand_quantity,
                            item.externally_reserved_quantity,
                            item.safety_stock_quantity,
                            item.routeops_reserved_quantity,
                        )
                        for item in positions
                        if (item.center_source_id, item.sku) in current_keys
                    )
                    centers, orders, vehicles, order_ids, line_ids = self._load_revision(
                        session, revision
                    )
                    try:
                        decisions = policy.allocate(centers, orders, vehicles, stock)
                    except RoutingDependencyError as exc:
                        raise AllocationError("ROUTING_DEPENDENCY_FAILED", 503) from exc
                    relevant_skus = {line.sku for order in orders for line in order.lines}
                    inventory_data: dict[str, Any] = {
                        "scenario_revision_id": str(revision.id),
                        "source_snapshot_id": str(snapshot.id),
                        "source_snapshot_sha256": snapshot.content_sha256,
                        "positions": [
                            {
                                "center_id": row.center_id,
                                "sku": row.sku,
                                "on_hand": row.on_hand,
                                "externally_reserved": row.externally_reserved,
                                "safety_stock": row.safety_stock,
                                "routeops_reserved": row.routeops_reserved,
                                "available": row.available,
                            }
                            for row in stock
                            if row.sku in relevant_skus
                        ],
                    }
                    fingerprint = hashlib.sha256(
                        json.dumps(inventory_data, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    now = datetime.now(UTC)
                    attempt_id = uuid4()
                    attempt = AllocationAttemptModel(
                        id=attempt_id,
                        scenario_id=scenario_id,
                        scenario_revision_id=revision.id,
                        inventory_snapshot_id=snapshot.id,
                        client_key=client_key,
                        policy_version=policy_version,
                        status="BUILDING",
                        version=0,
                        created_at=now,
                        transitioned_at=now,
                    )
                    session.add(attempt)
                    session.flush()
                    session.add(
                        AllocationAttemptEventModel(
                            id=uuid4(),
                            attempt_id=attempt_id,
                            sequence=0,
                            from_status=None,
                            to_status="BUILDING",
                            occurred_at=now,
                            actor=actor,
                        )
                    )
                    session.flush()
                    session.add(
                        AllocationDecisionSnapshotModel(
                            attempt_id=attempt_id,
                            inventory_sha256=fingerprint,
                            inventory_data=inventory_data,
                            created_at=now,
                        )
                    )
                    by_order = {order.id: order for order in orders}
                    for decision in decisions:
                        decision_id = uuid4()
                        session.add(
                            AllocationOrderDecisionModel(
                                id=decision_id,
                                attempt_id=attempt_id,
                                order_id=order_ids[decision.order_id],
                                center_source_id=decision.center_id,
                                reason_code=decision.reason_code,
                                sequence=decision.evidence["sequence"],
                                evidence=decision.evidence,
                            )
                        )
                        if decision.center_id is not None:
                            for line in by_order[decision.order_id].lines:
                                session.add(
                                    AllocationReservationLineModel(
                                        id=uuid4(),
                                        attempt_id=attempt_id,
                                        decision_id=decision_id,
                                        order_line_id=line_ids[(decision.order_id, line.sku)],
                                        scenario_id=scenario_id,
                                        center_source_id=decision.center_id,
                                        sku=line.sku,
                                        quantity=line.quantity,
                                    )
                                )
                    session.flush()
                    final_at = datetime.now(UTC)
                    attempt.status = "HELD"
                    attempt.version = 1
                    attempt.transitioned_at = final_at
                    session.add(
                        AllocationAttemptEventModel(
                            id=uuid4(),
                            attempt_id=attempt_id,
                            sequence=1,
                            from_status="BUILDING",
                            to_status="HELD",
                            occurred_at=final_at,
                            actor=actor,
                        )
                    )
            assert attempt_id is not None
            return self.get(attempt_id)
        except IntegrityError as exc:
            diagnostic = getattr(exc.orig, "diag", None)
            primary = getattr(diagnostic, "message_primary", "")
            constraint = getattr(diagnostic, "constraint_name", "")
            if primary == "operational stock conflict" or constraint == (
                "ck_operational_inventory_available"
            ):
                raise AllocationError("INVENTORY_CONCURRENT_CONFLICT") from exc
            raise AllocationError("ALLOCATION_PERSISTENCE_FAILED", 503) from exc

    def confirm(self, attempt_id: UUID, *, actor: str = "routeops") -> dict[str, Any]:
        return self._transition(attempt_id, "CONFIRMED", actor)

    def release(self, attempt_id: UUID, *, actor: str = "routeops") -> dict[str, Any]:
        return self._transition(attempt_id, "RELEASED", actor)

    def _transition(self, attempt_id: UUID, target: str, actor: str) -> dict[str, Any]:
        if not 1 <= len(actor) <= 100:
            raise AllocationError("ALLOCATION_ACTOR_INVALID", 422)
        with self.sessions.begin() as session:
            identity = session.get(AllocationAttemptModel, attempt_id)
            if identity is None:
                raise AllocationError("ALLOCATION_NOT_FOUND", 404)
            session.scalar(
                select(ScenarioModel)
                .where(ScenarioModel.id == identity.scenario_id)
                .with_for_update()
            )
            attempt = session.scalar(
                select(AllocationAttemptModel)
                .where(AllocationAttemptModel.id == attempt_id)
                .with_for_update()
            )
            assert attempt is not None
            if attempt.status == target:
                return self.get(attempt_id)
            if target == "CONFIRMED" and attempt.status != "HELD":
                raise AllocationError("ALLOCATION_TRANSITION_INVALID")
            if target == "RELEASED" and attempt.status not in ("HELD", "CONFIRMED"):
                raise AllocationError("ALLOCATION_TRANSITION_INVALID")
            before = attempt.status
            attempt.status = target
            attempt.version += 1
            attempt.transitioned_at = datetime.now(UTC)
            session.add(
                AllocationAttemptEventModel(
                    id=uuid4(),
                    attempt_id=attempt_id,
                    sequence=attempt.version,
                    from_status=before,
                    to_status=target,
                    occurred_at=attempt.transitioned_at,
                    actor=actor,
                )
            )
        return self.get(attempt_id)

    def get(self, attempt_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            attempt = session.get(AllocationAttemptModel, attempt_id)
            if attempt is None:
                raise AllocationError("ALLOCATION_NOT_FOUND", 404)
            snapshot = session.get(AllocationDecisionSnapshotModel, attempt_id)
            decisions = list(
                session.scalars(
                    select(AllocationOrderDecisionModel)
                    .where(AllocationOrderDecisionModel.attempt_id == attempt_id)
                    .order_by(AllocationOrderDecisionModel.sequence)
                )
            )
            reservations = list(
                session.scalars(
                    select(AllocationReservationLineModel).where(
                        AllocationReservationLineModel.attempt_id == attempt_id
                    )
                )
            )
            events = list(
                session.scalars(
                    select(AllocationAttemptEventModel)
                    .where(AllocationAttemptEventModel.attempt_id == attempt_id)
                    .order_by(AllocationAttemptEventModel.sequence)
                )
            )
            return {
                "id": str(attempt.id),
                "scenario_id": str(attempt.scenario_id),
                "scenario_revision_id": str(attempt.scenario_revision_id),
                "inventory_snapshot_id": str(attempt.inventory_snapshot_id),
                "policy_version": attempt.policy_version,
                "status": attempt.status,
                "inventory_sha256": snapshot.inventory_sha256 if snapshot else None,
                "inventory_snapshot": snapshot.inventory_data if snapshot else None,
                "decisions": [
                    {
                        "order_id": str(row.order_id),
                        "center_id": row.center_source_id,
                        "reason_code": row.reason_code,
                        "evidence": row.evidence,
                    }
                    for row in decisions
                ],
                "reservations": [
                    {
                        "decision_id": str(row.decision_id),
                        "order_line_id": str(row.order_line_id),
                        "center_id": row.center_source_id,
                        "sku": row.sku,
                        "quantity": row.quantity,
                    }
                    for row in reservations
                ],
                "events": [
                    {"sequence": row.sequence, "from": row.from_status, "to": row.to_status}
                    for row in events
                ],
            }

    @staticmethod
    def _activate_inventory(
        session: Session,
        scenario_id: UUID,
        revision: ScenarioRevisionModel,
        snapshot: InventorySnapshotModel,
    ) -> set[tuple[str, str]]:
        rows = list(
            session.execute(
                select(InventorySnapshotLineModel, DistributionCenterModel.source_id)
                .join(
                    DistributionCenterModel,
                    InventorySnapshotLineModel.distribution_center_id == DistributionCenterModel.id,
                )
                .where(InventorySnapshotLineModel.snapshot_id == snapshot.id)
                .order_by(DistributionCenterModel.source_id, InventorySnapshotLineModel.sku)
            )
        )
        state = session.get(OperationalInventoryStateModel, scenario_id, with_for_update=True)
        keys = {(center_id, line.sku) for line, center_id in rows}
        if state is not None and state.active_revision_id == revision.id:
            return keys
        now = datetime.now(UTC)
        for line, center_id in rows:
            position = session.get(
                OperationalInventoryPositionModel,
                (scenario_id, center_id, line.sku),
                with_for_update=True,
            )
            base_available = (
                line.on_hand_quantity
                - line.externally_reserved_quantity
                - line.safety_stock_quantity
            )
            if position is None:
                session.add(
                    OperationalInventoryPositionModel(
                        scenario_id=scenario_id,
                        center_source_id=center_id,
                        sku=line.sku,
                        source_revision_id=revision.id,
                        source_snapshot_line_id=line.id,
                        on_hand_quantity=line.on_hand_quantity,
                        externally_reserved_quantity=line.externally_reserved_quantity,
                        safety_stock_quantity=line.safety_stock_quantity,
                        routeops_reserved_quantity=0,
                        updated_at=now,
                    )
                )
            else:
                if position.routeops_reserved_quantity > base_available:
                    raise AllocationError("INVENTORY_RECONCILIATION_CONFLICT")
                position.source_revision_id = revision.id
                position.source_snapshot_line_id = line.id
                position.on_hand_quantity = line.on_hand_quantity
                position.externally_reserved_quantity = line.externally_reserved_quantity
                position.safety_stock_quantity = line.safety_stock_quantity
                position.updated_at = now
        if state is None:
            session.add(
                OperationalInventoryStateModel(
                    scenario_id=scenario_id,
                    active_revision_id=revision.id,
                    snapshot_id=snapshot.id,
                    activated_at=now,
                )
            )
        else:
            state.active_revision_id = revision.id
            state.snapshot_id = snapshot.id
            state.activated_at = now
        session.flush()
        return keys

    @staticmethod
    def _load_revision(
        session: Session,
        revision: ScenarioRevisionModel,
    ) -> tuple[
        tuple[DistributionCenter, ...],
        tuple[Order, ...],
        tuple[OperationalVehicle, ...],
        dict[str, UUID],
        dict[tuple[str, str], UUID],
    ]:
        centers_raw = list(
            session.execute(
                select(
                    DistributionCenterModel,
                    func.ST_Y(DistributionCenterModel.location),
                    func.ST_X(DistributionCenterModel.location),
                ).where(DistributionCenterModel.scenario_revision_id == revision.id)
            )
        )
        center_ids = {row.id: row.source_id for row, _, _ in centers_raw}
        centers = tuple(
            DistributionCenter(row.source_id, row.name, Coordinate(float(lat), float(lon)))
            for row, lat, lon in centers_raw
        )
        lines_by_order: dict[UUID, list[OrderLine]] = {}
        line_ids: dict[tuple[str, str], UUID] = {}
        order_rows = list(
            session.execute(
                select(
                    OrderModel, func.ST_Y(OrderModel.location), func.ST_X(OrderModel.location)
                ).where(OrderModel.scenario_revision_id == revision.id)
            )
        )
        order_ids = {row.source_id: row.id for row, _, _ in order_rows}
        source_by_id = {row.id: row.source_id for row, _, _ in order_rows}
        for line in session.scalars(
            select(OrderLineModel).where(OrderLineModel.scenario_revision_id == revision.id)
        ):
            lines_by_order.setdefault(line.order_id, []).append(
                OrderLine(line.sku, line.quantity, line.unit_weight_kg, line.unit_volume_m3)
            )
            line_ids[(source_by_id[line.order_id], line.sku)] = line.id
        orders = tuple(
            Order(
                id=row.source_id,
                customer_reference=row.customer_reference,
                location=Coordinate(float(lat), float(lon)),
                priority=row.priority,
                time_window_start=row.time_window_start,
                time_window_end=row.time_window_end,
                service_seconds=row.service_minutes * 60,
                required_skills=frozenset(row.required_skills),
                lines=tuple(sorted(lines_by_order.get(row.id, []), key=lambda line: line.sku)),
            )
            for row, lat, lon in order_rows
        )
        vehicles = tuple(
            OperationalVehicle(
                id=row.source_id,
                distribution_center_id=center_ids[row.distribution_center_id],
                capacity_units=row.capacity_units,
                capacity_weight_kg=row.capacity_weight_kg,
                capacity_volume_m3=row.capacity_volume_m3,
                skills=frozenset(row.skills),
            )
            for row in session.scalars(
                select(VehicleModel).where(VehicleModel.scenario_revision_id == revision.id)
            )
        )
        return centers, orders, vehicles, order_ids, line_ids
