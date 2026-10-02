from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from routeops.application.import_validation import ImportLimits
from routeops.application.revision_problem import WorkloadLimits


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    log_level: str
    database_url: str
    vroom_url: str
    osrm_url: str
    solver_timeout_seconds: int
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float
    cors_origins: tuple[str, ...]
    demo_dataset_path: Path
    map_dataset_sha256: str
    import_storage_root: Path
    import_limits: ImportLimits
    import_retention_days: int
    import_orphan_grace_seconds: int
    import_validation_lease_seconds: int
    import_validation_max_attempts: int
    import_validation_poll_seconds: int
    planning_limits: WorkloadLimits
    planning_lease_seconds: int
    planning_max_attempts: int
    planning_poll_seconds: int
    planning_max_snap_distance_m: float

    @classmethod
    def from_environment(cls) -> Settings:
        origins = os.getenv("ROUTEOPS_CORS_ORIGINS", "http://localhost:5173")
        database_url = os.getenv("ROUTEOPS_DATABASE_URL")
        if not database_url:
            raise RuntimeError("ROUTEOPS_DATABASE_URL must be configured")
        retention_days = int(os.getenv("ROUTEOPS_IMPORT_RETENTION_DAYS", "30"))
        orphan_grace_seconds = int(os.getenv("ROUTEOPS_IMPORT_ORPHAN_GRACE_SECONDS", "3600"))
        lease_seconds = int(os.getenv("ROUTEOPS_IMPORT_VALIDATION_LEASE_SECONDS", "120"))
        max_attempts = int(os.getenv("ROUTEOPS_IMPORT_VALIDATION_MAX_ATTEMPTS", "3"))
        poll_seconds = int(os.getenv("ROUTEOPS_IMPORT_VALIDATION_POLL_SECONDS", "5"))
        planning_lease = int(os.getenv("ROUTEOPS_PLANNING_LEASE_SECONDS", "120"))
        planning_attempts = int(os.getenv("ROUTEOPS_PLANNING_MAX_ATTEMPTS", "3"))
        planning_poll = int(os.getenv("ROUTEOPS_PLANNING_POLL_SECONDS", "5"))
        max_snap_distance_m = float(
            os.getenv("ROUTEOPS_PLANNING_MAX_SNAP_DISTANCE_M", "250")
        )
        if (
            min(
                retention_days,
                orphan_grace_seconds,
                lease_seconds,
                max_attempts,
                poll_seconds,
                planning_lease,
                planning_attempts,
                planning_poll,
            )
            <= 0
        ):
            raise ValueError("import and planning worker intervals must be positive")
        if not 0 < max_snap_distance_m <= 5000:
            raise ValueError("ROUTEOPS_PLANNING_MAX_SNAP_DISTANCE_M must be in (0, 5000]")
        return cls(
            environment=os.getenv("ROUTEOPS_ENV", "development"),
            log_level=os.getenv("ROUTEOPS_LOG_LEVEL", "INFO"),
            database_url=database_url,
            vroom_url=os.getenv("ROUTEOPS_VROOM_URL", "http://localhost:3000"),
            osrm_url=os.getenv("ROUTEOPS_OSRM_URL", "http://localhost:5000"),
            solver_timeout_seconds=int(os.getenv("ROUTEOPS_SOLVER_TIMEOUT_SECONDS", "15")),
            http_connect_timeout_seconds=float(
                os.getenv("ROUTEOPS_HTTP_CONNECT_TIMEOUT_SECONDS", "2")
            ),
            http_read_timeout_seconds=float(os.getenv("ROUTEOPS_HTTP_READ_TIMEOUT_SECONDS", "20")),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()),
            demo_dataset_path=Path(
                os.getenv(
                    "ROUTEOPS_DEMO_DATASET",
                    str(Path(__file__).resolve().parents[3] / "data" / "synthetic" / "demo.json"),
                )
            ),
            map_dataset_sha256=os.getenv(
                "ROUTEOPS_MAP_DATASET_SHA256",
                "22230c1c093a0fe50bbb240cce3d2c36aaf34e8bfb2fe136b76405084de213c4",
            ),
            import_storage_root=Path(
                os.getenv("ROUTEOPS_IMPORT_STORAGE_ROOT", "/app/private-imports")
            ),
            import_limits=ImportLimits.from_environment(),
            import_retention_days=retention_days,
            import_orphan_grace_seconds=orphan_grace_seconds,
            import_validation_lease_seconds=lease_seconds,
            import_validation_max_attempts=max_attempts,
            import_validation_poll_seconds=poll_seconds,
            planning_limits=WorkloadLimits(
                max_orders=int(os.getenv("ROUTEOPS_PLANNING_MAX_ORDERS", "20")),
                max_lines=int(os.getenv("ROUTEOPS_PLANNING_MAX_LINES", "60")),
                max_vehicles=int(os.getenv("ROUTEOPS_PLANNING_MAX_VEHICLES", "6")),
                max_matrix_cells=int(os.getenv("ROUTEOPS_PLANNING_MAX_MATRIX_CELLS", "80")),
                max_solver_matrix_cells=int(
                    os.getenv("ROUTEOPS_PLANNING_MAX_SOLVER_MATRIX_CELLS", "1024")
                ),
                max_inventory_positions=int(
                    os.getenv("ROUTEOPS_PLANNING_MAX_INVENTORY_POSITIONS", "10000")
                ),
            ),
            planning_lease_seconds=planning_lease,
            planning_max_attempts=planning_attempts,
            planning_poll_seconds=planning_poll,
            planning_max_snap_distance_m=max_snap_distance_m,
        )
