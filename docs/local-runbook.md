# Local installation and operations

RouteOps is a trusted local, single-user application. This is not a public
deployment recipe. Keep loopback bindings, private originals, existing limits,
and VROOM behind `SolverGateway`. Windows commands below use PowerShell;
Linux/WSL2 commands use a shell in the cloned repository.

## Requirements and exact map

- Linux containers, Docker Engine/Desktop and Compose >= 2.24.4 (`!override`).
  The pinned PostGIS image is `linux/amd64`. Windows: Docker Desktop's WSL2
  backend enabled; for a WSL shell, enable Docker integration for that distro.
- Python **3.14.7** for the standard-library review scripts; Node **24.21.0**
  only for host frontend checks. Container builds install the existing locks.
- Internet for official images/locked packages and low-volume OSM raster tiles.
  Routing uses the local prepared dataset, not public tile routing.
- Check `docker version`, `docker compose version`, `python --version` and
  `git status --short`. Do not change PowerShell execution policy.

Use the tracked gzip of the previously accepted Santiago source for exact
reproduction. `scripts/ci/prepare_map.py` checks both the packed hash
`4c59be7a6e262191a6ee59ca190e93b395c37b029683308bf869c97a43fc9f21`
and expanded source hash
`22230c1c093a0fe50bbb240cce3d2c36aaf34e8bfb2fe136b76405084de213c4`
(4,145,215 bytes), refusing to overwrite different data. Source/ODbL metadata
remain in `data/osrm/source-lock.json` and `infrastructure/ci/osm-archive.json`.
This is the same extract as CI; no new download/profile/dataset is substituted.
The download scripts are for deliberate refresh, not byte-identical reproduction.

## Existing local installation: preserve its history

Copy `.env.example` to `.env` only for a **new installation**. Edit its two blank
password/URL settings privately with the same chosen password, URL-encoded in
the database URL. Never overwrite an existing `.env`. Within Compose the
hosts are `database`, `osrm`, `vroom`; from a host process use loopback ports.

```powershell
# PowerShell, new installation only:
Copy-Item .env.example .env
# Edit .env privately, then restore the exact source:
python scripts/ci/prepare_map.py --destination data/osrm/santiago-demo.osm
docker compose config --quiet
docker compose --profile tools run --rm osrm-prepare
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

```sh
# Linux/WSL2, new installation only; edit .env privately before config/up:
umask 077
cp .env.example .env
python scripts/ci/prepare_map.py --destination data/osrm/santiago-demo.osm
docker compose config --quiet
docker compose --profile tools run --rm osrm-prepare
docker compose up -d --build
curl --fail http://127.0.0.1:8000/health/ready
bash scripts/smoke.sh http://127.0.0.1:8000
```

Backend startup runs `alembic upgrade head`; the other workers start after the
backend/database dependencies. `up -d` alone does not prove API readiness:
wait for `/health/ready` to return 200/`ready`. Use `/health/dependencies` for
PostGIS/OSRM/VROOM detail, `/health/live` for process liveness. Visit `/`,
`/imports`, `/planning`, `/analytics`, `/comparisons` on port 5173; API docs
are on port 8000 at `/docs` and `/openapi.json`.

## Clean, isolated reviewer installation

Clone the review branch into a fresh directory. The following Python commands
work in PowerShell and Linux/WSL2 without changing execution policy. They never
read the operational `.env`, named volumes, private originals or map indexes.
Each project must have a **new** `routeops-review-*` identity; preparation rejects
existing containers/volumes, and subsequent operations require its owner marker.

```sh
git clone --single-branch --branch feat/m4-3-portfolio-preparation https://github.com/ErosIgnacio/routeops-platform.git routeops-review
cd routeops-review
python scripts/review/environment.py prepare --project routeops-review-demo
python scripts/review/environment.py up --project routeops-review-demo
python scripts/review/environment.py status --project routeops-review-demo
```

`prepare` generates private hex credentials in ignored `.ci-work/<project>/`,
restores the checksum-verified map, and `up` runs car/MLD preprocessing, builds,
migrations and a bounded startup readiness check. API: **127.0.0.1:18000**;
UI: **127.0.0.1:15173**. Use `--api-port`/`--ui-port` on preparation for a second
project. Explicit review CORS matches its UI; database/OSRM/VROOM have no host
ports. Unset inherited `ROUTEOPS_*`, `POSTGRES_*`, `REVIEW_*` variables because
Compose shell variables override env files. On Windows, file modes do not
replace user ACLs: keep `.ci-work` in your private account directory.

## Stop, restart, update and diagnose

- `docker compose stop` or `down` preserves named volumes; `restart` does not
  rebuild images or run a new clone. A changed backend image must start with its
  startup migration command. Do not use `down --volumes` when keeping history.
- For an owned review project: `python scripts/review/environment.py stop
  --project <identity>`, `restart`, `status`; the same private config is reused.
- Before updating an installation, take a coherent database + original-object
  backup, note the Git SHA/image digests and Alembic head. Fetch/review changes,
  use fast-forward if appropriate, rebuild/start, then verify health and smoke.
  Do not downgrade a populated immutable schema as a rollback technique. Restore
  the matching backup/code in a separate environment if recovery is necessary.
- Use `docker compose ps -a`, scoped `logs --tail 100 backend planning-worker`
  and the job API/history. Do not print `docker compose config` with credentials;
  `config --quiet` checks it safely. Logs still require private handling.
- Failed readiness: check database health/migration errors, the exact source
  checksum and prepared MLD files, service-name URLs and VROOM `/health`.
  A window/stock error is not an OSRM outage. Do not increase limits/timeouts to
  hide a failure. See [API contracts](api.md) and [4.2 security](milestone-4-2-image-security-review.md).

## Durable work recovery

Validation, planning and comparison workers use PostgreSQL leases, heartbeats,
owner tokens and bounded retries. Start the appropriate worker and allow a
valid lease to finish or expire; do not edit rows/tokens or reset counters.
Late former owners cannot commit. Query the saved batch/run/comparison ID or
repeat the same operation identity/content. Changed content/context requires a
new identity. `READY` reservations **do not auto-expire**: explicitly accept or
cancel only the intended run. A worker restart is distinct from releasing
another run's reservations. Initial queue wait, attempts and unknown intervals
remain distinct from route times and browser polling.

## Backup and restore (checked with synthetic data)

PostgreSQL alone is insufficient: immutable `import_files` refer to originals
used for replay/provenance. Quiesce **all** writers (API, validation/planning/
comparison workers and maintenance); keep PostgreSQL running. Store one custom
`pg_dump` and the matching private-original volume archive, with hashes. The
backup is unencrypted: protect/encrypt it outside Git/public evidence. Do not
export `.env`, database credentials or SQL dumps to CI artifacts.

For the owned review project, the helper performs that quiescence, streams
binary data without PowerShell text redirection, hashes the pair and restarts
only writers that were previously running, including on backup failure:

```sh
python scripts/review/environment.py backup --project routeops-review-demo --backup-dir ../private-routeops-backup
python scripts/review/environment.py prepare --project routeops-review-restored --api-port 18001 --ui-port 15174
python scripts/review/environment.py restore --project routeops-review-restored --backup-dir ../private-routeops-backup --confirm-project routeops-review-restored
python scripts/review/environment.py up --project routeops-review-restored
python scripts/review/tour.py --base-url http://127.0.0.1:18001 --verify-report ../evidence/api-b2b/report.json
python scripts/review/tour.py --base-url http://127.0.0.1:18001 --verify-report ../evidence/api-b2c/report.json
```

Restore rejects a changed hash/size, the source project, a target with tables,
nonempty originals or missing exact confirmation. Tar members use the Python
data filter and reject links/traversal. Restoration is **not cross-resource
atomic**: on interruption, keep the source/backup intact and recreate only the
owned empty target before retrying. Do not run workers until both parts restore.
Retain the original schema/code/role versions. The helper intentionally refuses
the operational `routeops` project; backing up a real installation requires its
explicit private configuration and the same quiescence/paired archive procedure.
This delivery does not automatically back up or reset historical local data.
Official background: [PostgreSQL dumps](https://www.postgresql.org/docs/18/backup-dump.html)
and [Docker volumes](https://docs.docker.com/engine/storage/volumes/).

## Destructive demo reset is separate from service restart

After preserving reports, stop/remove only the owned synthetic project:

```sh
python scripts/review/environment.py reset --project routeops-review-demo --confirm-project routeops-review-demo
```

This explicitly removes that project's containers, network and two named
volumes (database/originals); it preserves images, source map, checkout, private
backup and all other projects. Owner/config markers remain so the identity
cannot silently adopt an unrelated installation. No `docker system prune`,
global volume cleanup, or reattempt of the blocked 4.1 cleanup is authorized.

Executed/reviewed distinctions and limitations are in
[the candidate report](milestone-4-3-portfolio.md).
