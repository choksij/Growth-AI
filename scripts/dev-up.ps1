# scripts/dev-up.ps1
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

if (!(Test-Path ".env")) {
  Write-Host "No .env found. Copying from .env.example..."
  Copy-Item ".env.example" ".env"
}

Import-DotEnv ".env"

Write-Host "Starting infra (postgres, redpanda, lightdash)..."
docker compose up -d

Write-Host "Waiting briefly for containers..."
Start-Sleep -Seconds 3

$pgUser = $env:POSTGRES_USER; if (!$pgUser) { $pgUser = "growthpilot" }
$pgDb   = $env:POSTGRES_DB;   if (!$pgDb)   { $pgDb   = "growthpilot" }

Write-Host "Initializing Postgres with infra/postgres/init.sql..."
Get-Content "infra/postgres/init.sql" | docker exec -i growthpilot-postgres psql -U $pgUser -d $pgDb

Write-Host "Creating Redpanda topics..."
.\infra\stream\redpanda\create-topics.ps1

$ldPort = $env:LIGHTDASH_PORT; if (!$ldPort) { $ldPort = "8080" }
Write-Host "`nLightdash: http://localhost:$ldPort"