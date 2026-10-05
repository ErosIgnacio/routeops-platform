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
- Preserve the synthetic `ORD-003` regression: it requires 20 units of
  `SKU-C`, while each center has only 5 available after safety stock. Its
  `STOCK_NO_FULL_COVERAGE` result is correct; this fixture must not be changed
  to demonstrate another allocation problem.

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

Status: **Milestone 2 formally accepted and closed as `v0.2.0`**. Deliveries
2.1–2.6 are complete. The acceptance evidence, local HTTP benchmarks,
objectives, and known limitations are in
[the 2.6 acceptance report](milestone-2-6-acceptance.md). The import workspace
and local performance samples for 2.3 are recorded in
[the 2.3d guide](milestone-2-3d-import-ui.md). These single-run measurements are
not capacity guarantees. The 2.4 inventory, policy evaluation, and reservations
are recorded in [the 2.4 guide](milestone-2-4-allocation.md). The 2.5 run
lifecycle, demos and measured local solver workload are recorded in
[the 2.5 guide](milestone-2-5-planning.md).

The [v0.2.1 correction](v0.2.1-routing-ui.md) was formally accepted by the user,
including its visual review, on 2026-10-01. It closes the road-network snapping
and visual workflow findings with the automated results and browser evidence
summarized in that guide. The `v0.2.0` acceptance record remains historical;
Milestone 3 begins with the separately authorized 3.1a below.

Approved delivery sequence (2026-09-28):

1. **2.1:** input contracts, reproducible templates, and bounded validation.
2. **2.2:** persistent model, migrations, and immutable scenario/import revisions.
3. **2.3:** provisional upload, error report, atomic publication, and interface.
4. **2.4:** deterministic center assignment and transactional RouteOps reservations.
5. **2.5:** execution, history, run interface, multiple-demo selection, and
   measured VROOM workload limit.
6. **2.6:** integrated acceptance, concurrency, recovery, and demo presentation.

Delivery 2.6 measured import and planning through HTTP under concurrent
clients, including process RSS and container memory. Its targets apply only to
the local measured hardware and workloads; the service-level samples from 2.3d
remain separate from these HTTP observations.
Known nonblocking limits are the local-only deployment, no global-optimality
claim for assignment, polling-related latency variation, and the Vite bundle
size warning. Published import packages can exceed the independently measured
planning limits and receive a pre-run HTTP 413 without creating reservations.

Assignment demos and phase boundaries:

- **2.4 — synthetic data and evaluation:** (1) stock exclusive to different
  centers, including an order whose lines no single center can cover;
  (2) an order eligible at several centers, with the chosen origin and rejected
  candidates recorded; (3) shared stock plus an order whose SKU coverage exists
  at only one center, where assigning a flexible order first to its nearer
  center makes the restricted order fail despite a feasible joint assignment;
  (4) stock-covered orders
  blocked by vehicle capacity or required skills. Keep `ORD-003` as a separate
  stock-coverage regression. Each demo has deterministic inputs, expected
  decisions, exception reasons, and automated assertions.
- **2.4 — assignment policy:** orders need not name an origin. RouteOps chooses
  one center that can cover every line, checks compatible fleet, records the
  candidates and reason for its deterministic tie break, and reserves stock
  before constructing solver tasks. Add a test that makes the current
  `greedy-v1` policy strand the restricted order even though assigning the
  flexible order to another center would cover both. Evaluate an auditable
  improvement that considers the remaining orders' alternatives or scarcity;
  compare it with `greedy-v1` on the same fixture. Do not describe either
  policy as globally optimal without proof.
- **2.5–2.6 — user experience:** select and run several synthetic demos and
  show each order's assigned center, considered centers, and explicit exception
  reasons alongside the route result. Integration and acceptance tests verify
  that the displayed decision matches persisted evidence.
- **Milestone 3 — comparison:** compare assignment strategies across demos
  using delivered orders, exceptions, route cost, and fleet use under the same
  stock and OSRM assumptions; report tradeoffs and workload limits. The basic
  origin decision remains in 2.4. VROOM remains reachable only through
  `SolverGateway`.

See [the 2.1 contract](milestone-2-1-import-validation.md). Validation and
planning now recover through PostgreSQL leases and fenced owner tokens, not
a speculative 30-minute timer. READY runs and confirmed reservations do not
expire automatically. The accepted 2.3b/2.5 guides describe their lifecycles.

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
- The restricted-order demo exposes the current greedy limitation, and the
  evaluated replacement remains deterministic and auditable.

## Milestone 3 — operation and analytics

Status: **complete and accepted on 2026-10-03; release `v0.3.0`**, based on
published `v0.2.1` (`0241a5168d9f7c02a152cf3614bf502659ea33b3`). The shared branch
`feat/m3-1-operation-analytics` served the three parts of 3.1; the three milestone
branches are retired after their integration and release are verified.

Approved sequence:

1. **3.1a:** executable v1 contracts, traceable restriction matrix, exact
   business-cost foundation, optional vehicle distance/driving/task limits,
   versioned import compatibility and current documentation. Allocation policies
   are unchanged.
2. **3.1b:** complete versioned indicator catalog, central calculation/queries,
   append-only fenced attempt/phase measurements and historical compatibility.
3. **3.1c:** layered diagnoses with evidence and independent B2B/B2C cases.
4. **3.2:** manual plan and comparison of strategies.
5. **3.3:** interface, exports and integrated acceptance.

See [the 3.1a report](milestone-3-1a-contracts-costs.md).
3.1a closed at `87f765079c1b03e5e76dfced795a04a32347920b` in local/remote main
and the shared branch. See [3.1b formulas and targeted evidence](milestone-3-1b-metrics.md).
3.1b closed at `f21effb531a92fe556708989e93a79bde4209588` in local/remote main
and the shared branch. The [3.1c report](milestone-3-1c-diagnostics.md) records
the implemented diagnostics, five isolated cases and joint backend, HTTP,
migration, real smoke/demo and frontend validation. Two obsolete downgrade
expectations were corrected and the affected tests passed. Acceptance on
2026-10-02 closed 3.1c/joint 3.1 at `0f95fcf507ea219345836cbaeca26e86c1787bd4`,
verified in `main` and `feat/m3-1-operation-analytics` locally and on origin.
Delivery 3.2 closed at `1fc356881b5bfa899399beafe3952da4f112f498`, verified in
local/remote `main` and `feat/m3-2-plan-comparison`, after directed departure-condition checks.
See [the comparison contract, reproduction and evidence](milestone-3-2-plan-comparison.md).
Manual fixed-sequence evaluation, frozen availability, unchanged assignment
policies, central KPIs/deltas and immutable leased jobs are implemented by API.
No analytical job creates operational reservations. Historical operational-run
baselines, declared times and common-served subplan comparisons are not implemented.
Delivery 3.3 completes operation analytics, the exact manual editor, comparison history/maps,
CSV/XLSX exports and integrated acceptance. See [3.3 evidence and remaining
limitations](milestone-3-3-analytics-exports.md). Backend 332 and frontend 36
tests passed, including real HTTP/PostGIS/OSRM/VROOM flows and export consistency.
Desktop/mobile and real downloads were reviewed in the integrated browser;
captures remain outside the repository. The user accepted the whole milestone
and authorized its release; no Hito 4 work has started.
The [final review matrix](milestone-3-final-review.md) separates model-specific
B2B/B2C browser/API observations from shared automated checks. Additional isolated
B2C import, acceptance, cancellation, comparison and export evidence closes the
relevant review gaps. The README includes two selected synthetic captures;
extensive reports and evidence remain external. The final folder and ZIP contain
128 matching files, including 45 readable captures; approved functional files
remain unchanged. The user manually removed the provisional folder after the
recorded policy rejection; its absence was verified without retrying deletion.
The two disposable check containers were already removed, with their reports
preserved. Nine operational services and both persistent volumes remain intact.
`v0.3.0` identifies the accepted whole milestone. Existing import and
solver limits and the OSRM extract remain unchanged. B2B/B2C labels add no
implicit constraints or integration services.

Deliverables:

- Complete v1 constraints and cost mapping.
- Manual baseline import/evaluation and scenario comparison.
- Layered unassigned explanation with certainty/evidence.
- Full KPI catalog and CSV/`.xlsx` exports.
- Richer route, utilization, comparison, and diagnostic UI.
- Global comparison of assignment strategies and their outcomes on the
  synthetic demo suite, without an unsupported optimality claim.

Acceptance criteria:

- Contract fixtures prove every constraint is mapped to VROOM correctly.
- KPI formulas reconcile against route facts and handle zero denominators.
- Manual and optimized plans use the same OSRM data and cost assumptions.
- Proven/inferred causes are visually distinct and backed by stored evidence.
- Exports match displayed values and protect against spreadsheet formula
  injection.

## Milestone 4 — portfolio quality

Status: **4.1 closed and published on 2026-10-04**, documentary closure
`3f4a7d6f24b7c174a76feadfff0fbf82db974958` in local/remote main and its branch at that closure;
[CI 37242798198](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37242798198)
passed automatically after the documentation-only push. Accepted functional commit
`f7582f0dc9f936fe2b93da291df9d47016dd01d9`, from accepted Hito 3
`c308bfb5224edf8268102cafd813faa8e9d5f206`. Documentary closure and fast-forward
publication completed; no Milestone 4 tag is authorized. The three local
check resources retained after `blocked by policy` do not condition closure.

Approved deliveries:

1. **4.1 — automated quality and CI:** formatting, Ruff, strict mypy, unit and
   frontend checks, clean Linux Compose integration, migrations, real routing,
   smoke and retained test evidence. See [the CI runbook](milestone-4-1-quality-ci.md).
2. **4.2 — performance and basic security:** measured resource/performance
   hardening and security boundaries. Technically accepted for local single-user
   use on `feat/m4-2-performance-security`. Approved review commit:
   `fa02d7133bc2bb2f15027960f854eb16e27bcd37`,
   [CI 37250283674](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37250283674)
   passed 381 backend/36 frontend tests, eight CI-tool contracts and three Node
   guard contracts, with migrations and real routing smoke. Earlier benchmark
   baseline `88f5398517a5a274ee02a8c986720d01b80833f3`: [CI 37245515724](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37245515724)
   passed 350 backend/36 frontend tests, eight CI-tool contracts, real integration
   and 39 measured HTTP samples plus ten warmups. Upstream image advisories
   remain explicitly inventoried; no limits/workers/timeouts were increased.
   See [measurements, local boundaries and dependency findings](milestone-4-2-performance-security.md).
   The [accepted image assessment](milestone-4-2-image-security-review.md)
   groups 47 priority component families, distinguishes backports and vendors,
   mitigates malformed XLSX Unicode and solver HTTP exposure, and replaces only
   the unsupported VROOM Node executable. Solver/profile/map remain unchanged.
   Residual advisories and installation-tool findings remain tracked; no security
   certification or public deployment is implied. Existing performance samples
   precede these mitigations and the Node replacement. Documentary closure uses
   branch CI followed by fast-forward publication to main and its automatic CI;
   no manual full-suite or benchmark rerun is required for documentation alone.
   Keep branches 4.1 and 4.2 until Milestone 4 closes; no new tag is authorized.
3. **4.3 — documentation, reproduction and portfolio presentation:** final
   runbooks, backup/reset guidance, third-party review and presentation. Not started.

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
- Planning workload stays within bounded leased jobs (original demo synchronous); the limit will be
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
| Long synchronous requests | Workload cap and measurement; PostgreSQL workers and bounded workloads |
| Spreadsheet attacks | No macros/formulas/external links; escape export cells beginning with formula sigils |

## Decisions approved before Milestone 1

1. **License:** Apache-2.0, with separate dependency and ODbL notices.
2. **Map extract:** bounded Santiago source, 50 MiB cap, committed lock metadata.
3. **Route end policy:** closed routes returning to the originating center.
4. **Manual baseline:** vehicle + ordered order IDs; optional planned timestamps
   do not replace facts recomputed by the pinned OSRM dataset.
