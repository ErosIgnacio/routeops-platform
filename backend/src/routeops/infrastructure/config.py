from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from routeops.application.import_validation import ImportLimits


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

    @classmethod
    def from_environment(cls) -> Settings:
        origins = os.getenv("ROUTEOPS_CORS_ORIGINS", "http://localhost:5173")
        database_url = os.getenv("ROUTEOPS_DATABASE_URL")
        if not database_url:
            raise RuntimeError("ROUTEOPS_DATABASE_URL must be configured")
        retention_days = int(os.getenv("ROUTEOPS_IMPORT_RETENTION_DAYS", "30"))
        orphan_grace_seconds = int(os.getenv("ROUTEOPS_IMPORT_ORPHAN_GRACE_SECONDS", "3600"))
        if retention_days <= 0 or orphan_grace_seconds <= 0:
            raise ValueError("import maintenance intervals must be positive")
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
        )
