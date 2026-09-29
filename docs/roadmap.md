# Implementation roadmap and acceptance criteria

Work stops for review at the end of each milestone. Completion reports must
state what was implemented, verified, tested, decided, and what risks remain.

## Milestone 0 — design

Deliverables:

- Final v1 scope and exclusions.
- Architecture and repository structure.
- Logical data model and transaction model.
- Formal input and optimization contracts.
- ADR for VROOM/OSRM and verified version pins.
- Roadmap, acceptance criteria, assumptions, risks, and pending decisions.

Acceptance criteria:

- Business rules are unambiguous enough to write unit tests.
- No domain contract imports VROOM-specific shapes.
- Inventory concurrency has an explicit database transaction boundary.
- Every requested input field has a type, unit, required flag, and validation.
- Version claims link to official upstream sources.
- The user approves the design before Milestone 1 starts.

## Milestone 1 — executable vertical slice

Status: **formally accepted by the user on 2026-09-28 after runtime validation
and confirmation that both routes render on the map**.
Reproducible evidence is recorded in
[the Milestone 1 validation report](research/milestone-1-validation.md).

Deliverables:

- Repository/tooling scaffold, dependency locks, `.env.example`, and Compose.
- PostgreSQL/PostGIS, pinned OSRM and VROOM, FastAPI, React/Vite, and health
  checks.
- Small seed-based synthetic Santiago fixture with two centers, vehicles, and
  orders; source coordinates explicitly marked synthetic.
- Hard-coded/repository fixture ingestion through domain models (not yet general
  file upload), deterministic center assignment, VROOM solve, persistence,
  route map, unassigned list, and first KPIs.
- Backend unit/contract/integration tests, frontend component test, and one
  cross-stack smoke test.

Acceptance criteria:

- A documented command starts the stack on Linux and Docker Desktop/WSL2.
- Health checks distinguish liveness/readiness and prove VROOM can reach OSRM.
- The demo routes at least one order and deliberately leaves at least one known
  infeasible order unassigned.
- Map route geometry and tabular sequence agree with persisted facts.
- Counts, distance, duration, service, wait, vehicles used, and solver time are
  derived from the run and covered by assertions.
- Restarting the stack preserves PostgreSQL and OSRM processed data.
- No required path relies on a mocked VROOM/OSRM response.

## Milestone 2 — data and inventory

Approved delivery sequence (2026-09-28):

1. **2.1:** input contracts, reproducible templates, and bounded validation.
2. **2.2:** persistent model, migrations, and immutable scenario/import revisions.
3. **2.3:** provisional upload, error report, atomic publication, and interface.
4. **2.4:** deterministic center assignment and transactional RouteOps reservations.
5. **2.5:** execution, history, run interface, and measured VROOM workload limit.
6. **2.6:** integrated acceptance, concurrency, and recovery tests.

See [the 2.1 contract](milestone-2-1-import-validation.md). Processing runs
stale for more than a configurable 30 minutes will be recovered idempotently
in a later delivery; ready-for-review runs and committed reservations do not
expire automatically. No such recovery is part of 2.1.

Deliverables:

- Downloadable CSV/`.xlsx` templates and secure upload pipeline.
- Full validation report and atomic staged publication.
- Alembic inventory/snapshot/reservation schema.
- Versioned allocation policy, transactional reservation, idempotency, release,
  and scenario history.
- Concurrent integration tests against PostgreSQL.

Acceptance criteria:

- Valid templates round-trip; representative invalid fixtures produce stable
  row/field error codes.
- Two concurrent runs cannot reserve the same availability.
- Multi-line orders allocate completely to one center or receive a proven stock
  reason; no partial stock reservation survives a rollback.
- Retrying an idempotent request neither duplicates reservations nor runs.
- Snapshot and allocation evidence can reconstruct each decision.

## Milestone 3 — operation and analytics

Deliverables:

- Complete v1 constraints and cost mapping.
- Manual baseline import/evaluation and scenario comparison.
- Layered unassigned explanation with certainty/evidence.
- Full KPI catalog and CSV/`.xlsx` exports.
- Richer route, utilization, comparison, and diagnostic UI.

Acceptance criteria:

- Contract fixtures prove every constraint is mapped to VROOM correctly.
- KPI formulas reconcile against route facts and handle zero denominators.
- Manual and optimized plans use the same OSRM data and cost assumptions.
- Proven/inferred causes are visually distinct and backed by stored evidence.
- Exports match displayed values and protect against spreadsheet formula
  injection.

## Milestone 4 — portfolio quality

Deliverables:

- Test pyramid completion, deterministic benchmarks, upload/resource hardening,
  CI, architecture/data/API documentation, third-party notices, and demo assets.
- Windows/WSL2 and Linux runbooks, backup/reset instructions for synthetic data,
  and publication checklist. No publication is performed.

Acceptance criteria:

- Formatting, lint, static analysis, tests, migration checks, frontend build,
  and container smoke test pass in CI.
- Benchmarks report environment, dataset seed/size, commands, raw results, and
  variability; no invented targets or results.
- Dependency and image versions are locked and upgrade instructions cover OSRM
  dataset rebuilds.
- README can take a new reviewer from clone to a successful synthetic run.
- Licenses, OSM attribution, and third-party notices are complete.

## Assumptions

- The initial planning horizon is one local calendar day.
- All routes start at a vehicle's assigned center and return to that same center.
- One OSRM `car` profile serves all v1 vehicle types; speed factors or separate
  profiles require measured justification.
- The deployment is trusted, local, and single-user, but uploaded files are
  still treated as untrusted input.
- Planning workload stays within a bounded synchronous solve; the limit will be
  measured rather than guessed.

## Principal risks and mitigations

| Risk | Mitigation |
|---|---|
| Windows bind mounts and line endings | Linux containers, named volumes, WSL2 smoke runbook |
| Large OSM extract/startup time | Small clipped dataset, checksum, pre-processing script, no silent download |
| Non-reproducible routing after map updates | Pin image and extract checksum/date; preserve run provenance |
| Inventory races | Stable row-lock order, atomic ledger/counter updates, concurrency tests |
| Misleading unassigned causes | Evidence model and explicit `PROVEN`/`INFERRED` certainty |
| VROOM/OSRM outage or malformed response | Bounded retries, timeouts, response reconciliation, distinct failure states |
| Decimal-to-integer distortion | Exact decimal validation and documented integer scales |
| Long synchronous requests | Workload cap and measurement; job runner only after demonstrated need |
| Spreadsheet attacks | No macros/formulas/external links; escape export cells beginning with formula sigils |

## Decisions approved before Milestone 1

1. **License:** Apache-2.0, with separate dependency and ODbL notices.
2. **Map extract:** bounded Santiago source, 50 MiB cap, committed lock metadata.
3. **Route end policy:** closed routes returning to the originating center.
4. **Manual baseline:** vehicle + ordered order IDs; optional planned timestamps
   do not replace facts recomputed by the pinned OSRM dataset.
