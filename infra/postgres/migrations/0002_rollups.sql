-- 0002_rollups.sql
-- KPI rollups + alerts raised
BEGIN;

SET search_path TO growthpilot, public;

-- KPI rollups (rolling windows; emitted as kpi.updates)
CREATE TABLE IF NOT EXISTS kpi_rollups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  schema_version text NOT NULL CHECK (schema_version ~ '^[0-9]+\.[0-9]+$'),
  event_id uuid NOT NULL UNIQUE,
  event_type text NOT NULL CHECK (event_type = 'KPI_UPDATE'),
  ts timestamptz NOT NULL,
  workspace_id text NOT NULL,
  source text NOT NULL,
  trace_id text NULL,

  level text NOT NULL CHECK (level IN ('campaign','adset','ad','segment')),
  campaign_id text NOT NULL,
  adset_id text NULL,
  ad_id text NULL,
  segment_id text NULL,

  window_size text NOT NULL CHECK (window_size IN ('1m','5m','15m','60m','1d')),

  impressions integer NOT NULL CHECK (impressions >= 0),
  clicks integer NOT NULL CHECK (clicks >= 0),
  conversions integer NOT NULL CHECK (conversions >= 0),
  spend double precision NOT NULL CHECK (spend >= 0),
  revenue double precision NOT NULL CHECK (revenue >= 0),

  ctr double precision NOT NULL CHECK (ctr >= 0),
  cvr double precision NOT NULL CHECK (cvr >= 0),
  cpa double precision NOT NULL CHECK (cpa >= 0),
  roas double precision NOT NULL CHECK (roas >= 0),

  pacing_ratio double precision NOT NULL CHECK (pacing_ratio >= 0),
  anomaly_score double precision NOT NULL CHECK (anomaly_score >= 0),

  baseline jsonb NULL,
  raw_event jsonb NOT NULL,

  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kpi_rollups_ws_ts
  ON kpi_rollups (workspace_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_kpi_rollups_campaign_ts
  ON kpi_rollups (campaign_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_kpi_rollups_level_window_ts
  ON kpi_rollups (level, window_size, ts DESC);

-- Alerts raised (alerts.raised)
CREATE TABLE IF NOT EXISTS alerts (
  alert_id uuid PRIMARY KEY,

  schema_version text NOT NULL CHECK (schema_version ~ '^[0-9]+\.[0-9]+$'),
  event_id uuid NOT NULL UNIQUE,
  event_type text NOT NULL CHECK (event_type = 'ALERT_RAISED'),
  ts timestamptz NOT NULL,
  workspace_id text NOT NULL,
  source text NOT NULL,
  trace_id text NULL,

  campaign_id text NOT NULL,

  severity text NOT NULL CHECK (severity IN ('low','medium','high')),
  alert_type text NOT NULL CHECK (alert_type IN ('ROAS_DROP','CPA_SPIKE','PACING_ANOMALY','CVR_DROP')),
  window_size text NOT NULL CHECK (window_size IN ('1m','5m','15m','60m')),

  baseline jsonb NOT NULL,
  current jsonb NOT NULL,

  anomaly_score double precision NOT NULL CHECK (anomaly_score >= 0),

  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,

  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKED','RESOLVED')),
  resolved_at timestamptz NULL,

  raw_event jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alerts_ws_ts
  ON alerts (workspace_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_campaign_ts
  ON alerts (campaign_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_status_severity
  ON alerts (status, severity, ts DESC);

COMMIT;