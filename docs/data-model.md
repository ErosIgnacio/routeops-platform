# RouteOps persistence model

Delivery 2.2 adds a versioned, audit-ready persistence foundation. The physical
schema lives in Alembic revision `525a2c8f3a8c`, following `20260924_0001`.
Delivery 2.3 will implement provisional import and atomic publication; delivery
2.4 will add mutable operating inventory, allocations, and reservations.

## Scenario and time model

`scenarios` is a stable identity with an `ACTIVE` or `ARCHIVED` status and an
optimistic concurrency version. Every publication creates a new
`scenario_revisions` row with a monotonically increasing `revision_no` unique
within its scenario. Published revisions are immutable. The current revision is
the highest published number; older revisions are never marked or overwritten.
Business identifiers are case-sensitive and unique **per revision**, so the
same source identifier may occur in a later revision.

A revision freezes `planning_date`, IANA `timezone_iana`, currency, contract
version, package hash, and an absolute `[horizon_start_at, horizon_end_at)`
interval stored as `timestamptz`. Milestone 2 supports one operating day. Center
hours and vehicle shifts are local SQL `time` values, with start before end and
no overnight shift. A database check keeps the absolute half-open horizon
within `planning_date` in the revision's timezone. Publication in 2.3 will
reject nonexistent or ambiguous
local times caused by daylight-saving transitions and order windows outside the
revision horizon. These checks need the revision context and are not claimed by
the structure-only validation in 2.1.

`operational_area` is nullable `geometry(MultiPolygon, 4326)`. Without an area,
there is no territorial validation. With an area, 2.3 will use `ST_Covers` so
points exactly on its boundary are inside; outside orders or centers initially
receive a warning. Global coordinate range and finiteness remain required.

## Physical relationships

```text
scenarios ──< import_batches ──< import_files
                   │                └─ provenance of published rows
                   ├──< import_batch_events
                   └──< validation_issues
scenarios ──< scenario_revisions (one source import batch per revision)
                   ├──< distribution_centers ──< vehicles
                   ├──< orders ──< order_lines
                   ├──< inventory_snapshots ──< inventory_snapshot_lines
                   └──< planning_runs (nullable for milestone 1 demos)
```

| Entity | Identity and key constraints | Main persisted facts |
|---|---|---|
| `scenarios` | UUID PK | Name, status, version, creation and update audit |
| `scenario_revisions` | UUID PK; unique `(scenario_id, revision_no)` and import batch | Frozen date, timezone, horizon, currency, optional area, content hash, publication audit |
| `import_batches` | UUID PK; unique `(scenario_id, client_key)` when supplied | Current status, version, created/transitioned times, package hash, parser version |
| `import_files` | UUID PK; unique `(batch_id, dataset)`; unique opaque storage key | Dataset or `workbook`, sanitized display name, SHA-256, byte count, opaque key |
| `import_batch_events` | UUID PK; unique `(batch_id, sequence)` | Previous/next status, actor, reason, timestamp; append-only |
| `validation_issues` | UUID PK; unique `(batch_id, ordinal)` | Severity, stable code, dataset/sheet/row/field, bounded sanitized message |
| `distribution_centers` | UUID PK; unique `(revision_id, source_id)` | Point 4326, name, local operating hours and source provenance |
| `orders` | UUID PK; unique `(revision_id, source_id)` | Point 4326, priority, absolute window, service time, required skills, provenance |
| `order_lines` | UUID PK; unique `(order_id, sku)` | Positive `BIGINT` quantity, exact `NUMERIC` weight and volume, provenance |
| `vehicles` | UUID PK; unique `(revision_id, source_id)` | Same-revision home center, exact capacities/costs, local shift, skills, provenance |
| `inventory_snapshots` | UUID PK; at most one `IMPORTED` snapshot per revision | Snapshot instant, kind, content hash, creation audit |
| `inventory_snapshot_lines` | UUID PK; unique `(snapshot_id, distribution_center_id, sku)` | Imported stock, external reservations, safety stock, availability, provenance |

Imported rows have `scenario_revision_id`, `import_batch_id`,
`source_import_file_id`, `source_row_number`, and `source_row_sha256`. Composite
foreign keys ensure all references belong to the same revision and import
batch. An insert trigger checks that each row's file belongs to the expected
dataset or the shared workbook and that its row hash has SHA-256 form. Foreign
keys use restrictive deletion. There is no automatic deletion
of published planning data. B-tree indexes cover revision, business key,
window, status, and inventory lookup; GiST indexes cover points and area.

The five CSV files each have one `import_files` record. An XLSX workbook has
one record with dataset `workbook`, referenced by rows from all five sheets.
The database stores no file bytes or absolute local paths. The key is opaque;
an `ObjectStorageGateway` and private physical storage belong to 2.3.

## Quantities, geometry, and skills

Stock and order quantities use `BIGINT`; weights, volumes, and costs use exact
`NUMERIC`. Database checks reject negative, nonfinite, or over-precision
values. Mapping to VROOM integer dimensions and checking total overflow occur
before publication or execution; no persisted calculation uses binary float.
`externally_reserved_quantity` always denotes reservations imported from the
source system. Imported availability equals stock on hand minus external
reservations and safety stock. `routeops_reserved_quantity` is zero in the
initial imported snapshot. Later run snapshots may capture RouteOps reservations
separately; 2.4 will introduce mutable positions and a reservation ledger.

Skills are PostgreSQL `varchar(100)[]`, stored in sorted, unique, lower-case
slug order. The repository normalizes `|`-delimited input, and the database
function `routeops_valid_skills` rejects unsorted, repeated, uppercase, blank,
or malformed array entries. IDs use `C` collation for case-sensitive keys.
PostGIS checks constrain point SRID, validity, and longitude/latitude ranges;
the optional multipolygon area must be nonempty and valid.

## Immutability and compatibility

Database triggers reject `UPDATE` and `DELETE` on revisions, published rows,
snapshots, import file metadata, validation issues, and import event history.
Another trigger freezes a planning run's revision/snapshot links and prevents
deletion of a linked run. The `planning_runs` links are both nullable only for
the existing demo path; a new linked run must provide a matching pair. Existing
run JSON, optimized routes, unassigned orders, endpoints, and `SolverGateway`
remain unchanged.

The import batch itself keeps its current state and optimistic version for
efficient locking. `import_batch_events` is append-only audit history. The
repository inserts the initial `RECEIVED` event with the batch and records a
permitted transition, incremented version, transition time, and event in one
transaction. `VALID → PUBLISHED` is reserved for the 2.3 publication unit of
work, not exposed by the 2.2 repository. Database triggers freeze the batch's
identity and content metadata, constrain status transitions, and defer a check
until commit that the current state matches its latest event.

The downgrade to `20260924_0001` refuses to run if any 2.2 table has data or
any run references a revision or snapshot. On an empty 2.2 schema it removes
the new schema and leaves milestone 1 runs intact.

## Subsequent delivery rules

In 2.3, files remain provisional until every required dataset validates with
zero `ERROR` issues. A package with all five datasets empty may validate its
structure but cannot be published. A transaction will create a fresh revision,
all imported rows, and its single initial inventory snapshot, then update the
batch state and append its publication event. An idempotency key and content
hash will make retries return the existing revision without duplication.

A nonempty scenario that cannot be optimized will receive an explicit
diagnosis before any call to VROOM. Allocation, one-center order coverage,
operational stock, reservations, and compensation are part of 2.4. VROOM
continues to be reached exclusively through `SolverGateway`.
