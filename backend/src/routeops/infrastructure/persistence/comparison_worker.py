"""Recoverable PostgreSQL worker for analytic comparisons, with no reservations."""

import argparse
import time

from routeops.infrastructure.config import Settings
from routeops.infrastructure.logging import configure_logging
from routeops.infrastructure.persistence.plan_comparisons import PlanComparisonService
from routeops.infrastructure.persistence.session import (
    create_database_engine,
    create_session_factory,
)
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.vroom import VroomAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate immutable analytic comparisons")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_environment()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings.database_url)
    osrm = OsrmClient(
        settings.osrm_url, settings.http_connect_timeout_seconds, settings.http_read_timeout_seconds
    )
    solver = VroomAdapter(
        settings.vroom_url,
        settings.http_connect_timeout_seconds,
        settings.http_read_timeout_seconds,
    )
    service = PlanComparisonService(
        create_session_factory(engine),
        osrm,
        solver,
        settings.planning_limits,
        map_dataset_sha256=settings.map_dataset_sha256,
        timeout_seconds=settings.solver_timeout_seconds,
        lease_seconds=settings.planning_lease_seconds,
        max_attempts=settings.planning_max_attempts,
        max_snap_distance_m=settings.planning_max_snap_distance_m,
        solver_version=solver.engine_version,
        adapter_version=solver.adapter_version,
    )
    try:
        if args.loop:
            while True:
                if not service.process_once():
                    time.sleep(settings.planning_poll_seconds)
        else:
            service.process_once()
    except KeyboardInterrupt:
        return 0
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
