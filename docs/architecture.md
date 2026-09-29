# Architecture and repository structure

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

Dependencies point inward: `api` and `infrastructure` depend on `application`
and `domain`; the domain imports neither FastAPI, SQLAlchemy, Pydantic transport
schemas, nor VROOM JSON shapes.

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

- Synchronous HTTP is sufficient for the portfolio MVP. Optimization runs have
  an explicit server-side timeout and a database state transition. If measured
  workloads exceed safe request duration, a durable job runner becomes a later
  ADR; Redis/Celery are not pre-installed.
- VROOM is the only solver. The application depends on `SolverGateway.solve()`;
  only `VroomAdapter` knows VROOM integer IDs, array capacities, encoded
  polylines, or error codes.
- The backend calls OSRM for center ranking, prechecks, and baseline evaluation.
  VROOM also calls the same OSRM service for its matrices and geometry. Each run
  stores the OSRM image version and map-dataset checksum.
- PostgreSQL constraints enforce local consistency; transactions and row locks
  protect shared inventory. Application checks improve error messages but do
  not replace database guarantees.

## Proposed repository layout

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

## Principal API resources

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

## Reliability and security boundaries

- File names are display metadata only. Server-generated IDs determine storage
  paths; path separators and traversal never reach filesystem operations.
- MIME type, extension, file signature, compressed size, uncompressed size,
  sheet count, row count, and column count are bounded.
- Parsing never evaluates formulas, macros, or uploaded code.
- Outbound VROOM/OSRM calls use connection/read timeouts. Only transport-level
  and selected 5xx failures are retried with bounded exponential backoff; input
  and routing-domain errors are not retried.
- Raw integration payloads are stored for audit behind a size limit and never
  leak into domain objects or normal logs.
- Health endpoints distinguish process liveness from dependency readiness.
