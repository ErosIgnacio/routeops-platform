#!/usr/bin/env sh
set -eu

base_url="${1:-http://localhost:8000}"

curl --fail --silent "${base_url}/health/live" | jq -e '.status == "alive"' >/dev/null
curl --fail --silent "${base_url}/health/dependencies" | jq -e '.status == "ready"' >/dev/null

run=$(curl --fail --silent --request POST "${base_url}/api/v1/demo/runs" \
  --header 'Content-Type: application/json' \
  --data '{"solution_quality":"BALANCED"}')

printf '%s' "$run" | jq -e '
  (.status == "SUCCEEDED" or .status == "PARTIAL") and
  (.kpis.routes >= 1) and
  (.kpis.assigned_orders >= 1) and
  (.kpis.unassigned_orders >= 1) and
  ([.result.routes[].geometry | length] | add >= 2)
' >/dev/null

printf '%s' "$run" | jq -r '"RouteOps smoke passed: run=\(.run_id), routes=\(.kpis.routes), assigned=\(.kpis.assigned_orders), unassigned=\(.kpis.unassigned_orders)"'
