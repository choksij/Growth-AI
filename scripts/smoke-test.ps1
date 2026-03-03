# scripts/smoke-test.ps1
# GrowthPilot AI - Smoke Test
# Verifies all services are up, topics exist, and events are flowing.
Write-Host "=== GrowthPilot Smoke Test ===" -ForegroundColor Cyan
$ErrorActionPreference = "Stop"

$PASS = 0
$FAIL = 0
$WARN = 0

function Pass($msg) {
  Write-Host "  [PASS] $msg" -ForegroundColor Green
  $script:PASS++
}

function Fail($msg) {
  Write-Host "  [FAIL] $msg" -ForegroundColor Red
  $script:FAIL++
}

function Warn($msg) {
  Write-Host "  [WARN] $msg" -ForegroundColor Yellow
  $script:WARN++
}

function Section($title) {
  Write-Host "`n--- $title ---" -ForegroundColor Cyan
}

# ------------------------------------------------
# 1) Health endpoints
# ------------------------------------------------
Section "Service Health"

$services = @(
  @{ Name = "Simulator";  Url = "http://127.0.0.1:8002/health" },
  @{ Name = "Ingestion";  Url = "http://127.0.0.1:8001/health" },
  @{ Name = "KPI";        Url = "http://127.0.0.1:8003/health" }
)

foreach ($svc in $services) {
  try {
    $resp = curl.exe -s --max-time 5 $svc.Url
    if ($resp -match '"ok"') {
      Pass "$($svc.Name) is healthy"
    } else {
      Fail "$($svc.Name) returned unexpected: $resp"
    }
  } catch {
    Fail "$($svc.Name) unreachable at $($svc.Url)"
  }
}

# ------------------------------------------------
# 2) Redpanda
# ------------------------------------------------
Section "Redpanda"

Write-Host "  Waiting for Redpanda (up to 30s)..."
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
  $out = docker exec growthpilot-redpanda rpk cluster info --brokers redpanda:9092 2>&1
  if ($LASTEXITCODE -eq 0) { $ready = $true; break }
  Start-Sleep -Seconds 2
}

if ($ready) {
  Pass "Redpanda cluster is healthy"
} else {
  Fail "Redpanda not ready after 30s"
}

# ------------------------------------------------
# 3) Kafka topics
# ------------------------------------------------
Section "Kafka Topics"

$requiredTopics = @(
  "ad.events",
  "ad.events.normalized",
  "kpi.updates",
  "alerts.raised",
  "actions.proposed",
  "actions.applied",
  "policy.updated",
  "decisions.audit"
)

$topicList = docker exec growthpilot-redpanda rpk topic list --brokers redpanda:9092 2>&1 | Out-String

foreach ($topic in $requiredTopics) {
  if ($topicList -match [regex]::Escape($topic)) {
    Pass "Topic exists: $topic"
  } else {
    # auto-create missing topics
    docker exec growthpilot-redpanda rpk topic create $topic --partitions 1 --replicas 1 --brokers redpanda:9092 *> $null
    if ($LASTEXITCODE -eq 0) {
      Warn "Topic created (was missing): $topic"
    } else {
      Fail "Topic missing and could not be created: $topic"
    }
  }
}

# ------------------------------------------------
# 4) Postgres tables
# ------------------------------------------------
Section "Postgres Schema"

$tables = @(
  "ad_events",
  "campaign_state",
  "kpi_rollups",
  "alerts",
  "actions",
  "outcomes",
  "policy_bandit",
  "kpi_campaign_minute"
)

foreach ($tbl in $tables) {
  $q = "select count(*) from pg_tables where schemaname='growthpilot' and tablename='$tbl';"
  $out = docker exec growthpilot-postgres psql -U growthpilot -d growthpilot -t -c $q 2>&1
  $n = (($out | Out-String).Trim() -replace "[^\d]", "")
  if ($n -eq "1") {
    Pass "Table exists: growthpilot.$tbl"
  } else {
    Fail "Table MISSING: growthpilot.$tbl  (run .\scripts\reset-db.ps1)"
  }
}

# ------------------------------------------------
# 5) Event flow check
# ------------------------------------------------
Section "Event Flow"

# Check simulator is producing
try {
  $worldResp = curl.exe -s --max-time 5 "http://127.0.0.1:8002/world"
  if ($worldResp -match "campaigns") {
    Pass "Simulator /world returns campaign state"
  } else {
    Warn "Simulator /world returned unexpected: $worldResp"
  }
} catch {
  Warn "Could not reach simulator /world"
}

# Check KPI metrics
try {
  $metricsResp = curl.exe -s --max-time 5 "http://127.0.0.1:8003/metrics"
  $metrics = $metricsResp | ConvertFrom-Json

  if ($metrics.consumed -gt 0) {
    Pass "KPI service has consumed $($metrics.consumed) events"
  } else {
    Warn "KPI consumed=0. Wait 60s for first window flush, or check simulator logs."
  }

  if ($metrics.rollups_upserted -gt 0) {
    Pass "KPI rollups upserted: $($metrics.rollups_upserted)"
  } else {
    Warn "No rollups yet (expected after first 60s window)"
  }

  if ($metrics.alerts_raised -gt 0) {
    Pass "Alerts raised: $($metrics.alerts_raised)"
  } else {
    Warn "No alerts yet (need enough history for anomaly detection)"
  }
} catch {
  Warn "Could not parse KPI metrics"
}

# Check ad.events topic has messages
Write-Host "  Checking ad.events for live messages (5s)..."
$job = Start-Job -ScriptBlock {
  docker exec growthpilot-redpanda rpk topic consume ad.events -n 1 --brokers redpanda:9092 2>&1
} 
$done = Wait-Job $job -Timeout 8
if ($done) {
  $out = Receive-Job $job
  Remove-Job $job | Out-Null
  if ($out -match "campaign_id") {
    Pass "ad.events topic has live messages flowing"
  } else {
    Warn "ad.events topic exists but no messages yet"
  }
} else {
  Stop-Job $job | Out-Null
  Remove-Job $job | Out-Null
  Warn "ad.events consume timed out (simulator may still be starting)"
}

# ------------------------------------------------
# 6) Row counts
# ------------------------------------------------
Section "Row Counts"

$countQueries = @(
  @{ Label = "ad_events rows";          Query = "select count(*) from growthpilot.ad_events;" },
  @{ Label = "kpi_campaign_minute rows"; Query = "select count(*) from growthpilot.kpi_campaign_minute;" },
  @{ Label = "alerts rows";             Query = "select count(*) from growthpilot.alerts;" },
  @{ Label = "actions rows";            Query = "select count(*) from growthpilot.actions;" },
  @{ Label = "policy_bandit rows";      Query = "select count(*) from growthpilot.policy_bandit;" }
)

foreach ($q in $countQueries) {
  $out = docker exec growthpilot-postgres psql -U growthpilot -d growthpilot -t -c $q.Query 2>&1
  $n = (($out | Out-String).Trim() -replace "[^\d]", "")
  if (-not $n) { $n = "0" }
  if ([int]$n -gt 0) {
    Pass "$($q.Label): $n"
  } else {
    Warn "$($q.Label): 0 (may be OK early in lifecycle)"
  }
}

# ------------------------------------------------
# Summary
# ------------------------------------------------
Section "Summary"
Write-Host "  PASS: $PASS  WARN: $WARN  FAIL: $FAIL"

if ($FAIL -gt 0) {
  Write-Host "`n=== Smoke Test FAILED ($FAIL failures) ===" -ForegroundColor Red
  exit 1
} elseif ($WARN -gt 0) {
  Write-Host "`n=== Smoke Test PASSED with warnings ===" -ForegroundColor Yellow
} else {
  Write-Host "`n=== Smoke Test PASSED ===" -ForegroundColor Green
}