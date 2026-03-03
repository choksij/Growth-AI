Write-Host "=== Shutting Down GrowthPilot ===" -ForegroundColor Cyan
$ErrorActionPreference = "Stop"

docker compose down -v

Write-Host "Containers and volumes removed." -ForegroundColor Yellow
Write-Host "System reset complete." -ForegroundColor Green