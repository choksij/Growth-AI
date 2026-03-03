-- 0004_lightdash_views.sql
-- Lightdash-friendly views (stable surface area)
BEGIN;

SET search_path TO growthpilot, public;

-- 1) Campaign-minute KPIs (drives "Live Performance")
CREATE OR REPLACE VIEW ld_campaign_minute AS
SELECT
  date_trunc('minute', ts) AS minute_ts,
  workspace_id,
  campaign_id,
  window_size,
  impressions,
  clicks,
  conversions,
  spend,
  revenue,
  ctr,
  cvr,
  cpa,
  roas,
  pacing_ratio,
  anomaly_score
FROM kpi_rollups
WHERE level = 'campaign';

-- 2) Actions table (drives "Autopilot Actions")
CREATE OR REPLACE VIEW ld_actions AS
SELECT
  workspace_id,
  campaign_id,
  alert_id,
  action_id,
  action_type,
  idempotency_key,
  proposed_ts,
  status,
  applied_ts,
  cooldown_until,
  error,
  parameters,
  constraints,
  policy,
  explainability,
  applied_changes
FROM actions;

-- 3) Action outcomes join (drives "Learning & Self-Improvement")
CREATE OR REPLACE VIEW ld_action_outcomes AS
SELECT
  a.workspace_id,
  a.campaign_id,
  a.action_id,
  a.action_type,
  a.status,
  a.proposed_ts,
  a.applied_ts,
  o.eval_started_at,
  o.eval_ended_at,
  o.eval_window_seconds,
  o.success,
  o.score_s,
  o.roas_before,
  o.roas_after,
  (o.roas_after - o.roas_before) AS roas_delta,
  o.cpa_before,
  o.cpa_after,
  (o.cpa_before - o.cpa_after) AS cpa_improvement
FROM actions a
JOIN outcomes o ON o.action_id = a.action_id;

-- 4) Daily campaign health
CREATE OR REPLACE VIEW ld_campaign_health_daily AS
SELECT
  date_trunc('day', minute_ts) AS day_ts,
  workspace_id,
  campaign_id,
  AVG(roas) AS avg_roas,
  AVG(NULLIF(cpa, 0)) AS avg_cpa,
  AVG(anomaly_score) AS avg_anomaly_score,
  (AVG(roas) / NULLIF(AVG(NULLIF(cpa, 0)), 0)) AS health_score,
  SUM(spend) AS total_spend,
  SUM(revenue) AS total_revenue,
  SUM(conversions) AS total_conversions
FROM (
  SELECT
    date_trunc('minute', ts) AS minute_ts,
    workspace_id,
    campaign_id,
    roas,
    cpa,
    anomaly_score,
    spend,
    revenue,
    conversions
  FROM kpi_rollups
  WHERE level = 'campaign'
) t
GROUP BY 1, 2, 3;

-- 5) Current policy snapshot per bucket
CREATE OR REPLACE VIEW ld_policy_current AS
SELECT
  workspace_id,
  bucket_key,
  action_type,
  alpha,
  beta,
  updated_at
FROM policy_bandit;

COMMIT;