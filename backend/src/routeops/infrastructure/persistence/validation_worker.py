"""Recoverable PostgreSQL-backed validation worker."""

from __future__ import annotations

import argparse
import time

from routeops.infrastructure.config import Settings
from routeops.infrastructure.logging import configure_logging
from routeops.infrastructure.persistence.import_validation_jobs import ValidationJobService
from routeops.infrastructure.persistence.session import (
    create_database_engine,
    create_session_factory,
)
from routeops.infrastructure.storage import LocalObjectStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Process provisional import validations")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_environment()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings.database_url)
    worker = ValidationJobService(
        create_session_factory(engine),
        LocalObjectStorage(settings.import_storage_root),
        settings.import_limits,
        retention_days=settings.import_retention_days,
        lease_seconds=settings.import_validation_lease_seconds,
        max_attempts=settings.import_validation_max_attempts,
    )
    try:
        if args.loop:
            while True:
                if not worker.process_once():
                    time.sleep(settings.import_validation_poll_seconds)
        else:
            worker.process_once()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
