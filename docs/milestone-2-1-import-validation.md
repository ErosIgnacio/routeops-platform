# Milestone 2.1: import contract, templates, and validation

This delivery validates input in memory. It does not write to PostgreSQL or
create scenarios, snapshots, allocations, reservations, or planning runs.

## Generate and validate

With the backend development dependencies installed and `PYTHONPATH=backend/src`
(PowerShell: `$env:PYTHONPATH='backend/src'`):

```text
python -m routeops.application.import_cli templates ./routeops-templates
python -m routeops.application.import_cli validate ./input/orders.csv ./input/order_lines.csv ./input/inventory.csv ./input/distribution_centers.csv ./input/vehicles.csv
python -m routeops.application.import_cli validate ./input/routeops.xlsx
```

The generator writes five UTF-8 header-only CSV files and a deterministic XLSX
with exactly five header sheets. It never embeds sample business data. Exit code
0 means valid, 1 means validation errors, and 2 means a CLI, configuration, or
file-operation error. The JSON report is written to stdout.

## Contract

The authoritative field types and requirements are in [data-contracts.md](data-contracts.md).
Headers and sheet names are case-sensitive. The required CSV filenames and XLSX
sheet names are `orders`, `order_lines`, `inventory`, `distribution_centers`,
and `vehicles`; CSV filenames add `.csv`. Optional columns may be omitted.
Unknown columns produce `WARNING` and are ignored. Empty strings normalize to
null. An empty package with valid headers is valid as a template round trip.
Column order in an uploaded file is flexible; generated templates use the
documented order. Worksheet order is also flexible, while names must match.
Identifiers and values are trimmed; identifiers remain case-sensitive. Skill
slugs are trimmed and lowercased before duplicate checking. Header and sheet
names are matched exactly and are never normalized.

Synthetic example data rows, in the exact template column order:

```csv
# orders.csv
ORD-1,SYNTH-1,-33.44,-70.65,50,2026-10-15T09:00:00-03:00,2026-10-15T14:00:00-03:00,5,cold
# order_lines.csv
ORD-1,SKU-1,2,1.25,0.001
# inventory.csv
2026-10-15T08:00:00-03:00,DC-1,SKU-1,10,2,1
# distribution_centers.csv
DC-1,SYNTH DC,-33.45,-70.66,08:00,18:00
# vehicles.csv
VEH-1,DC-1,van,100,1000,10,08:00,18:00,cold,100,10,1
```

In `inventory.csv`, the `2` is `externally_reserved_quantity`. RouteOps will
maintain its own reservation ledger in a later delivery and will never release
that imported external amount.

## Limits and safety

`ImportLimits` in the validator is the single source for limit defaults. Each
field can be overridden with `ROUTEOPS_IMPORT_<UPPERCASE_FIELD_NAME>`; for
example, `ROUTEOPS_IMPORT_MAX_ROWS_PER_DATASET=1000`. All values must be positive
integers. Defaults are 20 MiB per file, 100 MiB per package, 250,000 rows per
dataset, 200 columns, 4,096 characters per cell, 1,000,000 XLSX cells,
200 MiB expanded ZIP content, 4 MiB workbook metadata, 32 MiB shared strings,
200 ZIP entries, a 100:1
per-entry compression ratio, and 1,000 reported issues. The final report issue
is `REPORT_TRUNCATED` if more errors occur. Limits protect import validation;
the optimization workload limit is deferred to delivery 2.5.

The XLSX reader only parses workbook structure, shared strings, and the five
worksheets. It rejects formulas, external relationships, embedded binary parts,
invalid ZIP structures, XML DTD/entity declarations, and unsupported cell
types. It does not execute worksheet content or use Excel automation. Workbook
display names and local paths are replaced with `workbook.xlsx` in reports.
Value excerpts contain only a bounded character count.
The CLI reads in 64 KiB chunks and stops at the file/package byte limits.
CSV decoding is bounded by the 20 MiB file limit. XLSX ZIP metadata is checked
before any XML member is expanded; worksheet rows are parsed incrementally.
Only fields needed for cross-dataset checks are retained after row validation.

## Issue codes

| Category | Stable codes |
|---|---|
| Package and format | `PACKAGE_INCOMPLETE`, `PACKAGE_LIMIT`, `FILE_LIMIT`, `FILE_UNKNOWN`, `FORMAT_UNSUPPORTED`, `CSV_INVALID` |
| Workbook structure | `XLSX_INVALID`, `XLSX_SHEETS`, `XLSX_FORMULA`, `XLSX_EXTERNAL_LINK`, `XLSX_EXPANSION_LIMIT`, `XLSX_CELL_LIMIT` |
| Shape | `HEADER_MISSING`, `HEADER_DUPLICATE`, `HEADER_UNKNOWN`, `ROW_WIDTH`, `ROW_LIMIT`, `COLUMN_LIMIT`, `CELL_LENGTH_LIMIT`, `REPORT_TRUNCATED` |
| Field values | `VALUE_REQUIRED`, `IDENTIFIER_INVALID`, `TEXT_INVALID`, `SKILLS_INVALID`, `INTEGER_INVALID`, `QUANTITY_RANGE`, `DECIMAL_INVALID`, `DECIMAL_PRECISION`, `DECIMAL_SCALE`, `COORDINATE_RANGE`, `INSTANT_INVALID`, `TIME_INVALID` |
| Row and package semantics | `INTERVAL_INVALID`, `STOCK_INCONSISTENT`, `ID_DUPLICATE`, `RELATION_MISSING`, `ORDER_LINES_MISSING`, `SNAPSHOT_MISMATCH`, `SHIFT_OUTSIDE_HOURS` |

Each issue has `code`, `severity`, `dataset`, `source`, `row`, `field`, `message`,
and `value_excerpt`. `source` is a fixed CSV filename or `workbook.xlsx:<sheet>`.
Package-level fields may be null. Reports contain per-dataset total, accepted,
and rejected row counts. `ERROR` blocks validity; `WARNING` does not.

## Deferred rules

The scenario planning date, timezone, and optional Santiago/RM envelope are not
part of these five files. This delivery validates ISO instants with explicit
offsets, interval ordering, global coordinate ranges, and local time syntax.
Comparison to a persisted scenario's planning horizon and the envelope warning
will be added when scenario revisions are introduced. No route optimization or
stock assignment is performed here.
