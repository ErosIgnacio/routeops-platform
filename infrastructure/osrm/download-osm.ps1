param(
    [string]$Destination = "data/osrm/santiago-demo.osm",
    [int64]$MaximumBytes = 52428800,
    [string[]]$OverpassEndpoints = @(
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter"
    )
)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$target = [System.IO.Path]::GetFullPath((Join-Path $workspace $Destination))
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $workspace "data/osrm"))

if (-not $target.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination must stay inside $allowedRoot"
}

$query = '[out:xml][timeout:120];(way["highway"](-33.4550,-70.6750,-33.4250,-70.6250);>;);out body qt;'
$temporary = "$target.download"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
$downloadedFrom = $null
$lastError = $null
foreach ($endpoint in $OverpassEndpoints) {
    $uri = "${endpoint}?data=$([uri]::EscapeDataString($query))"
    try {
        Invoke-WebRequest -Uri $uri -OutFile $temporary -Headers @{ "User-Agent" = "RouteOps-portfolio/0.1" }
        $downloadedFrom = $endpoint
        break
    }
    catch {
        $lastError = $_
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary
        }
    }
}
if ($null -eq $downloadedFrom) {
    throw "Every configured Overpass endpoint failed. Last error: $lastError"
}

$length = (Get-Item -LiteralPath $temporary).Length
if ($length -gt $MaximumBytes) {
    Remove-Item -LiteralPath $temporary
    throw "Downloaded extract is $length bytes, above the $MaximumBytes byte limit."
}

Move-Item -LiteralPath $temporary -Destination $target -Force
$hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
$metadata = [ordered]@{
    source = "OpenStreetMap via Overpass API"
    endpoint = $downloadedFrom
    query = $query
    downloaded_at_utc = [DateTime]::UtcNow.ToString("o")
    sha256 = $hash
    bytes = $length
    license = "ODbL-1.0"
}
$metadata | ConvertTo-Json | Set-Content -LiteralPath "$target.meta.json" -Encoding utf8
Write-Output "Downloaded $length bytes to $target (sha256:$hash)"
