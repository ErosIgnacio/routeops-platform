# Data model and consistency rules

This is the logical model for RouteOps v1. Physical names, indexes, and PostGIS
types will be fixed in the first Alembic migration.

## Aggregates and entities

### Planning scenario

`Scenario` is the user-facing aggregate root.

- `id` UUID, `name`, `planning_date`, `timezone`, `currency`
- `status` and optimistic `version`
- `created_at`, `updated_at` in UTC
- source import package and zero or more immutable planning runs

### Imported planning data

- `ImportBatch`: file hash, original display name, dataset kind, parser version,
  status, counts, and timestamps.
- `ValidationIssue`: severity, stable code, dataset, sheet, row, field, message,
  and sanitized value excerpt.
- `DistributionCenter`: source ID, name, point, and operating interval.
- `Order`: source ID, customer reference, delivery point, priority, delivery
  window, service duration, and required skills.
- `OrderLine`: order, SKU, quantity, unit weight, and unit volume.
- `Vehicle`: source ID, home center, type, capacities, shift, skills, and costs.

Source IDs are unique inside a scenario; UUIDs are used for joins and APIs.
Coordinates are persisted as PostGIS `geometry(Point, 4326)` and validated
against the Santiago/RM operating envelope as a warning after global range
validation.

### Inventory and reservations

- `InventoryPosition`: current center/SKU quantities (`on_hand`, `reserved`,
  `safety_stock`) and a concurrency version; unique on center/SKU.
- `InventorySnapshot`: immutable header with `snapshot_at`, `created_at`, source
  import hash, and scenario/run relationship.
- `InventorySnapshotLine`: copied quantities and computed availability for each
  center/SKU.
- `StockAllocation`: one order to one center, policy name/version, rank evidence,
  and status.
- `InventoryReservation`: order line, inventory position, quantity, state
  (`HELD`, `COMMITTED`, `RELEASED`), idempotency key, and timestamps.

Quantities use exact numeric/integer storage and checks preventing negatives.
The database enforces one active allocation per order and one reservation per
run/order-line/position.

### Optimization and analytics

- `PlanningRun`: scenario, snapshot, solver adapter/version, OSRM/map version,
  contract version, options, input/output hashes, timings, status, and errors.
- `IntegrationPayload`: bounded immutable request/response JSON for audit,
  separate from normalized domain data.
- `Route`: run, vehicle, totals, cost, and canonical geometry.
- `RouteStop`: route sequence, order, arrival/service/departure, incremental and
  cumulative measures, load after service, and window status.
- `UnassignedOrder`: order, stage, reason code, certainty, evidence, and detail.
- `RunKpi`: name, numeric value or null, unit, numerator/denominator, and formula
  version.
- `ManualBaseline`: imported order/vehicle sequence and evaluated route facts.

## Transactional inventory algorithm

Snapshotting is not a detached read followed by later reservation. One database
transaction performs the consistency-critical work:

1. Lock relevant `InventoryPosition` rows in stable center/SKU order with
   `SELECT ... FOR UPDATE` to avoid deadlocks.
2. Recompute `available = on_hand - reserved - safety_stock` from locked rows.
3. Create the immutable snapshot and lines.
4. Process orders in the deterministic allocation order.
5. For the selected center, insert idempotent `HELD` reservations and increment
   `InventoryPosition.reserved` atomically.
6. Persist allocation decisions and their scoring evidence.
7. Commit once; on any error, roll back snapshot, allocations, reservations, and
   counters together.

An idempotency key unique to `(scenario_id, operation, client_key)` makes a
network retry return the original outcome. A database check ensures:

```text
on_hand >= 0
reserved >= 0
safety_stock >= 0
reserved + safety_stock <= on_hand
```

Releasing a reservation locks the same positions, decrements `reserved`, marks
the ledger rows `RELEASED`, and commits atomically. A committed plan does not
silently release stock. v1 uses explicit accept/cancel transitions; automated
expiry is deferred until a real operational requirement exists.

## Run immutability and reproducibility

- Completed run inputs, options, normalized result facts, and integration
  payload hashes are immutable.
- A rerun creates a new `PlanningRun` and references its predecessor when
  applicable.
- The run records dependency versions, image references, map extract source
  date/checksum, allocation policy version, contract version, dataset seed, and
  timezone.
- KPIs are derived from stored route facts and tagged with a formula version so
  a calculation change does not rewrite history unnoticed.

## Important indexes

- GiST on center, order, and route geometries.
- Unique `(scenario_id, source_order_id)`, `(scenario_id, source_vehicle_id)`,
  and `(distribution_center_id, sku)` where appropriate.
- B-tree on scenario/run status and creation time, reservation state, order
  priority/window, and route stop `(route_id, sequence)`.
- Foreign keys are restrictive by default; scenarios use an explicit deletion
  workflow rather than broad cascading deletes.
