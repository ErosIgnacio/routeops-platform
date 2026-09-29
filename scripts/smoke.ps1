param(
    [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

$live = Invoke-RestMethod -Uri "$BaseUrl/health/live"
if ($live.status -ne "alive") {
    throw "Liveness check failed"
}

$dependencies = Invoke-RestMethod -Uri "$BaseUrl/health/dependencies"
if ($dependencies.status -ne "ready") {
    throw "Dependency readiness failed"
}

$body = @{ solution_quality = "BALANCED" } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/demo/runs" `
    -ContentType "application/json" -Body $body

if ($run.status -notin @("SUCCEEDED", "PARTIAL")) {
    throw "Unexpected run status: $($run.status)"
}
if ($run.kpis.routes -lt 1 -or $run.kpis.assigned_orders -lt 1) {
    throw "The smoke scenario did not produce an assigned route"
}
if ($run.kpis.unassigned_orders -lt 1) {
    throw "The known infeasible fixture was not reported"
}
$geometryPoints = @($run.result.routes | ForEach-Object { $_.geometry.Count } | Measure-Object -Sum).Sum
if ($geometryPoints -lt 2) {
    throw "No usable route geometry was returned"
}

Write-Output "RouteOps smoke passed: run=$($run.run_id), routes=$($run.kpis.routes), assigned=$($run.kpis.assigned_orders), unassigned=$($run.kpis.unassigned_orders)"
