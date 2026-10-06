# API and reviewer contracts

The authority is generated [OpenAPI](http://127.0.0.1:8000/docs) on a running
local installation (`/openapi.json` machine-readable); reviewer installations
use port 18000. Relative routes below work in either environment. This API has
no authentication and must remain local/single-user. Uploaded originals have
**no download endpoint**; exports contain persisted analytic facts.

| Operation | Contract |
|---|---|
| Health | GET `/health/live`, `/health/dependencies`, `/health/ready` |
| Scenarios | POST/GET `/api/v1/scenarios`; listing uses offset/limit |
| Templates | GET `/api/v1/import-templates/{dataset}` or `/workbook` |
| Receive originals | POST `/api/v1/scenarios/{id}/imports`, multipart `files`, exactly five named CSV or one workbook; `Idempotency-Key` required |
| Recover upload | GET scenario imports `/lookup?key=...` or `/{batch}`; paginated list |
| Validate | POST/GET `.../imports/{batch}/validation`; fixed context; issues at `/validation/issues?after=0&limit=100` |
| Publish | POST `.../imports/{batch}/publish`; repeat returns same revision; GET `/publication` |
| Revisions | GET `.../revisions` (after/limit) and `.../revisions/{number}` |
| Plan | POST `.../revisions/{number}/runs`, JSON `{}`, `Idempotency-Key`; GET `/runs/lookup?key=...` |
| Lifecycle/history | GET `/api/v1/revision-runs/{id}`, POST `/accept` or `/cancel`; GET scenario `/revision-runs` (offset/limit) |
| Metrics/diagnostics | GET `/api/v1/runs/{id}/metrics`, `/estimated-operating-cost`, `/diagnostics` (offset/limit, stage/code/certainty), `/analytics` |
| Manual input | GET scenario revision `/comparison-input`; POST `/comparisons` with `manual_routes` and `Idempotency-Key` |
| Comparison history | GET scenario `/comparisons` (offset/limit), GET `/api/v1/comparisons/{id}` |
| Read-only exports | GET run or comparison `/exports/csv` (ZIP of tables) or `/exports/xlsx` |

Validation context includes planning date, explicit-offset daily horizon, IANA
zone, currency, optional geographic rectangle and contract/validator versions.
Reusing a batch with different context conflicts. `VALID` is not `PUBLISHED`;
publication verifies originals, normalization/report hashes and immutable
revision constraints atomically. A completely empty package cannot publish.

Runs use `BALANCED` by default and `alternatives-v2`; optional choices are
documented in [optimization contracts](optimization-contract.md). Limits are
checked before reserving stock. `READY/HELD` transitions to
`ACCEPTED/CONFIRMED` or `CANCELED/RELEASED`, idempotently and fenced against
concurrent actions. `FAILED` infrastructure differs from unassigned orders.

Manual routes contain `vehicle_id`, `center_id`, ordered `order_ids`, and an
optional explicit `departure_at`. Default manual departure is effective shift
start; optimized departure is solver-chosen within the shift. Frozen comparison
alternatives share context/inventory and do not create operational reservations.
Comparability checks coverage/feasibility/departure, not just a distance delta.
No plan is claimed globally optimal; operational Decimal cost and integer solver
objective have distinct definitions. Historical unknown fields remain unknown.

The error envelope and statuses are defined per operation in OpenAPI and the
phase reports: conflicts (409), invalid requests (422), resource limits (413),
missing records (404), unavailable infrastructure (503). Store identities before
waiting for a response, consult status/lookup after loss, and do not blindly
retry creation with a new key. JSON errors omit sensitive values and physical
paths; logs still require private handling. Pagination cursors are operation-
specific: do not interchange `after` with `offset`.

Versioned field definitions: [data contracts](data-contracts.md),
[models/provenance](data-model.md), [metric/time definitions](milestone-3-1b-metrics.md),
[diagnostic certainty](milestone-3-1c-diagnostics.md),
[safe exports](milestone-3-3-analytics-exports.md).
