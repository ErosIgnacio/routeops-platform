# Delivery 2.5: planning published revisions

Status: implemented on `feat/m2-5-planning-execution`, awaiting review. No 2.5
commit or push is part of this delivery.

## Run lifecycle and inventory

The planning page is `/planning`. Select an active scenario and its latest
published revision, choose `alternatives-v2` or `greedy-v1`, and request a run.
The browser saves the scenario, revision, run ID, and idempotency key locally. A
lost response can be recovered through the key lookup. “Nueva clave” starts a
new operation and never releases an existing reservation.

`POST /api/v1/scenarios/{scenario_id}/revisions/{revision_no}/runs` requires
`Idempotency-Key` (1–100 characters) and accepts `solution_quality` and
`allocation_policy`. A matching retry returns HTTP 200 and the same run; a new
job returns 202; a reused key with different input returns 409. Read endpoints:

- `GET /api/v1/scenarios/{scenario_id}/revisions/{revision_no}/runs/lookup?key=…`
- `GET /api/v1/scenarios/{scenario_id}/revision-runs?offset=0&limit=50`
- `GET /api/v1/revision-runs/{run_id}`

`POST /api/v1/revision-runs/{run_id}/accept` and `/cancel` are idempotent for
their own terminal state and return 409 for incompatible transitions. The run
states are `QUEUED → RUNNING → READY → ACCEPTED`, with `CANCELED` from queued,
running, or ready and `FAILED` from queued or running. A recovered worker can
repeat `RUNNING → RUNNING`; each transition has an event. A lease token fences
the previous worker. Heartbeats extend a configurable lease; a job that exceeds
the configured attempt count fails and releases its holds. The worker starts as
the `planning-worker` Compose service. `READY` holds and `ACCEPTED` reservations
have no automatic expiry.

The run input binds the immutable revision, imported inventory snapshot,
content and context hashes, contract version, quality, and allocation policy.
The worker rejects an incomplete or oversized revision before allocating.
OSRM calculates a center-to-order matrix outside inventory locks. The 2.4
allocation service takes that frozen matrix and rechecks live stock under its
ordered PostgreSQL locks. VROOM is called only via `SolverGateway`, after the
reservation transaction commits. Its result is reconciled against the exact
tasks and vehicles before persistence. Unknown or duplicate tasks, wrong
centers, incompatible skills/capacity, and invalid route endpoints are rejected.

Each chosen order has a run reservation and immutable transition events. A
solver-unassigned order releases only its own RouteOps hold. A ready routed
order remains `HELD`; acceptance confirms it; cancellation or failure releases
it. The PostgreSQL trigger decrements RouteOps counters exactly once on release
without changing imported external reservations. Acceptance and cancellation
serialize through the scenario and job row locks. Allocation evidence and
inventory snapshots remain available to reconstruct the decision. The 2.4
attempt is kept as an immutable aggregate after linkage; the per-order records
are authoritative for a planning run.

## Reproducible demos

The `/planning` selector lists the original `ORD-003` demo and the four 2.4
fixtures. The original opens the unchanged Milestone 1 dashboard. For a 2.4
fixture, “Crear escenario demo” uses the normal five-CSV upload, validation, and
publication pipeline to create a **new** scenario with one revision. Run that
revision, then inspect route, chosen center, candidates, exception and
reservation. Preparing it again creates another scenario; it does not reset or
release prior stock.

| Fixture | Expected result with `alternatives-v2` |
| --- | --- |
| Exclusive stock | Orders route from their covering CD; a mixed-line order is `STOCK_NO_FULL_COVERAGE`. |
| Choice between CDs | One CD is chosen deterministically; the other candidate and duration remain in evidence. |
| Shared stock, restricted order | Flexible order uses CD-B and restricted order uses CD-A; `greedy-v1` strands the restricted order. |
| Fleet restrictions | Stock exists, but skill/capacity failures produce `NO_COMPATIBLE_VEHICLE`. |

This is deterministic assignment, not a proof of globally optimal routes. A
fleet compatibility check does not guarantee route feasibility; VROOM can leave
an allocated task unassigned. Infrastructure failures appear separately from
proven stock and fleet exceptions.

## Local VROOM/OSRM measurement and limit

Run `PYTHONPATH=src python scripts/benchmark_planning.py` from `backend` while
the local OSRM and VROOM services are running. The script sends a synthetic
20-order, 60-line, 4-center, 6-vehicle problem and an 80-cell OSRM matrix. Each
synthetic order has three real `OrderLine` values aggregated through the domain
model. It does not create database rows or reservations. Three local runs on Windows 11,
16 logical CPUs, produced:

| Sample | Prepare | OSRM matrix | VROOM solve | Python `tracemalloc` peak | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 2 ms | 118 ms | 57 ms | 1,994,460 bytes | 4 routes, 20 assigned |
| 2 | 2 ms | 113 ms | 61 ms | 1,994,813 bytes | 4 routes, 20 assigned |
| 3 | 2 ms | 139 ms | 45 ms | 1,994,053 bytes | 4 routes, 20 assigned |

The default run limit matches that observed workload: 20 orders, 60 lines, 6
vehicles, and 80 center × order matrix cells. Each is configurable using
`ROUTEOPS_PLANNING_MAX_ORDERS`, `_MAX_LINES`, `_MAX_VEHICLES`, and
`_MAX_MATRIX_CELLS`. `ROUTEOPS_PLANNING_LEASE_SECONDS` (120),
`_MAX_ATTEMPTS` (3), and `_POLL_SECONDS` (5) control recovery. The API rejects
oversized revisions with `SOLVER_WORKLOAD_LIMIT` (HTTP 413) before reservations.
The import row limits do not imply planning capacity.

These samples measured client-side elapsed time and Python allocations for one
synthetic request. They do not measure VROOM/OSRM process RSS, HTTP concurrency,
or a maximum safe production workload. Delivery 2.6 will measure the integrated
HTTP workflow under concurrency and set acceptance targets from those results.
