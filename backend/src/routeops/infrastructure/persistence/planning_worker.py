"""PostgreSQL-leased imported-revision planning worker."""

from __future__ import annotations

import argparse
import logging
import time

from routeops.infrastructure.config import Settings
from routeops.infrastructure.persistence.operational_allocation import OperationalAllocationService
from routeops.infrastructure.persistence.revision_runs import RevisionRunService
from routeops.infrastructure.persistence.session import (
    create_database_engine,
    create_session_factory,
)
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.vroom import VroomAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Process imported-revision planning jobs")
    parser.add_argument("--loop", action="store_true")
    options = parser.parse_args()
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level)
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    osrm = OsrmClient(
        settings.osrm_url,
        settings.http_connect_timeout_seconds,
        settings.http_read_timeout_seconds,
    )
    solver = VroomAdapter(
        settings.vroom_url,
        settings.http_connect_timeout_seconds,
        settings.http_read_timeout_seconds,
    )
    service = RevisionRunService(
        sessions,
        OperationalAllocationService(sessions, osrm),
        osrm,
        solver,
        settings.planning_limits,
        timeout_seconds=settings.solver_timeout_seconds,
        lease_seconds=settings.planning_lease_seconds,
        max_attempts=settings.planning_max_attempts,
        max_snap_distance_m=settings.planning_max_snap_distance_m,
    )
    try:
        if options.loop:
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
