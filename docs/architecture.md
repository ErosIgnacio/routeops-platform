# Architecture and repository structure

## Current implementation versus initial design

This document retains the initial logical boundaries and illustrative layout.
Milestone 2 and v0.2.1 implement private uploads (`ObjectStorageGateway`),
PostgreSQL validation/planning jobs with leases, immutable revisions, operating
stock/reservations and `/imports` and `/planning`. Baselines, comparison, the
full indicator catalog and exports remain Milestone 3 work, not current APIs.
B2B/B2C use the same modules and explicit fields; no business-label dispatch.

## Architectural style

RouteOps is a **modular monolith** with a React single-page application and
three external infrastructure processes: PostgreSQL/PostGIS, VROOM Express, and
OSRM. The backend uses ports and adapters around a small domain model. This
keeps business rules testable without distributing them across microservices.

```text
React/MapLibre
      |
      v
FastAPI (transport adapters)
      |
      v
Application use cases ---- transaction boundary / idempotency
      |
      v
Domain ------------------- entities, policies, solver-neutral contracts
      |
      +--> repositories --------> SQLAlchemy/PostgreSQL/PostGIS
      +--> SolverGateway -------> VroomAdapter --> vroom-express
      +--> RoutingGateway ------> OsrmAdapter  --> OSRM
      +--> TabularFilePort -----> CSV/XLSX adapter
      +--> ExportPort ----------> CSV/XLSX writer

vroom-express -------------------------------> OSRM
```

The intended boundaries point inward. Domain imports neither FastAPI,
SQLAlchemy, Pydantic transport schemas nor VROOM JSON. The existing application
revision-preparation module reads SQLAlchemy models directly; this is an
implementation dependency, not a claim of strict isolation in every application
module. Routing/solver exceptions now belong to application ports, with old
infrastructure imports reexported for compatibility. 3.1a does not relocate that
persistence mapper; its new cost formula and v1 input invariants remain neutral.

## Responsibilities

### Domain

- Scenario, order, vehicle, stock, reservation, allocation, and planning-run
  invariants.
- Deterministic allocation policy and diagnostic rules.
- Solver-neutral `OptimizationProblem` / `OptimizationResult` value objects.
- Units, money, time intervals, coordinates, and reason codes.

### Application

- Use cases: import, validate, create snapshot and reserve, optimize, accept or
  cancel a plan, compare plans, calculate KPIs, and export.
- Ports for persistence, unit of work, solver, routing, clock, file parsing,
  export, and ID generation.
- Transaction, idempotency, timeout, and retry orchestration.
- 3.1b central plan/processing metric calculations derive from persisted facts;
  query adapters supply immutable capacities/windows/rates and append-only
  fenced timing boundaries. No metric formulas are implemented in controllers
  or the frontend. See [3.1b scope and evidence](milestone-3-1b-metrics.md).
- 3.1c central diagnostics explain reconciled results using recorded stock,
  fleet, normalized inputs and source hashes. The persistence adapter stores the
  immutable document in the same fenced transaction as results, compensation
  and terminal processing transition. Queries validate its hash and reproduce
  recorded reasons; they do not consult current inventory. Input incidences and
  operational failures have separate scopes. No external call is added while
  holding stock locks. See [3.1c catalog and tests](milestone-3-1c-diagnostics.md).

### Infrastructure

- SQLAlchemy repositories, PostGIS mappings, Alembic migrations, and database
  unit of work.
- VROOM request/response mapping isolated in `VroomAdapter`.
- OSRM matrix/route clients and health probes.
- CSV/XLSX parsing and writing.
- Structured logging and configuration.

### API

- FastAPI routes, request/response DTOs, validation at HTTP boundaries, problem
  details, upload limits, and dependency wiring.
- No allocation or optimization rules in controllers.

## Runtime decisions

- Original demo optimization retains synchronous HTTP. Imported revision runs
  and validation use durable PostgreSQL jobs with owner tokens, heartbeat,
  bounded retries and fenced completion; no Redis/Celery. READY reservations
  do not expire automatically. External routing calls do not hold stock locks.
- VROOM is the only solver. The application depends on `SolverGateway.solve()`;
  only `VroomAdapter` knows VROOM integer IDs, array capacities, encoded
  polylines, or error codes.
- The backend calls OSRM for center ranking, prechecks, and baseline evaluation.
  VROOM also calls the same OSRM service for its matrices and geometry. Each run
  stores the OSRM image version and map-dataset checksum.
- PostgreSQL constraints enforce local consistency; transactions and row locks
  protect shared inventory. Application checks improve error messages but do
  not replace database guarantees.

## Initial illustrative repository layout

```text
routeops-platform/
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── migrations/
│   ├── src/routeops/
│   │   ├── domain/
│   │   │   ├── models/
│   │   │   ├── policies/
│   │   │   ├── optimization/
│   │   │   ├── value_objects/
│   │   │   └── errors.py
│   │   ├── application/
│   │   │   ├── commands/
│   │   │   ├── queries/
│   │   │   ├── ports/
│   │   │   └── services/
│   │   ├── infrastructure/
│   │   │   ├── persistence/
│   │   │   ├── routing/
│   │   │   ├── tabular/
│   │   │   ├── telemetry/
│   │   │   └── config.py
│   │   └── api/
│   │       ├── routes/
│   │       ├── schemas/
│   │       ├── dependencies.py
│   │       └── main.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── contract/
│       └── smoke/
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── app/
│   │   ├── features/
│   │   │   ├── scenarios/
│   │   │   ├── imports/
│   │   │   ├── planning/
│   │   │   └── comparison/
│   │   ├── components/
│   │   ├── lib/
│   │   └── test/
│   └── e2e/
├── infrastructure/
│   ├── vroom/config.yml
│   ├── osrm/README.md
│   └── scripts/
├── data/
│   ├── templates/
│   └── synthetic/
├── docs/
│   ├── adr/
│   └── research/
├── tests/e2e/
├── docker-compose.yml
├── docker-compose.test.yml
├── .env.example
├── .editorconfig
├── .gitignore
├── README.md
└── LICENSE
```

Backend tests live with the backend because they import the Python package;
root `tests/e2e` holds cross-stack tests only. Runtime-generated OSRM artifacts,
uploads, and exports are ignored and mounted into named volumes or a controlled
application data directory.

The actual implementation uses application modules directly under `application`,
`infrastructure/persistence/revision_runs.py` for the durable coordinator and
`api/main.py` for transport. The tree above is the initial conceptual layout,
not a list of files that still need to be created. In 3.1a a read-only
`OperatingCostQuery` derives versioned costs without altering historical JSON.

## Principal API resources (initial resource boundaries)

- `POST /api/v1/scenarios`
- `POST /api/v1/scenarios/{id}/imports`
- `GET /api/v1/scenarios/{id}/validation-issues`
- `POST /api/v1/scenarios/{id}/inventory-reservations`
- `POST /api/v1/scenarios/{id}/runs` with an idempotency key
- `GET /api/v1/runs/{id}` and `/routes`, `/unassigned`, `/kpis`
- `POST /api/v1/scenarios/{id}/baselines`
- `GET /api/v1/runs/{id}/comparison`
- `GET /api/v1/runs/{id}/exports/{format}`
- `GET /health/live`, `/health/ready`, and `/health/dependencies`

The exact OpenAPI schemas are implemented incrementally with each vertical
slice; this list defines resource boundaries, not a frozen HTTP contract.

## Delivery 3.2 analytical comparison boundary

`PlanComparisonService` captures a published revision, normalized input, rates,
versions, hashes and current availability under the scenario lock. It reads
RouteOps holds/confirmations without activating inventory or creating reservations.
Each alternative receives the same immutable initial availability. Read sessions
close before external calls; no inventory lock spans OSRM/VROOM work.

The manual evaluator uses `FixedRouteGateway` and OSRM's ordered `route` service,
not `trip`, to simulate the supplied sequence without repair. Optimization uses
only `SolverGateway`. Both reuse the central business cost/KPI catalog; policies
and optimized-response reconciliation remain shared with operational planning.

The separate `comparison-worker` claims PostgreSQL jobs with leases, heartbeat
and owner fencing. Context/input/events/results are immutable, and final result
plus READY event commit atomically. Operational run/reservation transitions are
not called. The API, migration and limits are in the
[3.2 report](milestone-3-2-plan-comparison.md). The interface remains 3.3 scope.

## Reliability and security boundaries

- File names are display metadata only. Server-generated IDs determine storage
  paths; path separators and traversal never reach filesystem operations.
- MIME type, extension, file signature, compressed size, uncompressed size,
  sheet count, row count, and column count are bounded.
- Parsing never evaluates formulas, macros, or uploaded code.
- Outbound VROOM/OSRM calls use connection/read timeouts. Only transport-level
  and selected 5xx failures are retried with bounded exponential backoff; input
  and routing-domain errors are not retried.
- Normalized results, immutable decision evidence, versions and hashes are
  stored for audit. Raw vendor payload retention remains an initial proposal,
  not implemented storage; provider JSON does not leak into domain DTOs.
- Health endpoints distinguish process liveness from dependency readiness.
