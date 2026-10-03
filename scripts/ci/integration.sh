#!/usr/bin/env bash
# Exclusively owns a generated routeops-ci-* project; never uses local .env.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p .ci-work .ci-artifacts
project="routeops-ci-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])')"
password=$(python -c 'import secrets; print(secrets.token_hex(24))')
if [[ ${GITHUB_ACTIONS:-} == true ]]; then printf '::add-mask::%s\n' "$password"; fi
umask 077
printf 'POSTGRES_DB=routeops_ci\nPOSTGRES_USER=routeops_ci\nPOSTGRES_PASSWORD=%s\nROUTEOPS_DATABASE_URL=postgresql+psycopg://routeops_ci:%s@database:5432/routeops_ci\n' "$password" "$password" > .ci-work/ci.env
unset ROUTEOPS_DATABASE_URL POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD ROUTEOPS_OSRM_URL ROUTEOPS_VROOM_URL
compose=(docker compose --env-file .ci-work/ci.env -p "$project" -f docker-compose.yml -f infrastructure/ci/compose.yml)
cleanup() {
  status=$?
  trap - EXIT
  # Logs are synthetic; redact the ephemeral credential before saving artifacts.
  "${compose[@]}" logs --no-color 2>&1 | sed "s/$password/[REDACTED]/g" > .ci-artifacts/compose.txt || true
  "${compose[@]}" ps -a > .ci-artifacts/services.txt || true
  ROUTEOPS_CI_SECRET="$password" python scripts/ci/sanitize_reports.py || status=1
  if ! "${compose[@]}" down --volumes --remove-orphans; then status=1; fi
  rm -f .ci-work/ci.env
  exit "$status"
}
trap cleanup EXIT
"${compose[@]}" config --quiet
python scripts/ci/prepare_map.py
"${compose[@]}" --profile tools run --rm osrm-prepare
"${compose[@]}" up -d --build
"${compose[@]}" --profile tools build checks
"${compose[@]}" --profile tools run --rm --no-deps checks python /scripts/ci/wait_ready.py
"${compose[@]}" --profile tools run --rm --no-deps checks
python scripts/ci/check_junit.py .ci-artifacts/integration.xml
# Reuse the application's existing smoke against this project's ephemeral port.
backend_address=$("${compose[@]}" port backend 8000)
bash scripts/smoke.sh "http://$backend_address" > .ci-artifacts/smoke.txt
