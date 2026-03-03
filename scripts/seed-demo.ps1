# scripts/seed-demo.ps1
# GrowthPilot AI - Demo Seeder
# Triggers a ROAS drop scenario and waits for the pipeline to react.
Write-Host "=== GrowthPilot Demo Seeder ===" -ForegroundColor Cyan
$ErrorActionPreference = "Stop"

# ------------------------------------------------
# Helpers
# ------------------------------------------------

function Safe-Exec {
  param([string[]]$Cmd)
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & $Cmd[0] $Cmd[1..($Cmd.Length-1)] 2>&1
    return @{ Output = ($out | Out-String); ExitCode = $LASTEXITCODE }
  } finally {
    $ErrorActionPreference = $old
  }
}

function Wait-Postgres($timeoutSeconds = 60) {
  Write-Host "Waiting for Postgres (up to ${timeoutSeconds}s)..." -ForegroundColor Yellow
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $r = Safe-Exec @("docker","exec","growthpilot-postgres","pg_isready","-U","growthpilot","-d","growthpilot")
    if ($r.ExitCode -eq 0) { Write-Host "  Postgres ready." -ForegroundColor Green; return }
    Start-Sleep -Seconds 2
  }
  throw "Postgres not ready after ${timeoutSeconds}s"
}

function Wait-Redpanda($timeoutSeconds = 60) {
  Write-Host "Waiting for Redpanda (up to ${timeoutSeconds}s)..." -ForegroundColor Yellow
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $r = Safe-Exec @("docker","exec","growthpilot-redpanda","rpk","cluster","info","--brokers","redpanda:9092")
    if ($r.ExitCode -eq 0) { Write-Host "  Redpanda ready." -ForegroundColor Green; return }
    Start-Sleep -Seconds 2
  }
  throw "Redpanda not ready after ${timeoutSeconds}s"
}

function Wait-Service($name, $url, $timeoutSeconds = 60) {
  Write-Host "Waiting for $name at $url (up to ${timeoutSeconds}s)..." -ForegroundColor Yellow
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = curl.exe -s --max-time 3 $url
      if ($resp -match '"ok"') {
        Write-Host "  $name ready." -ForegroundColor Green
        return
      }
    } catch {}
    Start-Sleep -Seconds 3
  }
  throw "$name not ready after ${timeoutSeconds}s"
}

function Ensure-Topic($topic) {
  Write-Host "  Ensuring topic: $topic"
  $r = Safe-Exec @("docker","exec","growthpilot-redpanda","rpk","topic","create",$topic,"--partitions","1","--replicas","1","--brokers","redpanda:9092")
  if ($r.ExitCode -ne 0 -and $r.Output -notmatch "TOPIC_ALREADY_EXISTS") {
    Write-Host "  Warning: topic create returned: $($r.Output)" -ForegroundColor Yellow
  }
}

function Run-Migrations() {
  Write-Host "`nRunning migrations..." -ForegroundColor Cyan
  $migDir = "infra\postgres\migrations"
  if (-not (Test-Path $migDir)) {
    Write-Host "  Warning: $migDir not found, skipping migrations." -ForegroundColor Yellow
    return
  }
  # Suppress stderr so psql NOTICE messages don't trigger PowerShell's error handler
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  Get-ChildItem -Path $migDir -Filter "*.sql" | Sort-Object Name | ForEach-Object {
    Write-Host "  -> $($_.Name)"
    Get-Content $_.FullName | docker exec -i growthpilot-postgres psql -U growthpilot -d growthpilot 2>&1 | Where-Object { $_ -notmatch "^NOTICE:" } | Out-Null
  }
  $ErrorActionPreference = $old
  Write-Host "  Migrations applied." -ForegroundColor Green
}

function Assert-Tables() {
  Write-Host "`nVerifying critical tables exist..." -ForegroundColor Cyan
  $tables = @("ad_events","alerts","actions","outcomes","policy_bandit","kpi_campaign_minute")
  $missing = @()
  foreach ($t in $tables) {
    $q = "select count(*) from pg_tables where schemaname='growthpilot' and tablename='$t';"
    $out = docker exec growthpilot-postgres psql -U growthpilot -d growthpilot -t -c $q 2>&1
    $n = (($out | Out-String).Trim() -replace "[^\d]","")
    if ($n -ne "1") { $missing += $t }
  }
  if ($missing.Count -gt 0) {
    Write-Host "  Missing tables: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "  Running migrations to create them..."
    Run-Migrations
  } else {
    Write-Host "  All critical tables present." -ForegroundColor Green
  }
}

function Seed-PolicyBandit() {
  Write-Host "`nSeeding initial bandit policy (alpha=1, beta=1 for all buckets)..." -ForegroundColor Cyan

  $buckets = @(
    "objective=roas|severity=high|type=prospecting",
    "objective=roas|severity=medium|type=prospecting",
    "objective=cpa|severity=high|type=retargeting",
    "objective=cpa|severity=medium|type=retargeting"
  )
  $actions = @("PAUSE_CREATIVE","REALLOC_BUDGET","ADJUST_BID","REFRESH_COPY")

  foreach ($bucket in $buckets) {
    foreach ($action in $actions) {
      $sql = @"
INSERT INTO growthpilot.policy_bandit (workspace_id, bucket_key, action_type, alpha, beta, updated_at)
VALUES ('ws_demo', '$bucket', '$action', 1, 1, now())
ON CONFLICT (workspace_id, bucket_key, action_type) DO NOTHING;
"@
      $sql | docker exec -i growthpilot-postgres psql -U growthpilot -d growthpilot *> $null
    }
  }
  Write-Host "  Policy bandit seeded." -ForegroundColor Green
}

function Seed-CampaignState() {
  Write-Host "`nSeeding campaign state for demo campaigns..." -ForegroundColor Cyan

  foreach ($i in 1..3) {
    $cid = "cmp_{0:D3}" -f $i
    $state = '{"status":"active","daily_budget":1000,"budget_allocations":{"seg_1":0.5,"seg_2":0.5},"bid_strategy":{"type":"target_roas","value":2.0}}'
    $sql = @"
INSERT INTO growthpilot.campaign_state (workspace_id, campaign_id, state, updated_at)
VALUES ('ws_demo', '$cid', '$state'::jsonb, now())
ON CONFLICT (workspace_id, campaign_id) DO UPDATE SET state = EXCLUDED.state, updated_at = now();
"@
    $sql | docker exec -i growthpilot-postgres psql -U growthpilot -d growthpilot *> $null
  }
  Write-Host "  Campaign state seeded." -ForegroundColor Green
}

function Apply-Scenario($name, $duration_s) {
  Write-Host "`nApplying scenario: $name (duration=${duration_s}s)..." -ForegroundColor Cyan
  # Write body to a temp file to avoid PowerShell/curl.exe quoting issues on Windows
  $tmpFile = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($tmpFile, "{""name"":""$name"",""duration_s"":$duration_s}")
    $resp = curl.exe -s -X POST "http://127.0.0.1:8002/scenario/apply" `
      -H "Content-Type: application/json" `
      --data-binary "@$tmpFile"
    if ($resp -match '"ok"') {
      Write-Host "  Scenario '$name' applied." -ForegroundColor Green
      Write-Host "  Response: $resp"
    } else {
      Write-Host "  Warning: unexpected response: $resp" -ForegroundColor Yellow
    }
  } catch {
    Write-Host "  Warning: could not apply scenario: $_" -ForegroundColor Yellow
  } finally {
    Remove-Item $tmpFile -ErrorAction SilentlyContinue
  }
}

function Wait-For-Events($timeoutSeconds = 90) {
  Write-Host "`nWaiting for events to flow through pipeline (up to ${timeoutSeconds}s)..." -ForegroundColor Yellow
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  $consumed = 0

  while ((Get-Date) -lt $deadline) {
    try {
      $raw = curl.exe -s --max-time 3 "http://127.0.0.1:8003/metrics"
      $m = $raw | ConvertFrom-Json
      $consumed = [int]$m.consumed
      $rollups  = [int]$m.rollups_upserted
      $alerts   = [int]$m.alerts_raised

      Write-Host "  consumed=$consumed  rollups=$rollups  alerts=$alerts"

      if ($consumed -gt 0 -and $rollups -gt 0) {
        Write-Host "  Pipeline is flowing!" -ForegroundColor Green
        return
      }
    } catch {}
    Start-Sleep -Seconds 5
  }

  if ($consumed -eq 0) {
    Write-Host "  Warning: KPI service consumed 0 events after ${timeoutSeconds}s." -ForegroundColor Yellow
    Write-Host "  This is normal if the window hasn't flushed yet. Run smoke-test after 60s." -ForegroundColor Yellow
  }
}

function Print-Summary() {
  Write-Host "`n=== Pipeline Summary ===" -ForegroundColor Cyan

  Write-Host "`n[KPI Metrics]"
  curl.exe -s "http://127.0.0.1:8003/metrics"
  Write-Host ""

  Write-Host "`n[Simulator World State]"
  curl.exe -s "http://127.0.0.1:8002/world"
  Write-Host ""

  Write-Host "`n[Alert Summary (DB)]"
  docker exec growthpilot-postgres psql -U growthpilot -d growthpilot -c `
    "SELECT alert_type, severity, status, count(*) FROM growthpilot.alerts GROUP BY 1,2,3 ORDER BY 4 DESC;" 2>&1

  Write-Host "`n[Policy Bandit State]"
  docker exec growthpilot-postgres psql -U growthpilot -d growthpilot -c `
    "SELECT bucket_key, action_type, alpha, beta FROM growthpilot.policy_bandit ORDER BY bucket_key, action_type;" 2>&1

  Write-Host "`n[Recent ad.events from Kafka (last 3)]"
  $job = Start-Job -ScriptBlock {
    docker exec growthpilot-redpanda rpk topic consume ad.events -n 3 --brokers redpanda:9092 2>&1
  }
  $done = Wait-Job $job -Timeout 8
  if ($done) {
    Receive-Job $job | Select-Object -First 30
    Remove-Job $job | Out-Null
  } else {
    Stop-Job $job | Out-Null; Remove-Job $job | Out-Null
    Write-Host "  (no messages yet)"
  }
}

function Print-Diagnostics() {
  Write-Host "`n=== DIAGNOSTICS ===" -ForegroundColor Yellow

  Write-Host "`n[Docker status]"
  docker compose ps

  Write-Host "`n[Kafka topics]"
  docker exec growthpilot-redpanda rpk topic list --brokers redpanda:9092 2>&1

  foreach ($svc in @("growthpilot-simulator","growthpilot-ingestion","growthpilot-kpi")) {
    Write-Host "`n[Last 60 lines: $svc]" -ForegroundColor Yellow
    $r = Safe-Exec @("docker","logs","--tail","60",$svc)
    $r.Output | Out-Host
  }

  Write-Host "`n[ad.events sample (1 msg)]"
  docker exec growthpilot-redpanda rpk topic consume ad.events -n 1 --brokers redpanda:9092 2>&1 | Out-Host

  Write-Host "`n[Consumer groups]"
  docker exec growthpilot-redpanda rpk group list --brokers redpanda:9092 2>&1 | Out-Host
}

# ------------------------------------------------
# MAIN
# ------------------------------------------------

# 1) Wait for infra
Wait-Postgres 60
Wait-Redpanda 60

# 2) Ensure all topics exist
Write-Host "`nEnsuring Kafka topics..." -ForegroundColor Cyan
foreach ($t in @("ad.events","ad.events.normalized","kpi.updates","alerts.raised","actions.proposed","actions.applied","policy.updated","decisions.audit")) {
  Ensure-Topic $t
}

# 3) Ensure migrations have run (tables exist)
Assert-Tables

# 4) Seed reference data
Seed-PolicyBandit
Seed-CampaignState

# 5) Wait for services
Wait-Service "Simulator" "http://127.0.0.1:8002/health" 60
Wait-Service "Ingestion" "http://127.0.0.1:8001/health" 30
Wait-Service "KPI"       "http://127.0.0.1:8003/health" 30

# 6) Apply demo scenario
Apply-Scenario "roas_drop" 300

# 7) Wait for events to flow
Wait-For-Events 90

# 8) Print summary
Print-Summary

# 9) Diagnostics if nothing flowed
$raw2 = curl.exe -s --max-time 3 "http://127.0.0.1:8003/metrics" 2>&1
try {
  $m2 = $raw2 | ConvertFrom-Json
  if ([int]$m2.consumed -eq 0) { Print-Diagnostics }
} catch { Print-Diagnostics }

Write-Host "`n=== Demo Seed Complete ===" -ForegroundColor Cyan
Write-Host "Tip: Run .\scripts\smoke-test.ps1 after 60s to verify full pipeline." -ForegroundColor Yellow