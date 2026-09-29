# Milestone 1 runtime validation

Validation date: 2026-09-24
Environment: Windows with Docker Desktop and the WSL2 Linux backend

This report records the executed acceptance evidence for the RouteOps Milestone
1 vertical slice. It does not cover or start Milestone 2.

## Runtime and pinned services

| Check | Observed result |
|---|---|
| Docker context | `desktop-linux` |
| Docker client/server | `29.8.0` / `29.8.0` |
| Docker Compose | `v5.5.1` |
| Server platform | `linux/amd64` |
| Kernel | `6.18.33.2-microsoft-standard-WSL2` |
| PostgreSQL/PostGIS | PostgreSQL 18.6; PostGIS 3.6.4 |
| OSRM | 26.9.0, MLD |
| VROOM | 1.15.0 |

`docker compose config --quiet` completed with exit code 0. All five runtime
services were up after validation; PostgreSQL and VROOM reported healthy.

## OSM/OSRM preparation

The approved input `data/osrm/santiago-demo.osm` was 4,145,215 bytes and its
SHA-256 matched `data/osrm/source-lock.json`:

```text
22230c1c093a0fe50bbb240cce3d2c36aaf34e8bfb2fe136b76405084de213c4
```

The following command completed with exit code 0:

```powershell
docker compose --profile tools run --rm osrm-prepare
```

OSRM processed 25,596 raw nodes and 6,237 ways. MLD partition and customization
completed, producing the required `.partition`, `.cells`, `.cell_metrics`, and
`.mldgr` artifacts together with the geometry and index files.

## Database and migrations

The backend applied the migration online during startup. The final migration
state was:

```text
20260924_0001 (head)
```

PostgreSQL contained `planning_runs`, `optimized_routes`, and
`unassigned_orders`. The `optimized_routes.geometry` column was a PostGIS
`LineString,4326` with a GiST index.

## Health and real dependency path

The API returned:

```text
/health/live          alive
/health/ready         ready
/health/dependencies  database=ready, osrm=ready, vroom=ready
```

VROOM readiness includes a minimal optimization request. Runtime logs showed
the API posting that probe to VROOM and the VROOM container issuing both table
and route requests to OSRM with HTTP 200. This checks the VROOM-to-OSRM path,
not only the two processes independently.

## Smoke and run evidence

The final smoke command passed:

```powershell
./scripts/smoke.ps1
```

```text
RouteOps smoke passed: run=35796cab-dc61-4814-9b9d-b51228c8f25c,
routes=2, assigned=4, unassigned=1
```

A reconciled acceptance run (`ca329058-dd32-4c7e-b463-d4fe038dbe43`) produced:

| Fact | Value |
|---|---:|
| Status | `PARTIAL` |
| Routes / vehicles used | 2 / 2 |
| Assigned / unassigned orders | 4 / 1 |
| Distance | 8.218 km |
| Driving / service / waiting | 0.249 h / 0.750 h / 0.000 h |
| Total route duration | 0.999 h |
| Solver time | 60 ms |
| Estimated cost | CLP 47,660.44 |
| Dashboard cost display | `CLP $47.660` |

The expected infeasible fixture was `ORD-003`, rejected during allocation with
the proven reason `STOCK_NO_FULL_COVERAGE`.

PostGIS reconciliation showed exact agreement between normalized route columns,
stored JSON payloads, and geometries:

| Vehicle | Sequence | Distance | Duration | JSON/PostGIS points |
|---|---|---:|---:|---:|
| `VEH-CENTRO-01` | START → ORD-001 → ORD-004 → END | 3,753 m | 1,615 s | 137 / 137 |
| `VEH-ORIENTE-01` | START → ORD-002 → ORD-005 → END | 4,465 m | 1,982 s | 231 / 231 |

The dashboard map and stop-sequence component consume those same persisted
`routes[].geometry` and `routes[].steps` values. Automated tests assert GeoJSON
longitude/latitude conversion and rendered step order.

After visual review found insufficient route contrast, the map overlay was
strengthened without changing OSRM geometry or numerical results. Vehicle
colors are now keyed by stable source vehicle ID, route sources update through
`setData`, and the viewport fits all current route and stop coordinates. The
final visual acceptance was recorded on 2026-09-28 after the user confirmed
that the routes were visible on the map. The user subsequently confirmed formal
acceptance of Milestone 1.

A subsequent reviewer screenshot showed that no RouteOps overlay had been
installed even though the raster map was visible. Runtime logs then exposed
the direct cause: Vite's dependency optimizer requested a missing
`maplibre-gl-worker.mjs` file, preventing MapLibre's worker from processing the
GeoJSON. `maplibre-gl` is now excluded from dependency pre-bundling. The GeoJSON
sources and all four overlay layers are also part of the initial MapLibre
style, and run data is synchronized on the specific `style.load` lifecycle
event. A component regression test covers delayed style readiness, source
updates, and viewport fitting.

## Follow-up validation on 2026-09-28

The copied project was started on a second Windows machine with Docker Desktop.
The Santiago OSM input matched the recorded SHA-256 lock. Compose started all
five services; PostgreSQL and VROOM became healthy, the backend applied
`20260924_0001` online, and the dependency probe reached OSRM and VROOM.

The live PowerShell smoke test passed with run
`42de4d3f-3b7f-4398-8c7c-fe3afe10ad57`: two routes, four assigned orders,
and one expected unassigned order. The persisted API response contained 137
and 231 geometry points and four steps per route. The frontend served
`/?debugMap=1` with HTTP 200. Frontend logs did not show the prior missing
MapLibre worker error.

The style validation test was corrected to call the public `validateStyleMin`
API. MapLibre accepted the complete style, all 12 frontend tests passed, and
the TypeScript/Vite production build passed with 917 transformed modules. The
existing non-blocking bundle-size warning remains. The user then confirmed
that both routes were visible in the running dashboard. No screenshot was
retained in this report.

## Final closure on 2026-09-28

The current route colors are `#3338d6` for `VEH-CENTRO-01` and `#eb1010` for
`VEH-ORIENTE-01`. After removing demonstration database passwords from tracked
configuration, the stack started using the ignored local `.env` file. The
final smoke run `a6664f8a-c206-458a-9ef1-7750b1fe05f4` returned `PARTIAL`,
with two routes, four assigned orders, and one expected exception: `ORD-003`
with `STOCK_NO_FULL_COVERAGE`. The API returned `ready`, and the frontend
returned HTTP 200.

Final checks passed: Ruff, strict mypy on 31 source files, all eight backend
tests including the real Compose integration, all 12 frontend tests in seven
files, TypeScript, Vite production build, and `docker compose config --quiet`.
The build still reports a non-blocking chunk-size warning; code splitting is
deferred to later performance work. No route screenshot was retained, so the
visual evidence is the user's explicit confirmation in the project conversation.

## Automated verification

Backend checks ran in a Linux/Python 3.14.7 container connected to the live
Compose network:

```text
ruff:  all checks passed
mypy:  no issues in 31 source files
pytest: 8 passed, including the real Compose integration test
```

The integration test performs a real planning run, checks every first-slice KPI
against the optimization summary, fetches the persisted run again, and requires
the persisted response to equal the original result.

Frontend verification ran in the Node 24 container:

```text
Vitest: 6 files, 9 tests passed
TypeScript/Vite production build: passed, 917 modules transformed
```

The production build emitted a non-blocking bundle-size warning for the main
MapLibre/MUI chunk. Code splitting remains a later performance improvement; it
does not affect Milestone 1 correctness.

## Restart persistence

The complete stack was stopped with `docker compose down` (without `-v`) and
started again. Before and after the restart, PostgreSQL reported the same run
facts:

```text
PARTIAL|2 routes|1 unassigned order
```

The run remained retrievable through the API. Three representative processed
MLD artifacts retained identical SHA-256 hashes:

```text
santiago-demo.osrm.partition      CAC6D3BCC2CAAC259C0E82D5F01A5A6F4F8541179BF08212D222730E91317AF1
santiago-demo.osrm.cell_metrics   D7C941484CA99FEDD05633382C5C96468C5A26012067CF1C3C9C2095C6870BCD
santiago-demo.osrm.mldgr          8BDEB04CFF2C5120D9CC3FE17D739F8C0E9018B0FA60F420822A93457535BC14
```

## Acceptance criteria

| Criterion | Result and evidence |
|---|---|
| Documented command starts on Linux and Docker Desktop/WSL2 | Passed on `desktop-linux`, `linux/amd64`; documented quick start used. |
| Liveness/readiness are distinct and VROOM reaches OSRM | Passed; dependency probe performs a real VROOM optimization and OSRM logs show its table/route requests. |
| At least one routed and one known infeasible order | Passed; 4 routed and `ORD-003` proven infeasible. |
| Map geometry and tabular sequence agree with persistence | Automated checks passed; JSON/PostGIS point counts matched and frontend tests cover geometry mapping, stable colors, source updates, layer styling, and sequence rendering. On 2026-09-28 the user confirmed both routes were visible in the running dashboard. |
| Counts, distance, duration, service, wait, vehicles, solver time | Passed; exposed as run-derived KPIs and asserted by unit and live integration tests. |
| Restart preserves PostgreSQL and OSRM data | Passed; run facts and MLD artifact hashes were unchanged. |
| No required path uses mocked VROOM/OSRM | Passed; smoke and Compose integration used the pinned live services. Mocks remain only in isolated unit contract tests. |

No remote publication or Milestone 2 work was performed as part of this
validation.
