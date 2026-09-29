# RouteOps v1 product requirements

## Product outcome

RouteOps v1 demonstrates an auditable end-to-end planning workflow for a
fictitious last-mile operation in Santiago and the Metropolitan Region. A user
can load synthetic operational data, understand data-quality problems, allocate
complete orders to one distribution center, reserve stock, optimize routes,
inspect results and KPIs, compare against a manual baseline, and export the
plan.

VROOM is the only optimization engine in v1. It is accessed through an internal
solver port so a future engine can be added without changing allocation,
inventory, KPI, or scenario rules.

## Actors

- **Planner:** creates scenarios, imports data, runs planning, reviews maps and
  diagnostics, compares results, and exports plans.
- **Portfolio reviewer/developer:** runs the stack locally, reproduces the
  synthetic dataset, tests the rules, and inspects architectural decisions.

RouteOps v1 is single-user and has no authentication or authorization model.

## In scope

1. Scenario lifecycle: draft, data validation, inventory snapshot, allocation,
   optimization, result review, export, cancellation.
2. CSV and `.xlsx` imports for orders, order lines, inventory, distribution
   centers, and vehicles.
3. Strict schema, relational, and business validation with row-level errors and
   warnings. An import is published atomically only when it has no errors.
4. Multiple distribution centers, per-SKU stock, and heterogeneous fleets.
5. One-CD-per-order allocation using a documented deterministic greedy
   heuristic. It is explicitly not presented as a global optimum.
6. Transactional inventory snapshots and reservations, with idempotent retry
   behavior and no double consumption between concurrent planning runs.
7. Capacity dimensions for units, weight, and volume; work shifts; delivery
   windows; service time; priority; and required/vehicle skills.
8. Fixed, hourly, and distance-based vehicle costs.
9. VROOM optimization with OSRM matrices and route geometry.
10. Persistence of normalized input, parameters, immutable engine payloads,
    engine and map-data versions, result, diagnostics, and processing times.
11. Map and tabular views for routes, sequence, ETA, load, waiting, service,
    distance, and unassigned orders.
12. KPIs from persisted run facts, never generated or invented values.
13. Manual-versus-optimized comparison using the same normalized data and OSRM
    dataset.
14. CSV and `.xlsx` export of route details, unassigned orders, and KPI summary.
15. A seed-based synthetic dataset clearly marked as fictitious.

## Out of scope

- Splitting an order across distribution centers.
- Inventory transfers, replenishment, pre-consolidation, substitution, lots, or
  expiry dates.
- Direct WMS/ERP/TMS integrations.
- Address geocoding or use of real customer addresses.
- Live traffic, dynamic dispatch, driver mobile apps, and proof of delivery.
- RouteEngine or any solver other than VROOM.
- Multi-user authentication, authorization, tenancy, and audit identities.
- Redis, Celery, Kubernetes, or independently deployed application
  microservices.
- Production hosting, publishing, and `git push`.

Extension points may be named and modeled, but no inactive framework or
placeholder implementation will be built for these exclusions.

## Core workflow and state guards

```text
DRAFT
  -> DATA_VALIDATED
  -> SNAPSHOT_READY
  -> STOCK_RESERVED
  -> OPTIMIZING
  -> COMPLETED | COMPLETED_WITH_UNASSIGNED | FAILED

DRAFT..STOCK_RESERVED -> CANCELLED
FAILED/CANCELLED       -> reservations released when applicable
```

- A scenario cannot take a snapshot until the whole import package passes.
- Snapshot, allocation, and reservation form one consistency boundary.
- Optimization cannot start without active reservations for every allocated
  order.
- A run is immutable after completion. A re-run creates a new planning run.
- Accepting a plan commits its reservations; cancellation/failure releases held
  reservations according to the explicit state transition.

## Allocation policy v1

Available stock is:

```text
available = on_hand - reserved - safety_stock
```

Orders are processed deterministically by:

1. priority descending;
2. earliest window end ascending;
3. stable `order_id` ascending.

For each order, a distribution center is eligible only when it can cover all
order lines and has at least one potentially compatible vehicle. Eligible
centers are ranked by:

1. OSRM estimated travel duration from the center to the order, ascending;
2. post-allocation inventory slack, descending;
3. stable `distribution_center_id`, ascending.

The chosen center is reserved immediately inside the transaction so later
orders see the reduced availability. If no center covers all lines, the order
gets the proven reason `STOCK_NO_FULL_COVERAGE`. Routing feasibility remains
VROOM's responsibility; prechecks are diagnostics, not claims of optimality.

If OSRM is unavailable while ranking multiple otherwise eligible centers, the
run fails explicitly rather than silently changing the policy. A future policy
port can replace this heuristic.

## KPI definitions

Every KPI includes its unit and denominator. Undefined ratios return `null`,
not zero.

- Total, allocated, routed, and unassigned orders.
- Assignment rate = routed orders / valid input orders.
- Total distance (km), route duration, driving time, service time, and waiting
  time (hours).
- Kilometers per routed order.
- Per-route utilization peak and average for units, weight, and volume.
- Window compliance = on-time routed orders / routed orders with a window.
- Estimated cost and cost per routed order, in the scenario currency.
- Used vehicles and duration per route.
- Work balance: min/max/mean/stddev and coefficient of variation of route work
  duration; `null` when fewer than two vehicles are used.
- Processing time split into validation, allocation, solver, and total wall
  time.
- Baseline delta for distance, duration, estimated cost, vehicles, and
  assignment rate. Both plans use the same normalized inputs and OSRM dataset.

Estimated business cost is recomputed from persisted route duty time, distance,
and fixed cost using exact decimals. VROOM's integer objective is a scaled
optimization proxy; any engine limitation around the pricing of waiting time is
reported with the run rather than hidden in the KPI.

## Unassigned-order explanations

Reason records have `code`, `certainty` (`PROVEN` or `INFERRED`), human-readable
detail, and structured evidence. Priority order avoids misleading diagnoses:

1. invalid coordinates — proven during validation;
2. no full stock coverage / no eligible center — proven during allocation;
3. no skill-compatible vehicle — proven by set inclusion;
4. demand exceeds every compatible vehicle's individual capacity — proven;
5. impossible time window even under direct travel lower bound — proven when
   the bound is conclusive, otherwise inferred;
6. shift, max distance/time/tasks, or fleet-wide packing infeasibility —
   inferred unless a conclusive check is available;
7. solver unassigned without conclusive local cause — inferred;
8. routing/solver error — proven operational failure, not business
   infeasibility.

## Non-functional requirements

- Works on Linux and Windows through Docker Desktop with the WSL2 backend.
- UTC timestamps in persistence; scenario timezone retained as an IANA name.
- UUID identifiers internally and stable source identifiers per scenario.
- Decimal parsing at ingestion and integer normalization at the solver boundary.
- Structured logs with correlation, scenario, run, and request identifiers; no
  uploaded row data or secrets in logs.
- Bounded uploads, row counts, timeouts, retries, and solver concurrency.
- No execution of uploaded content and no user-controlled filesystem paths.
- Unit, integration, contract, smoke, frontend, and end-to-end tests appropriate
  to each milestone.
- Reproducible migrations, dependency locks, pinned container images, and CI.

## Definition of done for RouteOps v1

The v1 release candidate is done when all milestone acceptance criteria pass on
Linux CI and the documented Windows/WSL2 smoke path, the synthetic demo can be
reproduced from a seed, no required flow depends on an undocumented mock, and
licenses/attributions are present.
