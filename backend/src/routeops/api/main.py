from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from routeops.api.logging import configure_logging
from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_upload import UploadError, receive_package
from routeops.application.planning import DemoPlanningService
from routeops.application.revision_problem import RunInputError
from routeops.domain.optimization import SolutionQuality
from routeops.domain.policies.allocation import DeterministicAllocationPolicy
from routeops.infrastructure.config import Settings
from routeops.infrastructure.data import SyntheticScenarioLoader
from routeops.infrastructure.data.demo_catalog import DemoCatalogService
from routeops.infrastructure.persistence import (
    DatabaseRunRepository,
    create_database_engine,
    create_session_factory,
)
from routeops.infrastructure.persistence.import_publication import (
    ImportPublicationService,
    PublicationError,
)
from routeops.infrastructure.persistence.import_upload_repository import UploadService
from routeops.infrastructure.persistence.import_validation_jobs import (
    ValidationJobError,
    ValidationJobService,
)
from routeops.infrastructure.persistence.operational_allocation import OperationalAllocationService
from routeops.infrastructure.persistence.planning_data_repository import ScenarioRepository
from routeops.infrastructure.persistence.revision_runs import PlanningRunError, RevisionRunService
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
validation_service = ValidationJobService(
    sessions,
    object_storage,
    settings.import_limits,
    retention_days=settings.import_retention_days,
    lease_seconds=settings.import_validation_lease_seconds,
    max_attempts=settings.import_validation_max_attempts,
)
publication_service = ImportPublicationService(
    sessions,
    validation_service,
    settings.import_storage_root,
    retention_days=settings.import_retention_days,
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
revision_planning = RevisionRunService(
    sessions,
    OperationalAllocationService(sessions, osrm),
    osrm,
    vroom,
    settings.planning_limits,
    timeout_seconds=settings.solver_timeout_seconds,
    lease_seconds=settings.planning_lease_seconds,
    max_attempts=settings.planning_max_attempts,
    max_snap_distance_m=settings.planning_max_snap_distance_m,
)
demo_catalog = DemoCatalogService(
    scenario_repository, object_storage, upload_service, validation_service, publication_service
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


class CreateRevisionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_quality: SolutionQuality = SolutionQuality.BALANCED
    allocation_policy: str = "alternatives-v2"


class RequestValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planning_date: date
    horizon_start_at: datetime
    horizon_end_at: datetime
    timezone_iana: str
    currency: str
    operational_area: dict[str, Any] | None = None
    contract_version: str = "2.1"
    validator_version: str = "2.3b.1"


@app.exception_handler(UploadError)
def upload_error(_: Request, exc: UploadError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": "Import upload could not be accepted."},
    )


@app.exception_handler(ValidationJobError)
def validation_job_error(_: Request, exc: ValidationJobError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": "Import validation request could not be accepted."},
    )


@app.exception_handler(PublicationError)
def publication_error(_: Request, exc: PublicationError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": "Import publication could not be completed."},
    )


@app.exception_handler(PlanningRunError)
@app.exception_handler(RunInputError)
def revision_run_error(_: Request, exc: PlanningRunError | RunInputError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": "Planning request could not be completed."},
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


@app.get("/api/v1/scenarios", tags=["imports"])
def list_scenarios(offset: int = 0, limit: int = 50) -> dict[str, object]:
    try:
        return scenario_repository.list_page(offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="PAGINATION_INVALID") from exc


@app.get("/api/v1/import-templates/{name}", tags=["imports"])
def download_import_template(name: str) -> Response:
    if name == "workbook":
        return Response(
            content=xlsx_template(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="routeops-template.xlsx"'},
        )
    if name in DATASETS:
        return Response(
            content=csv_template(name),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    raise HTTPException(status_code=404, detail="IMPORT_TEMPLATE_NOT_FOUND")


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


@app.get("/api/v1/scenarios/{scenario_id}/imports", tags=["imports"])
def list_imports(scenario_id: UUID, offset: int = 0, limit: int = 50) -> dict[str, object]:
    return upload_service.list(scenario_id, offset=offset, limit=limit)


@app.get("/api/v1/scenarios/{scenario_id}/imports/lookup", tags=["imports"])
def lookup_import(scenario_id: UUID, key: str) -> dict[str, object]:
    client_key = upload_service.validate_client_key(key)
    result = upload_service.lookup(scenario_id, client_key)
    if result is None:
        raise HTTPException(status_code=404, detail="BATCH_NOT_FOUND")
    return result


@app.get("/api/v1/scenarios/{scenario_id}/imports/{batch_id}", tags=["imports"])
def get_import(scenario_id: UUID, batch_id: UUID) -> dict[str, object]:
    result = upload_service.get(scenario_id, batch_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Import batch was not found")
    return result


@app.post("/api/v1/scenarios/{scenario_id}/imports/{batch_id}/validation", tags=["imports"])
def request_import_validation(
    scenario_id: UUID, batch_id: UUID, request: RequestValidation, response: Response
) -> dict[str, Any]:
    try:
        context = ValidationContext(
            planning_date=request.planning_date,
            horizon_start_at=request.horizon_start_at,
            horizon_end_at=request.horizon_end_at,
            timezone_iana=request.timezone_iana,
            currency=request.currency,
            operational_area=request.operational_area,
            contract_version=request.contract_version,
            validator_version=request.validator_version,
        )
    except ValueError as exc:
        raise ValidationJobError(str(exc), 422) from exc
    result = validation_service.request(scenario_id, batch_id, context)
    response.status_code = 202 if result["status"] == "VALIDATING" else 200
    return result


@app.get("/api/v1/scenarios/{scenario_id}/imports/{batch_id}/validation", tags=["imports"])
def get_import_validation(scenario_id: UUID, batch_id: UUID) -> dict[str, Any]:
    return validation_service.get(scenario_id, batch_id)


@app.get("/api/v1/scenarios/{scenario_id}/imports/{batch_id}/validation/issues", tags=["imports"])
def get_import_validation_issues(
    scenario_id: UUID, batch_id: UUID, after: int = 0, limit: int = 100
) -> dict[str, Any]:
    return validation_service.issues(scenario_id, batch_id, after=after, limit=limit)


@app.post("/api/v1/scenarios/{scenario_id}/imports/{batch_id}/publish", tags=["imports"])
def publish_import(scenario_id: UUID, batch_id: UUID, response: Response) -> dict[str, Any]:
    try:
        result, created = publication_service.publish(scenario_id, batch_id)
    except (OSError, SQLAlchemyError) as exc:
        logger.exception("import_publication_failed", extra={"batch_id": str(batch_id)})
        raise PublicationError("PUBLICATION_INFRASTRUCTURE_FAILED", 503) from exc
    response.status_code = 201 if created else 200
    return result


@app.get("/api/v1/scenarios/{scenario_id}/imports/{batch_id}/publication", tags=["imports"])
def get_import_publication(scenario_id: UUID, batch_id: UUID) -> dict[str, Any]:
    return publication_service.get_by_batch(scenario_id, batch_id)


@app.get("/api/v1/scenarios/{scenario_id}/revisions", tags=["imports"])
def list_scenario_revisions(scenario_id: UUID, after: int = 0, limit: int = 100) -> dict[str, Any]:
    return publication_service.list_revisions(scenario_id, after=after, limit=limit)


@app.get("/api/v1/scenarios/{scenario_id}/revisions/{revision_no}", tags=["imports"])
def get_scenario_revision(scenario_id: UUID, revision_no: int) -> dict[str, Any]:
    return publication_service.get_revision(scenario_id, revision_no)


@app.post("/api/v1/scenarios/{scenario_id}/revisions/{revision_no}/runs", tags=["planning"])
def create_revision_run(
    scenario_id: UUID,
    revision_no: int,
    body: CreateRevisionRunRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    key = request.headers.get("Idempotency-Key")
    if key is None:
        raise PlanningRunError("RUN_IDEMPOTENCY_KEY_REQUIRED", 422)
    result, created = revision_planning.submit(
        scenario_id, revision_no, key, body.solution_quality, body.allocation_policy
    )
    response.status_code = 202 if created else 200
    return result


@app.get("/api/v1/scenarios/{scenario_id}/revisions/{revision_no}/runs/lookup", tags=["planning"])
def lookup_revision_run(scenario_id: UUID, revision_no: int, key: str) -> dict[str, Any]:
    return revision_planning.lookup(scenario_id, revision_no, key)


@app.get("/api/v1/scenarios/{scenario_id}/revision-runs", tags=["planning"])
def list_revision_runs(scenario_id: UUID, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    return revision_planning.list(scenario_id, offset=offset, limit=limit)


@app.get("/api/v1/revision-runs/{run_id}", tags=["planning"])
def get_revision_run(run_id: UUID) -> dict[str, Any]:
    return revision_planning.get(run_id)


@app.post("/api/v1/revision-runs/{run_id}/accept", tags=["planning"])
def accept_revision_run(run_id: UUID) -> dict[str, Any]:
    return revision_planning.accept(run_id)


@app.post("/api/v1/revision-runs/{run_id}/cancel", tags=["planning"])
def cancel_revision_run(run_id: UUID) -> dict[str, Any]:
    return revision_planning.cancel(run_id)


@app.get("/api/v1/allocation-demos", tags=["planning"])
def list_allocation_demos() -> list[dict[str, str]]:
    return demo_catalog.list()


@app.post("/api/v1/allocation-demos/{name}/prepare", tags=["planning"])
def prepare_allocation_demo(name: str) -> dict[str, Any]:
    try:
        return demo_catalog.prepare(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
