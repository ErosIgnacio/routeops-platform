# Portfolio demonstration: two reproducible operations

These are synthetic inputs on the reviewed Santiago network, not new operational
policies. They use the existing CSV/XLSX contracts and `SolverGateway`. Keep the
old demos, including `ORD-003` requiring 20 `SKU-C` units with only five available
per CD and `STOCK_NO_FULL_COVERAGE`, intact.

Start a [clean review environment](local-runbook.md). Use the exact project
arguments below, substituting only your owned identity/paths if needed.

```sh
docker compose --env-file .ci-work/routeops-review-demo/review.env -p routeops-review-demo -f docker-compose.yml -f infrastructure/review/compose.yml exec -T backend python -m routeops.infrastructure.data.portfolio_cases --model b2b --format csv --output /tmp/portfolio-b2b
docker compose --env-file .ci-work/routeops-review-demo/review.env -p routeops-review-demo -f docker-compose.yml -f infrastructure/review/compose.yml cp backend:/tmp/portfolio-b2b ../evidence/inputs-b2b
docker compose --env-file .ci-work/routeops-review-demo/review.env -p routeops-review-demo -f docker-compose.yml -f infrastructure/review/compose.yml exec -T backend python -m routeops.infrastructure.data.portfolio_cases --model b2c --format xlsx --output /tmp/portfolio-b2c
docker compose --env-file .ci-work/routeops-review-demo/review.env -p routeops-review-demo -f docker-compose.yml -f infrastructure/review/compose.yml cp backend:/tmp/portfolio-b2c ../evidence/inputs-b2c
```

Create the outside `../evidence` directory first. Generator directories must be
new/empty, so use another `/tmp` destination for a repeated generation. The
package contains `context.json` as instructions: upload **only the five CSV** or
the one XLSX, never that JSON. Both formats are supported for either model.

| Fact | B2B | B2C |
|---|---|---|
| Orders / lines / vehicles / CDs | 4 / 4 / 2 / 2 | 6 / 6 / 2 / 1 |
| Origin | Exclusive per-SKU CD stock, first two from CD1, last two from CD2 | One CD, two vans with three-unit and three-task limits |
| Representative constraints | 12/8-unit cold loads, 20 kg/unit, 30/20 min service, 09–12 windows; truck capacity 40 units/500 kg/2 m³ | Six distinct destinations, parcel skill, five-minute service, 09–13 windows, bounded vans |
| Expected result | Two closed routes covering four orders | Two closed routes, three deliveries per van, six covered orders |
| Stock | Per SKU: 200 on hand, 5 externally reserved, 5 safety | Same separate concepts; release never changes external stock |

All input points are within the locked bounding box. The executed report records
OSRM nearest distances and snapped points; the road-snap limit remains 250 m.
Orders use IDs such as `B2B-001`, `B2C-001`, and plain special text. No labels
change the solver: B2B/B2C differences arise from explicit fields.

## Eight-minute evaluator script

1. **Imports**: create a new B2B scenario, choose the five CSV. Configure
   **2026-10-15**, **08:00–18:00**, **−03:00**, **America/Santiago**, **CLP**.
   Read accepted/rejected counts, pending checks and incidences. Show `VALID`
   before publication and `PUBLISHED` afterward. Reload and retry publication:
   revision ID/number remains the same. Repeat with a new B2C scenario/XLSX.
2. **Planning**: select the scenario/revision, `BALANCED`, `alternatives-v2`.
   Inspect the two colored routes, each CD, delivery sequence, decision evidence,
   candidates, `READY` and `HELD` reservations. Reload/recover the saved key.
3. **Analytics**: select its run, read coverage, dimensional utilization, wait/
   driving/service, Decimal estimated operating cost and separate solver objective.
   Show diagnostic roles/certainty and explicitly unavailable historical facts.
4. **Comparisons**: supply the two manual routes below, preserving their reverse
   order. Compare manual, greedy-v1 and alternatives-v2 from one frozen context.
   Show coverage and departure convention before any delta. Manual starts at
   effective shift start; solver may delay departure. A difference can reflect
   sequence, coverage and waiting/departure; it is not all optimization savings.
5. Download run/comparison **XLSX and CSV ZIP**. XLSX identifiers are text; CSV
   must be imported as text to retain zeros. Export is read-only and does not
   execute spreadsheet formulas. Inspect schema/provenance and diagnostics.
6. Accept one READY run (`CONFIRMED`). Create a separate run and cancel it
   (`RELEASED`). Show automatic history and same-ID recovery. Never release an
   earlier demonstration's reservations.
7. Resize to mobile, inspect the same map, scroll to routes/analytics and check
   no page-wide overflow. Finish on the original dashboard, preserving ORD-003.

Manual sequences:

- B2B: `B2B-V1 / CD-B2B-1 / B2B-002,B2B-001`;
  `B2B-V2 / CD-B2B-2 / B2B-004,B2B-003`.
- B2C: `B2C-V1 / CD-B2C / B2C-003,B2C-002,B2C-001`;
  `B2C-V2 / CD-B2C / B2C-006,B2C-005,B2C-004`.

The deliberately fixed baseline is not a claim about a real dispatcher's route.
Comparison evaluation does not mutate stock; use a fresh scenario if testing
another availability assumption.

## Executable API counterpart

```sh
python scripts/review/tour.py --base-url http://127.0.0.1:18000 --model b2b --package ../evidence/inputs-b2b --output ../evidence/api-b2b
python scripts/review/tour.py --base-url http://127.0.0.1:18000 --model b2c --package ../evidence/inputs-b2c --output ../evidence/api-b2c
```

Each invocation creates a new isolated scenario, records every finished fact,
exercises same-key upload/run and publication retries, compares before planning,
and accepts/cancels distinct runs. Reports separate `READY` from terminal
lifecycle snapshots. `--verify-report` is read-only and checks exact historical
API facts plus export hashes after restart/restore. A failed invocation leaves
its identified test history for diagnosis; it does not alter unrelated runs.
These are functional checks, not new performance benchmarks.
