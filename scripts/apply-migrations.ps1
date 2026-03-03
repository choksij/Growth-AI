# scripts/apply-migrations.ps1
$ErrorActionPreference = "Stop"

$pgUser = $env:POSTGRES_USER; if (!$pgUser) { $pgUser = "growthpilot" }
$pgDb   = $env:POSTGRES_DB;   if (!$pgDb)   { $pgDb   = "growthpilot" }

Write-Host "Applying migrations..." -ForegroundColor Cyan

Get-ChildItem -Path "infra/postgres/migrations" -Filter "*.sql" |
  Sort-Object Name |
  ForEach-Object {
    Write-Host " -> $($_.Name)"
    Get-Content $_.FullName | docker exec -i growthpilot-postgres psql -U $pgUser -d $pgDb
  }

Write-Host "Migrations applied ✅" -ForegroundColor Green