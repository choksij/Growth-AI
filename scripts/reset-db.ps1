# scripts/reset-db.ps1
$ErrorActionPreference = "Stop"

function Import-DotEnv($path) {
  if (!(Test-Path $path)) { return }
  Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    if ($line.Contains("=")) {
      $parts = $line.Split("=", 2)
      $key = $parts[0].Trim()
      $val = $parts[1].Trim()
      if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
        $val = $val.Substring(1, $val.Length - 2)
      }
      [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
    }
  }
}

# Ensure .env exists and load it into the current PowerShell process
if (!(Test-Path ".env")) {
  Write-Host "No .env found. Copying from .env.example..."
  Copy-Item ".env.example" ".env"
}
Import-DotEnv ".env"

Write-Host "Resetting local stack (including volumes)..."
docker compose down -v

Write-Host "Starting fresh stack..."
docker compose up -d

$pgUser = $env:POSTGRES_USER; if (!$pgUser) { $pgUser = "growthpilot" }
$pgDb   = $env:POSTGRES_DB;   if (!$pgDb)   { $pgDb   = "growthpilot" }

Write-Host "Waiting for Postgres to become ready..."
$ready = $false
for ($i=0; $i -lt 60; $i++) {
  docker exec growthpilot-postgres pg_isready -U $pgUser -d $pgDb *> $null
  if ($LASTEXITCODE -eq 0) {
    $ready = $true
    Write-Host "Postgres is ready."
    break
  }
  Start-Sleep -Seconds 1
}
if (-not $ready) {
  throw "Postgres did not become ready in time."
}

Write-Host "Running infra/postgres/init.sql..."
Get-Content "infra/postgres/init.sql" | docker exec -i growthpilot-postgres psql -U $pgUser -d $pgDb

Write-Host "Recreating Redpanda topics..."
$topics = @(
  "ad.events",
  "ad.events.normalized",
  "kpi.updates",
  "alerts.raised",
  "actions.proposed",
  "actions.applied",
  "policy.updated",
  "decisions.audit"
)

foreach ($t in $topics) {
  Write-Host " - ensuring topic exists: $t"
  # Topic create is idempotent-ish: ignore errors if it already exists
  docker exec growthpilot-redpanda rpk topic create $t --partitions 1 --replicas 1 *> $null
}

Write-Host "`nApplying DB migrations..."
if (!(Test-Path ".\scripts\apply-migrations.ps1")) {
  throw "Missing scripts/apply-migrations.ps1. Please create it before running reset-db.ps1."
}
.\scripts\apply-migrations.ps1

Write-Host "`nReset complete. Topics now:"
docker exec growthpilot-redpanda rpk topic list