from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from routeops.api.logging import configure_logging
from routeops.application.import_upload import UploadError, receive_package
from routeops.application.planning import DemoPlanningService
from routeops.domain.optimization import SolutionQuality
from routeops.domain.policies.allocation import DeterministicAllocationPolicy
from routeops.infrastructure.config import Settings
from routeops.infrastructure.data import SyntheticScenarioLoader
from routeops.infrastructure.persistence import (
    DatabaseRunRepository,
    create_database_engine,
    create_session_factory,
)
from routeops.infrastructure.persistence.import_upload_repository import UploadService
from routeops.infrastructure.persistence.planning_data_repository import ScenarioRepository
from routeops.infrastructure.routing.errors import RoutingDependencyError
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver import VroomAdapter
from routeops.infrastructure.solver.errors import (
    SolverDependencyError,
    SolverInputError,
    SolverResponseError,
)
from routeops.infrastructure.storage import LocalObjectStorage

settings = Settings.from_environment()
configure_logging(settings.log_level)
logger = logging.getLogger("routeops.api")
engine = create_database_engine(settings.database_url)
sessions = create_session_factory(engine)
repository = DatabaseRunRepository(sessions)
scenario_repository = ScenarioRepository(sessions)
object_storage = LocalObjectStorage(settings.import_storage_root)
upload_service = UploadService(
    sessions, object_storage, retention_days=settings.import_retention_days
)
osrm = OsrmClient(
    settings.osrm_url,
    settings.http_connect_timeout_seconds,
    settings.http_read_timeout_seconds,
)
vroom = VroomAdapter(
    settings.vroom_url,
    settings.http_connect_timeout_seconds,
    settings.http_read_timeout_seconds,
)
planning = DemoPlanningService(
    SyntheticScenarioLoader(settings.demo_dataset_path),
    DeterministicAllocationPolicy(osrm),
    vroom,
    repository,
    settings.solver_timeout_seconds,
    settings.map_dataset_sha256,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    engine.dispose()


app = FastAPI(
    title="RouteOps API",
    version="0.1.0",
    description="Auditable route-planning API over synthetic Santiago data.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key"],
)


class CreateDemoRunRequest(BaseModel):
    solution_quality: SolutionQuality = SolutionQuality.BALANCED


class CreateScenarioRequest(BaseModel):
    name: str


@app.exception_handler(UploadError)
def upload_error(_: Request, exc: UploadError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": "Import upload could not be accepted."},
    )


@app.middleware("http")
async def request_observability(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid4()))[:100]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return response


@app.exception_handler(RoutingDependencyError)
@app.exception_handler(SolverDependencyError)
def dependency_error(_: Request, exc: Exception) -> JSONResponse:
    logger.error("planning_dependency_failed", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "type": "https://routeops.local/problems/dependency-unavailable",
            "title": "Planning dependency unavailable",
            "status": 503,
            "detail": "The routing or optimization dependency could not complete the request.",
        },
    )


@app.exception_handler(SolverInputError)
@app.exception_handler(SolverResponseError)
def solver_contract_error(_: Request, exc: Exception) -> JSONResponse:
    logger.error("solver_contract_failed", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "type": "https://routeops.local/problems/solver-contract",
            "title": "Invalid solver exchange",
            "status": 502,
            "detail": "The optimization exchange did not satisfy the RouteOps contract.",
        },
    )


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "alive", "service": "routeops-api"}


class HealthClient(Protocol):
    def health(self) -> dict[str, str]: ...


def dependency_status() -> tuple[bool, dict[str, object]]:
    checks: dict[str, object] = {}
    ready = True
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = {"status": "ready", "engine": "postgresql-postgis"}
    except Exception as exc:
        ready = False
        checks["database"] = {"status": "unavailable", "error": type(exc).__name__}
    clients: tuple[tuple[str, HealthClient], ...] = (("osrm", osrm), ("vroom", vroom))
    for name, client in clients:
        try:
            checks[name] = client.health()
        except Exception as exc:
            ready = False
            checks[name] = {"status": "unavailable", "error": type(exc).__name__}
    return ready, checks


@app.get("/health/dependencies", tags=["health"])
def dependencies(response: Response) -> dict[str, object]:
    ready, checks = dependency_status()
    response.status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "degraded", "dependencies": checks}


@app.get("/health/ready", tags=["health"])
def ready(response: Response) -> dict[str, str]:
    is_ready, _ = dependency_status()
    response.status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if is_ready else "not_ready"}


@app.post("/api/v1/demo/runs", status_code=status.HTTP_201_CREATED, tags=["planning"])
def create_demo_run(request: CreateDemoRunRequest) -> dict[str, object]:
    return planning.run(request.solution_quality)


@app.post("/api/v1/scenarios", status_code=status.HTTP_201_CREATED, tags=["imports"])
def create_scenario(request: CreateScenarioRequest) -> dict[str, str]:
    try:
        scenario_id = scenario_repository.create(request.name)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Scenario name must contain 1 to 200 characters"
        ) from exc
    return {"id": str(scenario_id), "name": request.name.strip()}


@app.post("/api/v1/scenarios/{scenario_id}/imports", tags=["imports"])
async def upload_import(
    scenario_id: UUID, request: Request, response: Response
) -> dict[str, object]:
    client_key = upload_service.validate_client_key(request.headers.get("Idempotency-Key"))
    if not upload_service.scenario_exists(scenario_id):
        raise HTTPException(status_code=404, detail="Active scenario was not found")
    package = await receive_package(request, object_storage, settings.import_limits)
    batch_id, created = upload_service.create(scenario_id, client_key, package)
    response.status_code = 201 if created else 200
    result = upload_service.get(scenario_id, batch_id)
    if result is None:
        raise RuntimeError("created import batch cannot be read")
    return result


@app.get("/api/v1/scenarios/{scenario_id}/imports/{batch_id}", tags=["imports"])
def get_import(scenario_id: UUID, batch_id: UUID) -> dict[str, object]:
    result = upload_service.get(scenario_id, batch_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Import batch was not found")
    return result


@app.get("/api/v1/runs/latest", tags=["planning"])
def latest_run() -> dict[str, object]:
    result = repository.latest()
    if result is None:
        raise HTTPException(status_code=404, detail="No planning runs are available")
    return result


@app.get("/api/v1/runs/{run_id}", tags=["planning"])
def get_run(run_id: UUID) -> dict[str, object]:
    result = repository.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Planning run was not found")
    return result
