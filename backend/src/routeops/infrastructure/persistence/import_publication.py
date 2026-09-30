"""Atomic publication of a verified import as an immutable scenario revision."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS
from routeops.infrastructure.persistence.import_validation_jobs import (
    ValidationJobError,
    ValidationJobService,
)
from routeops.infrastructure.persistence.models import (
    DistributionCenterModel,
    ImportBatchEventModel,
    ImportBatchExpirationModel,
    ImportBatchModel,
    ImportFileModel,
    ImportValidationContextModel,
    ImportValidationReportModel,
    InventorySnapshotLineModel,
    InventorySnapshotModel,
    OrderLineModel,
    OrderModel,
    ScenarioModel,
    ScenarioRevisionModel,
    ValidationIssueModel,
    VehicleModel,
)

_CHUNK = 500


class PublicationError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$instant": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, frozenset):
        return {"$skills": sorted(value)}
    return value


def _decode_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "$instant" in value:
        return datetime.fromisoformat(value["$instant"])
    if "$time" in value:
        return time.fromisoformat(value["$time"])
    if "$decimal" in value:
        return Decimal(value["$decimal"])
    if "$skills" in value:
        return value["$skills"]
    raise ValueError("invalid internal normalized value")


def _spooled_rows(
    stream: tempfile.SpooledTemporaryFile[bytes],
) -> Iterator[tuple[int, dict[str, Any], str]]:
    stream.seek(0)
    for line in stream:
        payload = json.loads(line)
        values = {key: _decode_value(value) for key, value in payload["values"].items()}
        row_sha256 = hashlib.sha256(
            json.dumps(payload["values"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        yield payload["row"], values, row_sha256


def _point(row: dict[str, Any]) -> WKTElement:
    return WKTElement(f"POINT ({row['longitude']} {row['latitude']})", srid=4326)


def _area_wkt(context: ValidationContext) -> WKTElement | None:
    area = context.operational_area
    if area is None:
        return None
    polygons = []
    for polygon in area["coordinates"]:
        rings = []
        for ring in polygon:
            rings.append("(" + ", ".join(f"{point[0]} {point[1]}" for point in ring) + ")")
        polygons.append("(" + ", ".join(rings) + ")")
    return WKTElement("MULTIPOLYGON (" + ", ".join(polygons) + ")", srid=4326)


def _insert_chunks(session: Session, model: type[Any], rows: Iterator[dict[str, Any]]) -> None:
    chunk: list[dict[str, Any]] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= _CHUNK:
            session.execute(model.__table__.insert(), chunk)
            chunk.clear()
    if chunk:
        session.execute(model.__table__.insert(), chunk)


class ImportPublicationService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        validation: ValidationJobService,
        spool_directory: Path,
        *,
        retention_days: int,
    ) -> None:
        self.sessions = sessions
        self.validation = validation
        self.spool_directory = spool_directory
        self.retention = timedelta(days=retention_days)

    @staticmethod
    def _result(revision: ScenarioRevisionModel) -> dict[str, Any]:
        return {
            "id": str(revision.id),
            "scenario_id": str(revision.scenario_id),
            "revision_no": revision.revision_no,
            "import_batch_id": str(revision.import_batch_id),
            "planning_date": revision.planning_date.isoformat(),
            "timezone_iana": revision.timezone_iana,
            "currency": revision.currency,
            "horizon_start_at": revision.horizon_start_at.isoformat(),
            "horizon_end_at": revision.horizon_end_at.isoformat(),
            "content_sha256": revision.content_sha256,
            "contract_version": revision.contract_version,
            "published_at": revision.published_at.isoformat(),
        }

    @staticmethod
    def _existing(session: Session, batch_id: UUID) -> ScenarioRevisionModel | None:
        return session.scalar(
            select(ScenarioRevisionModel).where(ScenarioRevisionModel.import_batch_id == batch_id)
        )

    def _published_result(self, scenario_id: UUID, batch_id: UUID) -> dict[str, Any] | None:
        with self.sessions() as session:
            batch = session.get(ImportBatchModel, batch_id)
            if batch is None or batch.scenario_id != scenario_id:
                raise PublicationError("BATCH_NOT_FOUND", 404)
            if batch.status != "PUBLISHED":
                if session.get(
                    ImportBatchExpirationModel, batch_id
                ) is not None or batch.transitioned_at + self.retention <= datetime.now(UTC):
                    raise PublicationError("BATCH_EXPIRED", 409)
                if batch.status != "VALID":
                    raise PublicationError("BATCH_NOT_VALID", 409)
                return None
            revision = self._existing(session, batch_id)
            if revision is None:
                raise PublicationError("PUBLICATION_INCONSISTENT", 409)
            return self._result(revision)

    def publish(self, scenario_id: UUID, batch_id: UUID) -> tuple[dict[str, Any], bool]:
        existing = self._published_result(scenario_id, batch_id)
        if existing is not None:
            return existing, False
        self.spool_directory.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            streams: dict[str, tempfile.SpooledTemporaryFile[bytes]] = {
                name: stack.enter_context(
                    tempfile.SpooledTemporaryFile(
                        max_size=1024 * 1024, mode="w+b", dir=str(self.spool_directory)
                    )
                )
                for name in DATASETS
            }
            counts = dict.fromkeys(DATASETS, 0)
            inventory_hash = hashlib.sha256()
            snapshot_at: datetime | None = None
            spooled_bytes = 0

            def capture(name: str, _source: str, row: int, values: dict[str, Any]) -> None:
                nonlocal snapshot_at, spooled_bytes
                payload = {
                    "row": row,
                    "values": {key: _encode_value(value) for key, value in values.items()},
                }
                line = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                spooled_bytes += len(line)
                if spooled_bytes > self.validation.limits.max_package_bytes * 8:
                    raise PublicationError("PUBLISH_NORMALIZED_LIMIT", 413)
                try:
                    streams[name].write(line)
                except OSError as exc:
                    raise PublicationError("PUBLISH_TEMPORARY_STORAGE_FAILED", 503) from exc
                counts[name] += 1
                if name == "inventory":
                    inventory_hash.update(line)
                    if snapshot_at is None:
                        snapshot_at = values.get("snapshot_at")

            try:
                files, context = self.validation.replay_verified_for_publication(
                    scenario_id, batch_id, row_sink=capture
                )
                del files
            except ValidationJobError as exc:
                existing = self._published_result(scenario_id, batch_id)
                if existing is not None:
                    return existing, False
                raise PublicationError(exc.code, exc.status_code) from exc
            with self.sessions.begin() as session:
                # Maintenance and validation acquire the batch first. Holding it
                # through commit also serializes simultaneous retries of this batch.
                batch = session.scalar(
                    select(ImportBatchModel)
                    .where(ImportBatchModel.id == batch_id)
                    .with_for_update()
                )
                if batch is None or batch.scenario_id != scenario_id:
                    raise PublicationError("BATCH_NOT_FOUND", 404)
                if batch.status == "PUBLISHED":
                    revision = self._existing(session, batch_id)
                    if revision is None:
                        raise PublicationError("PUBLICATION_INCONSISTENT", 409)
                    return self._result(revision), False
                now = datetime.now(UTC)
                if (
                    session.get(ImportBatchExpirationModel, batch_id) is not None
                    or batch.transitioned_at + self.retention <= now
                ):
                    raise PublicationError("BATCH_EXPIRED", 409)
                if batch.status != "VALID":
                    raise PublicationError("BATCH_NOT_VALID", 409)
                if not any(counts.values()):
                    raise PublicationError("PUBLISH_PACKAGE_EMPTY", 422)
                if not counts["inventory"] or snapshot_at is None:
                    raise PublicationError("PUBLISH_INVENTORY_EMPTY", 422)
                scenario = session.scalar(
                    select(ScenarioModel).where(ScenarioModel.id == scenario_id).with_for_update()
                )
                if scenario is None or scenario.status != "ACTIVE":
                    raise PublicationError("SCENARIO_NOT_ACTIVE", 409)
                report = session.get(ImportValidationReportModel, batch_id)
                context_row = session.get(ImportValidationContextModel, batch_id)
                if (
                    report is None
                    or context_row is None
                    or batch.package_sha256 != report.package_sha256
                    or context.sha256 != context_row.context_sha256
                    or context.sha256 != report.context_sha256
                    or context.contract_version != report.contract_version
                    or context.validator_version != report.validator_version
                    or any(report.counts[name]["accepted"] != counts[name] for name in DATASETS)
                    or session.scalar(
                        select(ValidationIssueModel.id)
                        .where(
                            ValidationIssueModel.batch_id == batch_id,
                            ValidationIssueModel.severity == "ERROR",
                        )
                        .limit(1)
                    )
                    is not None
                ):
                    raise PublicationError("VALIDATED_SOURCE_MISMATCH", 409)
                revision_no = (
                    session.scalar(
                        select(func.max(ScenarioRevisionModel.revision_no)).where(
                            ScenarioRevisionModel.scenario_id == scenario_id
                        )
                    )
                    or 0
                ) + 1
                revision_id = uuid4()
                revision = ScenarioRevisionModel(
                    id=revision_id,
                    scenario_id=scenario_id,
                    import_batch_id=batch_id,
                    revision_no=revision_no,
                    planning_date=context.planning_date,
                    timezone_iana=context.timezone_iana,
                    currency=context.currency,
                    horizon_start_at=context.horizon_start_at,
                    horizon_end_at=context.horizon_end_at,
                    operational_area=_area_wkt(context),
                    content_sha256=batch.package_sha256,
                    contract_version=context.contract_version,
                    published_at=now,
                )
                session.add(revision)
                session.flush()
                source_ids = {
                    row.dataset: row.id
                    for row in session.scalars(
                        select(ImportFileModel).where(ImportFileModel.batch_id == batch_id)
                    )
                }
                if "workbook" in source_ids:
                    source_ids = dict.fromkeys(DATASETS, source_ids["workbook"])
                if set(source_ids) != set(DATASETS):
                    raise PublicationError("VALIDATED_SOURCE_MISMATCH", 409)
                self._insert_datasets(
                    session,
                    streams,
                    source_ids,
                    batch_id,
                    revision_id,
                    snapshot_at,
                    inventory_hash.hexdigest(),
                    now,
                )
                previous = batch.status
                batch.status = "PUBLISHED"
                batch.version += 1
                batch.transitioned_at = now
                session.add(
                    ImportBatchEventModel(
                        id=uuid4(),
                        batch_id=batch_id,
                        sequence=batch.version,
                        from_status=previous,
                        to_status="PUBLISHED",
                        occurred_at=now,
                    )
                )
                scenario.version += 1
                scenario.updated_at = now
                return self._result(revision), True

    @staticmethod
    def _insert_datasets(
        session: Session,
        streams: dict[str, tempfile.SpooledTemporaryFile[bytes]],
        source_ids: dict[str, UUID],
        batch_id: UUID,
        revision_id: UUID,
        snapshot_at: datetime,
        inventory_sha256: str,
        now: datetime,
    ) -> None:
        centers: dict[str, UUID] = {}
        orders: dict[str, UUID] = {}

        def rows(name: str) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
            for row_number, value, row_hash in _spooled_rows(streams[name]):
                yield (
                    value,
                    {
                        "id": uuid4(),
                        "scenario_revision_id": revision_id,
                        "import_batch_id": batch_id,
                        "source_import_file_id": source_ids[name],
                        "source_row_number": row_number,
                        "source_row_sha256": row_hash,
                        "created_at": now,
                    },
                )

        def center_rows() -> Iterator[dict[str, Any]]:
            for value, base in rows("distribution_centers"):
                centers[value["distribution_center_id"]] = base["id"]
                yield base | {
                    "source_id": value["distribution_center_id"],
                    "name": value["name"],
                    "location": _point(value),
                    "operating_start": value["operating_start"],
                    "operating_end": value["operating_end"],
                }

        def order_rows() -> Iterator[dict[str, Any]]:
            for value, base in rows("orders"):
                orders[value["order_id"]] = base["id"]
                yield base | {
                    "source_id": value["order_id"],
                    "customer_reference": value["customer_reference"],
                    "location": _point(value),
                    "priority": value["priority"],
                    "time_window_start": value["time_window_start"],
                    "time_window_end": value["time_window_end"],
                    "service_minutes": value["service_minutes"],
                    "required_skills": value.get("required_skills", []),
                }

        _insert_chunks(session, DistributionCenterModel, center_rows())
        _insert_chunks(session, OrderModel, order_rows())
        snapshot_id = uuid4()
        session.add(
            InventorySnapshotModel(
                id=snapshot_id,
                scenario_revision_id=revision_id,
                import_batch_id=batch_id,
                kind="IMPORTED",
                snapshot_at=snapshot_at,
                content_sha256=inventory_sha256,
                created_at=now,
            )
        )
        session.flush()

        def line_rows() -> Iterator[dict[str, Any]]:
            for value, base in rows("order_lines"):
                yield base | {
                    "order_id": orders[value["order_id"]],
                    "sku": value["sku"],
                    "quantity": value["quantity"],
                    "unit_weight_kg": value["unit_weight_kg"],
                    "unit_volume_m3": value["unit_volume_m3"],
                }

        def vehicle_rows() -> Iterator[dict[str, Any]]:
            for value, base in rows("vehicles"):
                yield base | {
                    "source_id": value["vehicle_id"],
                    "distribution_center_id": centers[value["distribution_center_id"]],
                    "vehicle_type": value["vehicle_type"],
                    "capacity_units": value["capacity_units"],
                    "capacity_weight_kg": value["capacity_weight_kg"],
                    "capacity_volume_m3": value["capacity_volume_m3"],
                    "shift_start": value["shift_start"],
                    "shift_end": value["shift_end"],
                    "skills": value.get("skills", []),
                    "fixed_cost": value["fixed_cost"],
                    "cost_per_hour": value["cost_per_hour"],
                    "cost_per_km": value["cost_per_km"],
                }

        def inventory_rows() -> Iterator[dict[str, Any]]:
            for value, base in rows("inventory"):
                del base["id"]
                on_hand = value["on_hand_quantity"]
                external = value["externally_reserved_quantity"]
                safety = value["safety_stock_quantity"]
                yield base | {
                    "id": uuid4(),
                    "snapshot_id": snapshot_id,
                    "distribution_center_id": centers[value["distribution_center_id"]],
                    "sku": value["sku"],
                    "on_hand_quantity": on_hand,
                    "externally_reserved_quantity": external,
                    "safety_stock_quantity": safety,
                    "routeops_reserved_quantity": 0,
                    "available_quantity": on_hand - external - safety,
                }

        _insert_chunks(session, OrderLineModel, line_rows())
        _insert_chunks(session, VehicleModel, vehicle_rows())
        _insert_chunks(session, InventorySnapshotLineModel, inventory_rows())

    def list_revisions(
        self, scenario_id: UUID, *, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        if after < 0 or not 1 <= limit <= 200:
            raise PublicationError("PAGINATION_INVALID", 422)
        with self.sessions() as session:
            if session.get(ScenarioModel, scenario_id) is None:
                raise PublicationError("SCENARIO_NOT_FOUND", 404)
            revisions = list(
                session.scalars(
                    select(ScenarioRevisionModel)
                    .where(
                        ScenarioRevisionModel.scenario_id == scenario_id,
                        ScenarioRevisionModel.revision_no > after,
                    )
                    .order_by(ScenarioRevisionModel.revision_no)
                    .limit(limit + 1)
                )
            )
            return {
                "items": [self._result(row) for row in revisions[:limit]],
                "next_after": revisions[limit - 1].revision_no if len(revisions) > limit else None,
            }

    def get_revision(self, scenario_id: UUID, revision_no: int) -> dict[str, Any]:
        with self.sessions() as session:
            revision = session.scalar(
                select(ScenarioRevisionModel).where(
                    ScenarioRevisionModel.scenario_id == scenario_id,
                    ScenarioRevisionModel.revision_no == revision_no,
                )
            )
            if revision is None:
                raise PublicationError("REVISION_NOT_FOUND", 404)
            report = session.get(ImportValidationReportModel, revision.import_batch_id)
            context = session.get(ImportValidationContextModel, revision.import_batch_id)
            snapshot = session.scalar(
                select(InventorySnapshotModel).where(
                    InventorySnapshotModel.scenario_revision_id == revision.id,
                    InventorySnapshotModel.kind == "IMPORTED",
                )
            )
            return self._result(revision) | {
                "context_sha256": context.context_sha256 if context else None,
                "report_sha256": report.report_sha256 if report else None,
                "counts": report.counts if report else None,
                "snapshot_at": snapshot.snapshot_at.isoformat() if snapshot else None,
                "operational_area": (
                    context.context_data.get("operational_area") if context else None
                ),
            }
