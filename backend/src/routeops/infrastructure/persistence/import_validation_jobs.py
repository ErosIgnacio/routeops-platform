"""PostgreSQL-coordinated validation of immutable private import originals."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.import_context import ValidationContext
from routeops.application.import_validation import ImportLimits, ValidationReport, validate_package
from routeops.application.ports.object_storage import ObjectStorageGateway
from routeops.infrastructure.persistence.models import (
    ImportBatchEventModel,
    ImportBatchExpirationModel,
    ImportBatchModel,
    ImportFileModel,
    ImportValidationContextModel,
    ImportValidationJobModel,
    ImportValidationReportModel,
    ValidationIssueModel,
)

logger = logging.getLogger("routeops.import_validation_worker")
CHECKED_RULES = [
    "structure_and_relationships",
    "stock_snapshot_coherence",
    "planning_horizon",
    "local_time_dst",
    "persistent_field_ranges_and_precision",
    "operational_area_warning_if_supplied",
]
DEFERRED_RULES = [
    "atomic_publication_and_revision_constraints",
    "allocation_and_reservations",
    "route_and_fleet_feasibility",
]


class ValidationJobError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class ValidationJobService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: ObjectStorageGateway,
        limits: ImportLimits,
        *,
        retention_days: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> None:
        if min(retention_days, lease_seconds, max_attempts) <= 0:
            raise ValueError("validation worker settings must be positive")
        self.sessions = sessions
        self.storage = storage
        self.limits = limits
        self.retention = timedelta(days=retention_days)
        self.lease = timedelta(seconds=lease_seconds)
        self.max_attempts = max_attempts

    def _expired(self, session: Session, batch: ImportBatchModel, now: datetime) -> bool:
        return (
            session.get(ImportBatchExpirationModel, batch.id) is not None
            or batch.transitioned_at + self.retention <= now
        )

    def request(
        self, scenario_id: UUID, batch_id: UUID, context: ValidationContext
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            batch = session.scalar(
                select(ImportBatchModel).where(ImportBatchModel.id == batch_id).with_for_update()
            )
            if batch is None or batch.scenario_id != scenario_id:
                raise ValidationJobError("BATCH_NOT_FOUND", 404)
            now = datetime.now(UTC)
            if self._expired(session, batch, now):
                raise ValidationJobError("BATCH_EXPIRED", 409)
            if batch.parser_version != context.contract_version:
                raise ValidationJobError("VALIDATION_VERSION_CONFLICT", 409)
            existing = session.get(ImportValidationContextModel, batch_id)
            if existing is not None:
                if existing.context_sha256 != context.sha256:
                    raise ValidationJobError("VALIDATION_CONTEXT_CONFLICT", 409)
            else:
                if batch.status != "RECEIVED":
                    raise ValidationJobError("BATCH_NOT_RECEIVED", 409)
                if context.operational_area is not None:
                    geometry = json.dumps(context.operational_area, separators=(",", ":"))
                    valid = session.scalar(
                        select(
                            text(
                                "ST_IsValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326)) "
                                "AND NOT ST_IsEmpty(ST_GeomFromGeoJSON(:geometry))"
                            ).bindparams(geometry=geometry)
                        )
                    )
                    if not valid:
                        raise ValidationJobError("CONTEXT_AREA_INVALID", 422)
                session.add(
                    ImportValidationContextModel(
                        batch_id=batch_id,
                        context_sha256=context.sha256,
                        context_data=context.canonical(),
                        created_at=now,
                    )
                )
                session.flush()
                session.add(ImportValidationJobModel(batch_id=batch_id, attempts=0, updated_at=now))
                batch.status = "VALIDATING"
                batch.version += 1
                batch.transitioned_at = now
                session.add(
                    ImportBatchEventModel(
                        id=uuid4(),
                        batch_id=batch_id,
                        sequence=batch.version,
                        from_status="RECEIVED",
                        to_status="VALIDATING",
                        occurred_at=now,
                    )
                )
        return self.get(scenario_id, batch_id)

    def get(self, scenario_id: UUID, batch_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            batch = session.get(ImportBatchModel, batch_id)
            if batch is None or batch.scenario_id != scenario_id:
                raise ValidationJobError("BATCH_NOT_FOUND", 404)
            context = session.get(ImportValidationContextModel, batch_id)
            job = session.get(ImportValidationJobModel, batch_id)
            report = session.get(ImportValidationReportModel, batch_id)
            expired = self._expired(session, batch, datetime.now(UTC))
            return {
                "batch_id": str(batch_id),
                "status": "EXPIRED" if expired and batch.status != "PUBLISHED" else batch.status,
                "context_sha256": context.context_sha256 if context else None,
                "package_sha256": batch.package_sha256,
                "contract_version": context.context_data["contract_version"] if context else None,
                "validator_version": context.context_data["validator_version"] if context else None,
                "attempts": job.attempts if job else 0,
                "max_attempts": self.max_attempts,
                "lease_until": job.lease_until.isoformat() if job and job.lease_until else None,
                "report": (
                    {
                        "valid": batch.status == "VALID",
                        "counts": report.counts,
                        "report_sha256": report.report_sha256,
                        "checked_rules": report.checked_rules,
                        "deferred_rules": report.deferred_rules,
                        "completed_at": report.completed_at.isoformat(),
                    }
                    if report
                    else None
                ),
            }

    def issues(
        self, scenario_id: UUID, batch_id: UUID, *, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        if after < 0 or not 1 <= limit <= 200:
            raise ValidationJobError("PAGINATION_INVALID", 422)
        with self.sessions() as session:
            batch = session.get(ImportBatchModel, batch_id)
            if batch is None or batch.scenario_id != scenario_id:
                raise ValidationJobError("BATCH_NOT_FOUND", 404)
            rows = list(
                session.scalars(
                    select(ValidationIssueModel)
                    .where(
                        ValidationIssueModel.batch_id == batch_id,
                        ValidationIssueModel.ordinal > after,
                    )
                    .order_by(ValidationIssueModel.ordinal)
                    .limit(limit + 1)
                )
            )
            return {
                "items": [
                    {
                        "ordinal": row.ordinal,
                        "code": row.code,
                        "severity": row.severity,
                        "dataset": row.dataset,
                        "source": (
                            f"workbook.xlsx:{row.sheet}"
                            if row.sheet
                            else f"{row.dataset}.csv"
                            if row.dataset
                            else None
                        ),
                        "row": row.row_number,
                        "field": row.field,
                        "message": row.message,
                        "value_excerpt": row.value_excerpt,
                    }
                    for row in rows[:limit]
                ],
                "next_after": rows[limit - 1].ordinal if len(rows) > limit else None,
            }

    def claim(self) -> tuple[UUID, UUID] | None:
        with self.sessions.begin() as session:
            now = datetime.now(UTC)
            batch = session.scalar(
                select(ImportBatchModel)
                .join(
                    ImportValidationJobModel,
                    ImportValidationJobModel.batch_id == ImportBatchModel.id,
                )
                .where(
                    ImportBatchModel.status == "VALIDATING",
                    ImportBatchModel.transitioned_at + self.retention > now,
                    (ImportValidationJobModel.lease_until.is_(None))
                    | (ImportValidationJobModel.lease_until <= now),
                )
                .order_by(ImportValidationJobModel.updated_at, ImportBatchModel.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if batch is None or self._expired(session, batch, now):
                return None
            job = session.get(ImportValidationJobModel, batch.id)
            if job is None:
                return None
            if job.attempts >= self.max_attempts:
                job.owner_token = None
                job.lease_until = None
                job.updated_at = now
                self._transition(session, batch, "FAILED", now, "MAX_ATTEMPTS")
                return None
            token = uuid4()
            job.owner_token = token
            job.lease_until = now + self.lease
            job.heartbeat_at = now
            job.updated_at = now
            job.attempts += 1
            return batch.id, token

    def heartbeat(self, batch_id: UUID, token: UUID) -> bool:
        with self.sessions.begin() as session:
            now = datetime.now(UTC)
            job = session.scalar(
                select(ImportValidationJobModel)
                .where(ImportValidationJobModel.batch_id == batch_id)
                .with_for_update()
            )
            if (
                job is None
                or job.owner_token != token
                or job.lease_until is None
                or job.lease_until <= now
            ):
                return False
            job.lease_until = now + self.lease
            job.heartbeat_at = now
            job.updated_at = now
            return True

    @staticmethod
    def _transition(
        session: Session, batch: ImportBatchModel, target: str, now: datetime, reason: str | None
    ) -> None:
        previous = batch.status
        batch.status = target
        batch.version += 1
        batch.transitioned_at = now
        session.add(
            ImportBatchEventModel(
                id=uuid4(),
                batch_id=batch.id,
                sequence=batch.version,
                from_status=previous,
                to_status=target,
                occurred_at=now,
                reason=reason,
            )
        )

    def _owned(
        self, session: Session, batch_id: UUID, token: UUID
    ) -> tuple[ImportBatchModel, ImportValidationJobModel] | None:
        # Keep lock ordering identical to maintenance: batch before job.
        batch = session.scalar(
            select(ImportBatchModel).where(ImportBatchModel.id == batch_id).with_for_update()
        )
        if batch is None or batch.status != "VALIDATING":
            return None
        job = session.scalar(
            select(ImportValidationJobModel)
            .where(ImportValidationJobModel.batch_id == batch_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            job is None
            or job.owner_token != token
            or job.lease_until is None
            or job.lease_until <= now
        ):
            return None
        return batch, job

    def _read(self, batch_id: UUID) -> tuple[dict[str, bytes], ValidationContext, str]:
        with self.sessions() as session:
            batch = session.get(ImportBatchModel, batch_id)
            context = session.get(ImportValidationContextModel, batch_id)
            if batch is None or context is None:
                raise RuntimeError("validation batch is missing")
            rows = list(
                session.scalars(select(ImportFileModel).where(ImportFileModel.batch_id == batch_id))
            )
            files: dict[str, bytes] = {}
            total = 0
            manifest = hashlib.sha256()
            for row in sorted(rows, key=lambda item: item.dataset):
                if row.size_bytes > self.limits.max_file_bytes:
                    raise RuntimeError("stored import exceeds file limit")
                with self.storage.open(row.storage_key) as stream:
                    content = stream.read(self.limits.max_file_bytes + 1)
                total += len(content)
                if (
                    len(content) != row.size_bytes
                    or len(content) > self.limits.max_file_bytes
                    or total > self.limits.max_package_bytes
                    or hashlib.sha256(content).hexdigest() != row.sha256
                ):
                    raise RuntimeError("stored import does not match its manifest")
                manifest.update(f"{row.dataset}\0{row.sha256}\0{row.size_bytes}\n".encode("ascii"))
                name = "workbook.xlsx" if row.dataset == "workbook" else f"{row.dataset}.csv"
                files[name] = content
            if manifest.hexdigest() != batch.package_sha256:
                raise RuntimeError("package manifest changed")
            return files, ValidationContext.from_dict(context.context_data), batch.package_sha256

    def finish(self, batch_id: UUID, token: UUID, report: ValidationReport) -> bool:
        with self.sessions.begin() as session:
            owned = self._owned(session, batch_id, token)
            if owned is None:
                return False
            batch, job = owned
            now = datetime.now(UTC)
            if self._expired(session, batch, now):
                job.owner_token = None
                job.lease_until = None
                job.updated_at = now
                return False
            context = session.get(ImportValidationContextModel, batch_id)
            assert context is not None
            summary = report.to_dict()
            encoded = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
            session.add(
                ImportValidationReportModel(
                    batch_id=batch_id,
                    package_sha256=batch.package_sha256,
                    context_sha256=context.context_sha256,
                    contract_version=context.context_data["contract_version"],
                    validator_version=context.context_data["validator_version"],
                    report_sha256=hashlib.sha256(encoded).hexdigest(),
                    counts={key: asdict(value) for key, value in report.counts.items()},
                    checked_rules=CHECKED_RULES,
                    deferred_rules=DEFERRED_RULES,
                    completed_at=now,
                )
            )
            files = {
                row.dataset: row.id
                for row in session.scalars(
                    select(ImportFileModel).where(ImportFileModel.batch_id == batch_id)
                )
            }
            for ordinal, issue in enumerate(report.issues, start=1):
                session.add(
                    ValidationIssueModel(
                        id=uuid4(),
                        batch_id=batch_id,
                        file_id=(
                            files.get("workbook")
                            if "workbook" in files
                            else files.get(issue.dataset or "")
                        ),
                        ordinal=ordinal,
                        severity=issue.severity,
                        code=issue.code,
                        dataset=issue.dataset,
                        sheet=issue.dataset if "workbook" in files else None,
                        row_number=issue.row,
                        field=issue.field,
                        message=issue.message,
                        value_excerpt=issue.value_excerpt,
                        created_at=now,
                    )
                )
            self._transition(session, batch, "VALID" if report.valid else "INVALID", now, None)
            job.owner_token = None
            job.lease_until = None
            job.updated_at = now
            return True

    def fail(self, batch_id: UUID, token: UUID) -> bool:
        with self.sessions.begin() as session:
            owned = self._owned(session, batch_id, token)
            if owned is None:
                return False
            batch, job = owned
            now = datetime.now(UTC)
            job.owner_token = None
            job.lease_until = None
            job.updated_at = now
            if not self._expired(session, batch, now) and job.attempts >= self.max_attempts:
                self._transition(session, batch, "FAILED", now, "VALIDATION_INFRASTRUCTURE")
            return True

    def replay_verified_for_publication(
        self,
        scenario_id: UUID,
        batch_id: UUID,
        *,
        row_sink: Callable[[str, str, int, dict[str, Any]], None] | None = None,
    ) -> tuple[dict[str, bytes], ValidationContext]:
        """2.3c handoff: bounded replay of the same bytes, rules and context.

        Publication must also lock the batch and recheck status/expiry inside its
        own transaction before inserting a revision.
        """
        with self.sessions() as session:
            batch = session.get(ImportBatchModel, batch_id)
            report = session.get(ImportValidationReportModel, batch_id)
            context_row = session.get(ImportValidationContextModel, batch_id)
            if (
                batch is None
                or batch.scenario_id != scenario_id
                or batch.status != "VALID"
                or self._expired(session, batch, datetime.now(UTC))
                or report is None
                or context_row is None
                or report.package_sha256 != batch.package_sha256
                or report.context_sha256 != context_row.context_sha256
                or report.contract_version != batch.parser_version
            ):
                raise ValidationJobError("VALIDATED_SOURCE_UNAVAILABLE", 409)
            expected_hash = report.report_sha256
            expected_context_hash = context_row.context_sha256
            expected_contract = report.contract_version
            expected_validator = report.validator_version
        try:
            files, context, _ = self._read(batch_id)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationJobError("VALIDATED_SOURCE_UNAVAILABLE", 409) from exc
        if (
            context.sha256 != expected_context_hash
            or context.contract_version != expected_contract
            or context.validator_version != expected_validator
        ):
            raise ValidationJobError("VALIDATED_SOURCE_MISMATCH", 409)
        result = validate_package(files, self.limits, context=context, row_sink=row_sink)
        encoded = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        if not result.valid or hashlib.sha256(encoded).hexdigest() != expected_hash:
            raise ValidationJobError("VALIDATED_SOURCE_MISMATCH", 409)
        return files, context

    def process_once(self) -> bool:
        claimed = self.claim()
        if claimed is None:
            return False
        batch_id, token = claimed
        stop = threading.Event()

        def pulse() -> None:
            while not stop.wait(max(0.1, self.lease.total_seconds() / 3)):
                try:
                    if not self.heartbeat(batch_id, token):
                        return
                except Exception:
                    logger.exception(
                        "validation_heartbeat_failed", extra={"batch_id": str(batch_id)}
                    )
                    return

        heartbeat = threading.Thread(target=pulse, daemon=True)
        heartbeat.start()
        try:
            files, context, _ = self._read(batch_id)
            report = validate_package(files, self.limits, context=context)
            self.finish(batch_id, token, report)
        except Exception:
            logger.exception("validation_attempt_failed", extra={"batch_id": str(batch_id)})
            self.fail(batch_id, token)
        finally:
            stop.set()
            heartbeat.join(timeout=2)
        return True
