# RouteOps Platform

RouteOps is a portfolio-grade last-mile planning platform built around a real
optimization boundary: stock-aware distribution-center allocation, VROOM route
optimization, OSRM road costs, PostGIS persistence, and a React/MapLibre control
center. All business data in this repository is synthetic.

> Status: **Milestone 1 and deliveries 2.1–2.3c accepted. Delivery 2.3d is
> available locally for final review on `feat/m2-3-import-flow`.**
>
> Docker Desktop/WSL2
> validation passed on 2026-09-24, including MLD map processing, online
> migrations, real VROOM/OSRM optimization, persistence across a full stack
> restart, smoke tests, and integration tests.

## What the vertical slice does

1. Loads a deterministic synthetic Santiago scenario with two distribution
   centers, inventory, three vehicles, and five fictional orders.
2. Allocates each complete order to one stock-eligible center using a documented
   greedy policy: priority, window end, OSRM duration, inventory slack, center ID.
3. Sends only the allocated, solver-neutral problem through `SolverGateway` to
   the VROOM adapter. Center isolation, three-dimensional capacity, time windows,
   skills, costs, and closed routes are mapped explicitly.
4. Reconciles every returned job and vehicle, decodes route geometry, persists
   the run and PostGIS line strings, and exposes KPIs and explained exceptions.
5. Renders the latest run in a responsive React dashboard with a MapLibre map.

One fixture order, `ORD-003`, deliberately lacks full stock coverage, so a
successful demo should be `PARTIAL`: routable orders plus one proven allocation
exception. The current map colors are blue `#3338d6` for `VEH-CENTRO-01` and
red `#eb1010` for `VEH-ORIENTE-01`.

## Quick start

Prerequisites: Docker Engine with Compose v2 (Docker Desktop with Linux
containers on Windows) and PowerShell 7 or a POSIX shell for the map download.
The PostGIS 18 image used here currently targets `linux/amd64`.

```powershell
Copy-Item .env.example .env
# Edit .env: set POSTGRES_PASSWORD and ROUTEOPS_DATABASE_URL with the same
# locally chosen password (URL-encode reserved characters in the URL).
./infrastructure/osrm/download-osm.ps1
docker compose --profile tools run --rm osrm-prepare
docker compose up --build
```

Leave both password fields empty in `.env.example`; set them only in the local
`.env` copy. The database URL must use the same password as PostgreSQL. Compose
rejects a missing value before starting services. Do not commit `.env`.

The approved map input has already been downloaded in this workspace. It is
ignored by Git; its reviewed fingerprint is committed in
[`data/osrm/source-lock.json`](data/osrm/source-lock.json). A fresh clone must
run the download command and review any checksum change before preprocessing.

Once the stack is healthy:

- dashboard: <http://localhost:5173>
- import workspace: <http://127.0.0.1:5173/imports>
- OpenAPI: <http://localhost:8000/docs>
- liveness: <http://localhost:8000/health/live>
- readiness: <http://localhost:8000/health/ready>
- dependency detail: <http://localhost:8000/health/dependencies>

Run the cross-stack acceptance check in another terminal:

```powershell
./scripts/smoke.ps1
```

Stop services without deleting the PostgreSQL volume:

```powershell
docker compose down
```

## Milestone 2 imports

The 2.1 CLI provides a read-only validator and reproducible CSV/XLSX templates
for five datasets. With the backend dependencies installed and `PYTHONPATH=backend/src`, run
`python -m routeops.application.import_cli templates <directory>` or
`python -m routeops.application.import_cli validate <five CSV paths or one XLSX path>`.
See [the 2.1 contract and issue catalog](docs/milestone-2-1-import-validation.md).

Delivery 2.3a adds local-only provisional uploads, private original storage,
and recoverable retention cleanup. See [the 2.3a API and configuration](docs/milestone-2-3a-private-upload.md).
The import workspace, its recovery flow and performance measurements are in
[the 2.3d guide](docs/milestone-2-3d-import-ui.md).

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/demo/runs` | Allocate, optimize, persist, and return a demo run |
| `GET` | `/api/v1/runs/latest` | Return the newest persisted run |
| `GET` | `/api/v1/runs/{run_id}` | Return one persisted run |
| `GET` | `/health/live` | Process liveness only |
| `GET` | `/health/ready` | Aggregate database/VROOM/OSRM readiness |
| `GET` | `/health/dependencies` | Per-dependency diagnostic status |

Example request:

```bash
curl -X POST http://localhost:8000/api/v1/demo/runs \
  -H 'Content-Type: application/json' \
  -d '{"solution_quality":"BALANCED"}'
```

## Tests and quality checks

With a local Python 3.14 environment:

```bash
cd backend
python -m pip install -r requirements-dev.lock.txt
ruff check src tests
mypy src
pytest --cov
```

With Node 24:

```bash
cd frontend
npm install
npm test
npm run build
```

Direct dependency versions are exact; resolved Python lock files and
`package-lock.json` fix both dependency graphs. Containers use the resolved
Python lock and `npm ci`, so drift is visible instead of silently accepted.

## Architecture

```text
frontend (React + MUI + MapLibre)
        |
FastAPI HTTP API
        |
application services ---- RunRepository ---- PostgreSQL/PostGIS
        |
domain policy + solver-neutral contracts
        |
SolverGateway / VroomAdapter ---- VROOM ---- OSRM
             allocation travel times ---------^
```

The backend follows a modular-monolith/hexagonal boundary. Domain and
application modules never import VROOM JSON or SQLAlchemy models. Full design:

- [Product requirements](docs/product-requirements.md)
- [Architecture](docs/architecture.md)
- [Data model and inventory consistency](docs/data-model.md)
- [CSV/XLSX data contracts](docs/data-contracts.md)
- [Optimization contracts](docs/optimization-contract.md)
- [VROOM/OSRM assessment](docs/research/toolchain-versions.md)
- [Accepted v0.2.1 routing and UI correction](docs/v0.2.1-routing-ui.md)
- [Milestone 1 runtime validation](docs/research/milestone-1-validation.md)
- [ADR-0001: VROOM and OSRM](docs/adr/0001-vroom-osrm.md)
- [ADR-0002: approved product decisions](docs/adr/0002-approved-product-decisions.md)
- [Roadmap](docs/roadmap.md)

## Data, licensing, and responsible use

- Code is licensed under [Apache-2.0](LICENSE).
- VROOM/vroom-express/OSRM and MapLibre notices are in
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Routing data and dashboard tiles require `© OpenStreetMap contributors` and
  are subject to the ODbL/tile usage terms described in that notice.
- The public OSM raster tile endpoint is suitable only for low-volume local
  demonstration, not production traffic.
- No real customer addresses, orders, company inventory, or confidential data
  belong in this repository.

## Approved v1 decisions

- Apache-2.0 repository license.
- Small bounded Santiago OSM extract with source, bbox, timestamp, and SHA-256.
- Closed routes returning to their originating distribution center.
- Manual baseline imports vehicle/order sequence and optional planned times;
  RouteOps recomputes route facts with the same pinned OSRM dataset.
