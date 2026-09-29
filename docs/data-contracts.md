# Input data contract v1

## Package rules

RouteOps accepts either five CSV files or one `.xlsx` workbook with worksheets
named `orders`, `order_lines`, `inventory`, `distribution_centers`, and
`vehicles`. Names are case-sensitive in v1. CSV filenames are exactly the
dataset name plus `.csv`. CSV is UTF-8 (a BOM is accepted),
comma-delimited, and includes one header row. `.xls` and macro-enabled Excel
files are rejected.

In the later publishing flow, imports will be staged and validated as a package.
No rows will become planning data unless every required dataset is present and
the package has zero `ERROR` issues. `WARNING` issues will not block publication.

Approved configurable safety defaults are 20 MiB per file, 100 MiB per package,
five worksheets, 200 columns, and 250,000 rows per dataset. The default XLSX
limits are 1,000,000 cells, 200 MiB total expanded ZIP size, 4 MiB workbook
metadata, 32 MiB shared strings, 200 ZIP entries, and 100:1 compression ratio
per entry. These limits
belong to import validation; the operational VROOM run limit is a later decision.
Uploaded formulas and external links are rejected; values are never executed.

Blank strings normalize to null before required-field validation. Unknown
columns are warnings and are not persisted. Duplicate headers and duplicate
source identifiers are errors.

## Common types

| Type | Representation | Rules |
|---|---|---|
| identifier | string, 1–100 chars | Trimmed; `[A-Za-z0-9._-]`; case-sensitive |
| coordinate | decimal | Latitude `[-90,90]`, longitude `[-180,180]`; finite |
| instant | ISO 8601 string | Offset required, e.g. `2026-10-05T08:00:00-03:00` |
| local time | `HH:MM[:SS]` | Interpreted in scenario timezone and planning date |
| integer quantity | integer | Non-negative unless a field says strictly positive |
| decimal quantity | decimal | Base-10, finite, non-negative; no locale separators |
| skills | string | `|`-separated slugs, trimmed and lowercased; unique after normalization; blank means empty set |
| money | decimal | Non-negative, scenario currency; max 4 decimal places |

Time intervals require `start < end`. Local center and vehicle intervals cannot
cross midnight; cross-midnight shifts require two planning dates or a later
contract revision. Delivery 2.1 does not claim that an order window fits one
planning day, because it has no scenario planning date or timezone.
The scenario timezone defaults to `America/Santiago`, but it is stored
explicitly. Delivery 2.1 validates explicit instant offsets and interval
ordering; comparison with a scenario timezone/planning date and daylight-saving
ambiguity checks for local operating times wait for scenario revisions.

## Orders

| Field | Type | Required | Validation |
|---|---|---:|---|
| `order_id` | identifier | yes | Unique in import package and published revision |
| `customer_reference` | string ≤ 200 | yes | Synthetic/non-sensitive label |
| `latitude` | coordinate | yes | Valid point |
| `longitude` | coordinate | yes | Valid point |
| `priority` | integer | yes | `0..100` |
| `time_window_start` | instant | yes | Before window end |
| `time_window_end` | instant | yes | After start; same planning horizon when a scenario revision exists |
| `service_minutes` | integer | yes | `1..1440` |
| `required_skills` | skills | no | Normalized lower-case slugs |

Invalid global coordinates are errors now. An optional operational area in a
scenario revision uses `MultiPolygon`/SRID 4326. Points on its boundary are
inside; points outside produce a warning, not an automatic correction. With no
area configured, no territorial validation runs.

## Order lines

| Field | Type | Required | Validation |
|---|---|---:|---|
| `order_id` | identifier | yes | Must reference an order |
| `sku` | identifier | yes | Unique with `order_id` |
| `quantity` | integer | yes | `> 0` |
| `unit_weight_kg` | decimal | yes | `>= 0`, max 6 decimal places |
| `unit_volume_m3` | decimal | yes | `>= 0`, max 9 decimal places |

An order requires at least one line. Total units, weight, and volume are checked
for numeric overflow before publication in delivery 2.3.

## Inventory

| Field | Type | Required | Validation |
|---|---|---:|---|
| `snapshot_at` | instant | yes | Same value for all rows in a package |
| `distribution_center_id` | identifier | yes | Must reference a center |
| `sku` | identifier | yes | Unique with center |
| `on_hand_quantity` | integer | yes | `>= 0` |
| `externally_reserved_quantity` | integer | yes | `>= 0`; imported external reservations |
| `safety_stock_quantity` | integer | yes | `>= 0` |

`externally_reserved_quantity + safety_stock_quantity <= on_hand_quantity` is
required. RouteOps reservations are a separate ledger created in later deliveries;
they are never imported into or released from this external quantity.
Missing center/SKU rows mean zero available stock; they are not synthesized.

## Distribution centers

| Field | Type | Required | Validation |
|---|---|---:|---|
| `distribution_center_id` | identifier | yes | Unique in import package and published revision |
| `name` | string ≤ 200 | yes | Non-blank |
| `latitude` | coordinate | yes | Valid point |
| `longitude` | coordinate | yes | Valid point |
| `operating_start` | local time | yes | Before operating end |
| `operating_end` | local time | yes | After operating start |

## Vehicles

| Field | Type | Required | Validation |
|---|---|---:|---|
| `vehicle_id` | identifier | yes | Unique in import package and published revision |
| `distribution_center_id` | identifier | yes | Must reference a center |
| `vehicle_type` | identifier | yes | Stable type label |
| `capacity_units` | integer | yes | `> 0` |
| `capacity_weight_kg` | decimal | yes | `> 0`, max 6 decimal places |
| `capacity_volume_m3` | decimal | yes | `> 0`, max 9 decimal places |
| `shift_start` | local time | yes | Within center hours |
| `shift_end` | local time | yes | After start, within center hours |
| `skills` | skills | no | Normalized lower-case slugs |
| `fixed_cost` | money | yes | `>= 0` |
| `cost_per_hour` | money | yes | `>= 0` |
| `cost_per_km` | money | yes | `>= 0` |

At least one compatible vehicle must exist at any center used by allocation.
All three capacities are mandatory in v1, including values that are not
operationally limiting; templates explain how to choose an explicit upper
bound.

## Unit normalization

External decimal units remain exact `Decimal` values in validation and
persistence. The optimization mapper converts to integer dimensions:

```text
units      -> integer units
weight_kg  -> integer grams (× 1,000)
volume_m3  -> integer cubic centimeters (× 1,000,000)
minutes    -> integer seconds (× 60)
km         -> integer meters (× 1,000)
money      -> integer cost units using 10,000 units per currency unit
```

Values that cannot be represented exactly at these scales are rejected; they
are never silently rounded. For example, weight `1.000001` kg is invalid while
`1.000000` kg is representable as 1,000 grams. VROOM arrays always use
`[units, grams, cm3]` in that order, but that mapping exists only inside the
VROOM adapter. The v1 cost
scale preserves the four accepted decimal places even for zero-decimal
currencies such as CLP. The result carries the scale so presentation and KPI
code can recover the exact decimal amount.

## Validation report contract

Each issue contains:

```json
{
  "severity": "ERROR",
  "code": "INTERVAL_INVALID",
  "dataset": "orders",
  "source": "orders.csv",
  "row": 14,
  "field": "time_window_end",
  "message": "The start must be before the end",
  "value_excerpt": "[value omitted; 25 characters]"
}
```

Stable codes are suitable for tests and UI grouping. Messages are explanatory
and may evolve. Reports include total/accepted/rejected row counts and are
downloadable without exposing server paths or stack traces.

For workbook issues, `source` is `workbook.xlsx:<sheet>`; original local paths
and arbitrary uploaded names are not reflected in reports. Value excerpts reveal
only a bounded character count, never the original cell content.
