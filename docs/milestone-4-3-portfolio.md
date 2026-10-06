# Delivery 4.3 — accepted reproduction and portfolio

**Technically accepted by the user; Milestone 4 closure and `v0.4.0` publication
authorized.** Approved candidate: `e2cfe4a2877442be9950d069f838199b1eff25ad`,
[successful CI 37393123462](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37393123462).
Developed on `feat/m4-3-portfolio-preparation`, from accepted 4.2 closure
`260faf4a0a0bd4e16578c899fd9825315f66e179` (approved functional
`fa02d7133bc2bb2f15027960f854eb16e27bcd37` is its ancestor).

## Scope

- [Installation and operations](local-runbook.md), [current API](api.md),
  [portfolio tour](portfolio-tour.md), [closure gates](milestone-4-closure-checklist.md).
- Explicit owned loopback-only Compose projects, private generated configuration,
  exact archived OSM hashes and existing locks/migrations.
- Quiesced SQL/private-original paired backup, empty-target restore, restart
  readback and scoped reset. The helper cannot target operational RouteOps.
- B2B: four orders, two trucks, two CDs, weight/cold/service restrictions.
  B2C: six distinct destinations, two vans, parcel windows and task caps.
  Contract 2.2, October 15, 2026, America/Santiago, CLP.
- Linux CI executes both bounded API tours and review safeguards, retaining
  sanitized report JSON without original files or private backups.
- Dependency license inventory and matching Node runtime notice retention.
  Versions, solver binary, car profile and map source are unchanged.

No capacity/worker/timeout changes, new benchmarks, authentication, public
deployment, custom solver or historical-record modifications.

## Executed reproduction and recovery

Fresh remote checkout `C:\Users\erosi\Desktop\routeops-m43-reproduction`
started without operational `.env`, private originals or OSRM indexes.
Windows Python 3.14.7 in isolated mode ran the standard-library scripts; Docker
Engine 29.8.1 / Compose 5.5.1 ran Linux containers. Owned projects:
`routeops-review-m43` (API18000/UI15173), `routeops-review-m43-restored`
(18001/15174). Both are separate from operational `routeops`.

The packed OSM hash `4c59be7a6e262191a6ee59ca190e93b395c37b029683308bf869c97a43fc9f21`
and expanded source hash
`22230c1c093a0fe50bbb240cce3d2c36aaf34e8bfb2fe136b76405084de213c4`
(4,145,215 bytes) passed before pinned car/MLD extract/partition/customize.
Migrations/readiness completed. GitHub Actions separately exercises Linux clean
checkouts. WSL2 shell instructions were reviewed; the machine has only Docker's
managed distro, so a separate user WSL distro was not executed.

Backup stopped all six writers and retained a coherent pair outside Git/public
evidence: PostgreSQL custom dump 270,379 bytes, SHA-256
`a8dee69c8a3d1ac945a1aba8eca9f960e96c6e2e8abe12984d89a1a733370f44`;
private originals tar 30,720 bytes, SHA-256
`42b358312cf4455ee72ed846d3b0c8c4ea90fc915109aec6419761a3259166a6`.
Restore checked hashes, empty target database/storage and safe archive members.
After restore and again after restarting, each model's nine API resources and
four export hashes matched exactly: revision/provenance/import, both terminal
runs/reservations, metrics/diagnostics, comparison/history. Six restored originals
were read through `ObjectStorageGateway` and matched ORM sizes/SHA-256.
Restoration across SQL and files is not atomic: reset/recreate only an owned
interrupted target before retrying the verified pair. No operational backup/reset
was attempted. The tested pair precedes the additional browser-created scenarios.

## Evidence and methods

Preserved evidence of the accepted candidate:
`C:\Users\erosi\Desktop\routeops-visual-review-v0.4.0`.
`manifest.json`, `report.md`, independent-reader report and `SHA256SUMS.txt`
relate files/resources/methods. One selected synthetic capture is versioned;
previous evidence folders remain intact.

The local closure review independently compared all 65 files against the
manifest, verified all 64 SHA-256 entries (the inventory excludes itself),
decoded every image and inspected the legibility of all 18 original captures.
No discrepancy or missing file was found. This checks the saved evidence, not
a new browser execution or an independent remote visual review. The accepted
candidate bundle remains unchanged, including its historical candidate status.
Reproduction now uses the `v0.4.0` tag, so removing integrated branches does not
break the runbook.

| Check | Result and provenance |
|---|---|
| Five CSV / XLSX | API and browser: counts 4/6, VALID then revision 1 PUBLISHED, two complete routes |
| Acceptance/cancel | API: independent runs for each model; UI: B2B ACCEPTED/four CONFIRMED, B2C CANCELED/six RELEASED, automatic history |
| Recovery/idempotency | API repeated upload/run/publication/terminal keys; browser same run after reload and B2B same revision on retry |
| KPIs/diagnostics | API/browser coverage 1, zero exclusions, cost/objective distinct; missing historical departure policy remains LEGACY_NOT_RECORDED |
| Comparison | API both models; browser creates B2C manual comparison, inspects B2B API-created history; full coverage/two vehicles and declared departure/wait differences |
| Exports | Four API exports/model; actual browser B2C run XLSX/comparison CSV ZIP and B2B comparison XLSX; separate independent reader |
| Map | Desktop B2B/B2C, mobile 390×844 B2C; centers, deliveries and OSM attribution |
| Original | Explicit original optimization preserves two routes/four deliveries and ORD-003 stock exception |

The first browser CSV attempt retained October 5 because the tool's fill did
not commit the native segmented date control. Validation correctly rejected four
`WINDOW_OUTSIDE_HORIZON` issues and disabled publication. The capture is kept;
a new load with October 15 visibly verified passed. This is a tool-input
limitation, not an application validation defect.

## Validation and known limits

Directed generator tests: four passed. Review safeguards: four passed.
Strict mypy: 75 source files. Ruff/format share the backend configuration.
The new nested report-sanitization test passed. The existing Linux-only readonly
report replacement contract fails locally on Windows with WinError5; it was not
suppressed or counted as locally approved. CI runs every contract.

CI `37389808213` initially failed because review-script formatting used a different
config; fixed with a backend-extending Ruff config. CI `37390167917` passed.
CI `37391351227` passed 264 backend unit/36 frontend tests; first integration
attempt failed in Docker BuildKit (`rpc … Unavailable … EOF`) before tests.
Only its failed job was retried without weakening checks and passed: 121
integrations, real smoke, B2B and B2C tours. Total backend: 385. Nine CI-tool,
four review safeguard and three Node boundary contracts also passed. The
accepted `e2cfe4a` CI passed all three jobs; recovered JUnit counts and real
smoke/tour reports are in the final manifest. Documentary closure changes no
functional code. Branch and main pushes each run quality CI automatically;
successful runs on the final closure commit and direct remote-reference checks
are required before tagging, as specified in the closure checklist. Previous
local suites and benchmark samples are preserved without routine repetition.

4.2 residual advisories and trusted local single-user scope remain explicit.
Historical benchmarks precede the runtime mitigations; no new capacity claims
are made. Vite bundle warnings, no global optimality guarantee, and operational
departure metadata not persisted remain known limitations. Comparisons do record
their departure policy. PostgreSQL/filesystem restore requires interruption
handling rather than pretending to be atomic.

Only owned review projects are retired after evidence is saved. The rejected
4.1 container/image/OSM cleanup is not retried. Operational services, originals,
volumes, historical scenarios and all prior tags remain intact.
