"""Persist a received package and its initial event as one SQL transaction."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.import_upload import ReceivedPackage, UploadError
from routeops.application.ports.object_storage import ObjectStorageGateway
from routeops.infrastructure.persistence.models import (
    ImportBatchEventModel,
    ImportBatchExpirationModel,
    ImportBatchModel,
    ImportFileModel,
    ScenarioModel,
)

_CLIENT_KEY = re.compile(r"[A-Za-z0-9._-]{1,200}\Z")
logger = logging.getLogger("routeops.import_upload")


class UploadService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: ObjectStorageGateway,
        *,
        retention_days: int = 30,
    ) -> None:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self._sessions = sessions
        self._storage = storage
        self._retention = timedelta(days=retention_days)

    def _is_expired(self, batch: ImportBatchModel, recorded: bool) -> bool:
        return recorded or (
            batch.status != "PUBLISHED"
            and batch.transitioned_at + self._retention <= datetime.now(UTC)
        )

    @staticmethod
    def validate_client_key(value: str | None) -> str:
        if value is None or not _CLIENT_KEY.fullmatch(value):
            raise UploadError("IDEMPOTENCY_KEY_INVALID", 400)
        return value

    def scenario_exists(self, scenario_id: UUID) -> bool:
        with self._sessions() as session:
            scenario = session.get(ScenarioModel, scenario_id)
            return scenario is not None and scenario.status == "ACTIVE"

    def _existing(self, scenario_id: UUID, client_key: str) -> ImportBatchModel | None:
        with self._sessions() as session:
            return session.scalar(
                select(ImportBatchModel).where(
                    ImportBatchModel.scenario_id == scenario_id,
                    ImportBatchModel.client_key == client_key,
                )
            )

    def create(
        self, scenario_id: UUID, client_key: str, package: ReceivedPackage
    ) -> tuple[UUID, bool]:
        committed = False
        try:
            existing = self._existing(scenario_id, client_key)
            if existing is not None:
                return self._resolve_existing(existing, package)
            batch_id = uuid4()
            now = datetime.now(UTC)
            try:
                with self._sessions.begin() as session:
                    session.add(
                        ImportBatchModel(
                            id=batch_id,
                            scenario_id=scenario_id,
                            client_key=client_key,
                            package_sha256=package.sha256,
                            parser_version="2.1",
                            status="RECEIVED",
                            version=1,
                            created_at=now,
                            transitioned_at=now,
                        )
                    )
                    session.flush()
                    session.add(
                        ImportBatchEventModel(
                            id=uuid4(),
                            batch_id=batch_id,
                            sequence=1,
                            from_status=None,
                            to_status="RECEIVED",
                            occurred_at=now,
                        )
                    )
                    for item in package.files:
                        session.add(
                            ImportFileModel(
                                id=uuid4(),
                                batch_id=batch_id,
                                dataset=item.dataset,
                                display_name=item.display_name,
                                storage_key=item.object.key,
                                sha256=item.object.sha256,
                                size_bytes=item.object.size_bytes,
                                created_at=now,
                            )
                        )
                committed = True
                return batch_id, True
            except IntegrityError:
                existing = self._existing(scenario_id, client_key)
                if existing is None:
                    raise
                return self._resolve_existing(existing, package)
        finally:
            if not committed:
                for item in package.files:
                    try:
                        self._storage.delete(item.object.key)
                    except OSError:
                        # The recoverable maintenance task removes any remaining orphan.
                        logger.warning("import_object_cleanup_deferred", exc_info=True)

    def _resolve_existing(
        self, existing: ImportBatchModel, package: ReceivedPackage
    ) -> tuple[UUID, bool]:
        with self._sessions() as session:
            recorded = session.get(ImportBatchExpirationModel, existing.id) is not None
            if self._is_expired(existing, recorded):
                raise UploadError("BATCH_EXPIRED", 409)
        if existing.package_sha256 != package.sha256:
            raise UploadError("IDEMPOTENCY_CONFLICT", 409)
        return existing.id, False

    def get(self, scenario_id: UUID, batch_id: UUID) -> dict[str, object] | None:
        with self._sessions() as session:
            batch = session.get(ImportBatchModel, batch_id)
            if batch is None or batch.scenario_id != scenario_id:
                return None
            expiration = session.get(ImportBatchExpirationModel, batch_id)
            expired = self._is_expired(batch, expiration is not None)
            files = session.scalars(
                select(ImportFileModel)
                .where(ImportFileModel.batch_id == batch_id)
                .order_by(ImportFileModel.dataset)
            )
            return {
                "id": str(batch.id),
                "scenario_id": str(batch.scenario_id),
                "status": "EXPIRED" if expired else batch.status,
                "package_sha256": batch.package_sha256,
                "created_at": batch.created_at.isoformat(),
                "expired_at": expiration.expired_at.isoformat() if expiration else None,
                "expires_at": (
                    (batch.transitioned_at + self._retention).isoformat()
                    if batch.status != "PUBLISHED"
                    else None
                ),
                "files": [
                    {
                        "dataset": item.dataset,
                        "display_name": item.display_name,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                    }
                    for item in files
                ],
            }

    def lookup(self, scenario_id: UUID, client_key: str) -> dict[str, object] | None:
        with self._sessions() as session:
            batch_id = session.scalar(
                select(ImportBatchModel.id).where(
                    ImportBatchModel.scenario_id == scenario_id,
                    ImportBatchModel.client_key == client_key,
                )
            )
        return self.get(scenario_id, batch_id) if batch_id is not None else None

    def list(self, scenario_id: UUID, *, offset: int = 0, limit: int = 50) -> dict[str, object]:
        if offset < 0 or not 1 <= limit <= 100:
            raise UploadError("PAGINATION_INVALID", 422)
        with self._sessions() as session:
            if session.get(ScenarioModel, scenario_id) is None:
                raise UploadError("SCENARIO_NOT_FOUND", 404)
            rows = list(
                session.scalars(
                    select(ImportBatchModel.id)
                    .where(ImportBatchModel.scenario_id == scenario_id)
                    .order_by(ImportBatchModel.created_at.desc(), ImportBatchModel.id.desc())
                    .offset(offset)
                    .limit(limit + 1)
                )
            )
        return {
            "items": [self.get(scenario_id, batch_id) for batch_id in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }
