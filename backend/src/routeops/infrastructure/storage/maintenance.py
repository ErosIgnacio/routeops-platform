"""Recoverable, idempotent cleanup of provisional and unreferenced originals."""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from routeops.application.ports.object_storage import ObjectStorageGateway
from routeops.infrastructure.config import Settings
from routeops.infrastructure.persistence.models import (
    ImportBatchExpirationModel,
    ImportBatchModel,
    ImportFileDeletionModel,
    ImportFileModel,
)
from routeops.infrastructure.persistence.session import (
    create_database_engine,
    create_session_factory,
)
from routeops.infrastructure.storage import LocalObjectStorage

logger = logging.getLogger("routeops.import_maintenance")


class ImportStorageMaintenance:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: ObjectStorageGateway,
        *,
        retention_days: int,
        orphan_grace_seconds: int,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._retention = timedelta(days=retention_days)
        self._grace = orphan_grace_seconds

    def run_once(self) -> tuple[int, int, int]:
        expired = 0
        while count := self._expire_due():
            expired += count
        deleted = 0
        while count := self._delete_expired_originals():
            deleted += count
        orphans = self._remove_orphans()
        return expired, deleted, orphans

    def _expire_due(self) -> int:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            batches = list(
                session.scalars(
                    select(ImportBatchModel)
                    .where(
                        ImportBatchModel.status != "PUBLISHED",
                        ImportBatchModel.transitioned_at <= now - self._retention,
                        ~exists().where(ImportBatchExpirationModel.batch_id == ImportBatchModel.id),
                    )
                    .order_by(ImportBatchModel.transitioned_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            for batch in batches:
                session.add(
                    ImportBatchExpirationModel(
                        batch_id=batch.id, expired_at=now, reason="RETENTION"
                    )
                )
            return len(batches)

    def _delete_expired_originals(self) -> int:
        deleted = 0
        with self._sessions() as session:
            file_ids = list(
                session.scalars(
                    select(ImportFileModel.id)
                    .join(
                        ImportBatchExpirationModel,
                        ImportBatchExpirationModel.batch_id == ImportFileModel.batch_id,
                    )
                    .where(~exists().where(ImportFileDeletionModel.file_id == ImportFileModel.id))
                    .limit(100)
                )
            )
        for file_id in file_ids:
            with self._sessions.begin() as session:
                file = session.get(ImportFileModel, file_id)
                if file is None:
                    continue
                expiration = session.scalar(
                    select(ImportBatchExpirationModel)
                    .where(ImportBatchExpirationModel.batch_id == file.batch_id)
                    .with_for_update(skip_locked=True)
                )
                if expiration is None or session.get(ImportFileDeletionModel, file_id) is not None:
                    continue
                self._storage.delete(file.storage_key)
                session.add(
                    ImportFileDeletionModel(
                        file_id=file.id,
                        batch_id=file.batch_id,
                        deleted_at=datetime.now(UTC),
                        reason="RETENTION",
                    )
                )
                deleted += 1
        return deleted

    def _remove_orphans(self) -> int:
        threshold = time.time() - self._grace
        removed = 0
        with self._sessions() as session:
            referenced = set(
                session.scalars(
                    select(ImportFileModel.storage_key).where(
                        ~exists().where(ImportFileDeletionModel.file_id == ImportFileModel.id)
                    )
                )
            )
        for key, modified_at in self._storage.temporary_keys():
            if modified_at < threshold:
                self._storage.delete_temporary(key)
                removed += 1
        for key, modified_at in self._storage.object_keys():
            if modified_at < threshold and key not in referenced:
                self._storage.delete(key)
                removed += 1
        return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="RouteOps private import storage maintenance")
    parser.add_argument("--loop", action="store_true", help="repeat at the configured interval")
    args = parser.parse_args()
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")
    interval = int(os.getenv("ROUTEOPS_IMPORT_MAINTENANCE_INTERVAL_SECONDS", "3600"))
    if interval <= 0:
        raise ValueError("maintenance interval must be positive")
    engine = create_database_engine(settings.database_url)
    maintenance = ImportStorageMaintenance(
        create_session_factory(engine),
        LocalObjectStorage(settings.import_storage_root),
        retention_days=settings.import_retention_days,
        orphan_grace_seconds=settings.import_orphan_grace_seconds,
    )
    try:
        while True:
            try:
                logger.info("import_maintenance_complete: %s", maintenance.run_once())
            except Exception:
                logger.exception("import_maintenance_failed")
                if not args.loop:
                    return 1
            if not args.loop:
                return 0
            time.sleep(interval)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
