# infra/stream/redpanda/create-topics.ps1
$ErrorActionPreference = "Stop"

$container = $env:REDPANDA_CONTAINER
if (!$container) { $container = "growthpilot-redpanda" }

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

Write-Host "Creating topics in Redpanda container: $container"

foreach ($t in $topics) {
  Write-Host " - ensuring topic exists: $t"
  # rpk returns non-zero if topic exists; swallow the error output
  docker exec $container rpk topic create $t --partitions 1 --replicas 1 *> $null
}

Write-Host "`nDone. Current topics:"
docker exec $container rpk topic list