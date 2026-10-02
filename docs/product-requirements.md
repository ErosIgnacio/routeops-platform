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

## Delivery status and B2B/B2C

Milestones 1–2 and `v0.2.1` are accepted. Joint 3.1 is accepted and published.
The [3.2 candidate](milestone-3-2-plan-comparison.md) implements manual baselines
and controlled policy comparison by API, pending review. Analytics UI and
exports remain 3.3 scope. 3.1a supplies the business cost foundation and 3.1b
supplies the central indicator API.
B2B and B2C share one architecture and explicit input fields. Independent
synthetic cases were accepted in 3.1c; labels activate no implicit rules.

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
5. One-CD-per-order allocation: `alternatives-v2` is the revision default,
   `greedy-v1` remains available for comparison; the original demo retains greedy.
   Neither heuristic is presented as a global optimum.
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

- Splitting an order across distribution centers or deliveries.
- Inventory transfers, replenishment, pre-consolidation, substitution, lots, or
  expiry dates.
- Direct WMS/ERP/TMS integrations.
- Address geocoding or use of real customer addresses.
- Live traffic, dynamic dispatch, driver mobile apps, and proof of delivery.
- RouteEngine or any solver other than VROOM.
- Multi-user authentication, authorization, tenancy, and audit identities.
- Redis, Celery, Kubernetes, or independently deployed application
  microservices.
- Production hosting and network access beyond the trusted local machine.
- Palletization, dock appointments, returns and driver tracking.

Extension points may be named and modeled, but no inactive framework or
placeholder implementation will be built for these exclusions.

## Core workflow and state guards

The initial DRAFT-to-COMPLETED design is now represented by separate resources:

- Scenario: `ACTIVE` / `ARCHIVED`; published revisions are immutable.
- Import: `RECEIVED → VALIDATING → VALID | INVALID | FAILED`; `VALID → PUBLISHED`.
  Expiration is an append-only retention record and blocks publication.
- Imported run: `QUEUED → RUNNING → READY → ACCEPTED | CANCELED`, or `FAILED`.
  Leases, owner tokens and bounded retries recover interrupted workers.
- Complete per-order HELD reserves are created atomically with decision evidence.
  Solver-unassigned orders release every line. Acceptance confirms; cancellation
  and failure release only the run's held stock, never external reservations.
- External calls hold no inventory locks. New revisions preserve active reserves.
  READY runs do not expire automatically. Reruns create distinct runs.


## Allocation policy v1

Available stock is:

```text
available = on_hand - externally_reserved - safety_stock - active_routeops_reserved
```

Orders are processed deterministically by:

1. priority descending;
2. earliest window end ascending;
3. stable `order_id` ascending.

For each order, a distribution center is eligible only when it can cover all
order lines and has at least one potentially compatible vehicle. For `greedy-v1`, eligible centers are ranked by:

1. OSRM estimated travel duration from the center to the order, ascending;
2. post-allocation inventory slack, descending;
3. stable `distribution_center_id`, ascending.

The chosen center is reserved immediately inside the transaction so later
orders see the reduced availability. If no center covers all lines, the order
gets the proven reason `STOCK_NO_FULL_COVERAGE`. Routing feasibility remains
VROOM's responsibility; prechecks are diagnostics, not claims of optimality.

`alternatives-v2` first maximizes the number of remaining orders that retain
at least one stock-and-fleet alternative after the hypothetical assignment,
then uses the same duration/slack/ID tie breaks. Order sequence is unchanged.
This is one-step lookahead, not a solution to joint packing or a global optimum.

If OSRM is unavailable while ranking multiple otherwise eligible centers, the
run fails explicitly rather than silently changing the policy. The implemented policy boundary permits evaluated alternatives.

## KPI definitions (3.1b accepted)

Every KPI includes its unit and denominator. Undefined ratios return `null`,
not zero.
The centralized [3.1b catalog](milestone-3-1b-metrics.md) supplies units,
denominators, calculation versions, provenance and missing-data reasons.
Base distances/times use meters/seconds; kilometer/hour presentation is a
conversion. Analytics screens/exports remain 3.3; manual deltas are implemented
in the 3.2 candidate using the same catalog.

- Total, allocated, routed, and unassigned orders.
- Assignment rate = routed orders / valid input orders.
- Total distance (km), route duration, driving time, service time, and waiting
  time (hours).
- Kilometers per routed order.
- Per-route utilization peak and average for units, weight, and volume.
- Window compliance = routed orders whose service starts within their inclusive
  window / routed orders. Windows are required in v1; absent historical windows
  make the metric unavailable rather than changing its denominator.
- Estimated cost and cost per routed order, in the scenario currency.
- Used vehicles and duration per route.
- Work balance: min/max/mean/population stddev and coefficient of variation of
  route duty. CV is `null` with fewer than two used vehicles or zero mean;
  a single route has population stddev zero.
- Processing: initial queue, active phases by attempt, retry/recovery waits and
  direct creation-to-pre-commit READY/processing-terminal timestamp interval.
  Commit acknowledgment is not recorded; durable total is unavailable. Import validation, browser
  polling and user review waiting are separate. Unknown interruption intervals
  remain `null`; known failed-attempt phases are retained.
- Baseline delta for distance, duration, estimated cost, vehicles, and
  assignment rate. Both plans use the same normalized inputs and OSRM dataset.

Estimated business cost is recomputed from persisted route duty time, distance,
and fixed cost using exact decimals. VROOM's integer objective is a scaled
optimization proxy; any engine limitation around the pricing of waiting time is
reported separately. `kpis.estimated_cost` remains the historical scaled solver
objective. 3.1a adds a versioned read-only estimated-operating-cost query; no
existing cost field is silently repurposed.

## Unassigned-order explanations

Reason records have `code`, `certainty` (`PROVEN` or `INFERRED`), human-readable
detail, and structured evidence. Priority order avoids misleading diagnoses:

1. invalid coordinates — proven during validation;
2. no full stock coverage / no eligible center — proven for recorded availability
   at that policy decision, not for every possible joint allocation;
3. no skill-compatible vehicle — proven by set inclusion;
4. demand exceeds every compatible vehicle's individual capacity — proven;
5. window/service cannot fit the effective vehicle/CD/horizon shift even with
   zero travel — proven for the assigned center and evaluated compatible fleet;
   an OSRM direct route is not automatically a universal lower bound;
6. shift or fleet-wide packing infeasibility — inferred unless conclusive.
   Optional distance/driving/task maxima now constrain VROOM requests and are
   checked on response. 3.1c records their context without asserting individual
   causality; a task-count shortfall can be proven collectively for the fixed
   center assignment when every vehicle has a defined maximum;
7. solver unassigned without conclusive local cause — inferred;
8. excessive road snap/no route coverage — proven coverage failure before
   reserving stock, distinct from an infrastructure outage;
9. routing/solver error — proven operational failure, not business
   infeasibility.

Allocation keeps published umbrella codes as primary, matching recorded
decisions, with specific skill/capacity evidence as supplementary reasons.
For solver omissions, a conclusive local cause precedes the generic inferred
omission. Input incidences remain separate from run diagnostics. Sources,
calculation version and evaluated scope accompany reasons; historical reads
never use today's inventory. See [3.1c catalog and evidence](milestone-3-1c-diagnostics.md).

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
