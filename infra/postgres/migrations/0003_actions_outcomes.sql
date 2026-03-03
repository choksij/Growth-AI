-- 0003_actions_outcomes.sql
-- Actions (proposed/applied), outcomes, and policy learning state
BEGIN;

SET search_path TO growthpilot, public;

-- Actions table stores BOTH proposed and applied details (single audit record)
CREATE TABLE IF NOT EXISTS actions (
  action_id uuid PRIMARY KEY,

  -- envelope fields from ACTION_PROPOSED (required)
  proposed_schema_version text NOT NULL CHECK (proposed_schema_version ~ '^[0-9]+\.[0-9]+$'),
  proposed_event_id uuid NOT NULL UNIQUE,
  proposed_ts timestamptz NOT NULL,
  workspace_id text NOT NULL,
  proposed_source text NOT NULL,
  trace_id text NULL,

  alert_id uuid NOT NULL,
  campaign_id text NOT NULL,

  action_type text NOT NULL CHECK (action_type IN ('PAUSE_CREATIVE','REALLOC_BUDGET','ADJUST_BID','REFRESH_COPY')),
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
  policy jsonb NOT NULL DEFAULT '{}'::jsonb,
  explainability jsonb NULL,

  idempotency_key text NOT NULL UNIQUE,

  -- applied info (filled later by executor)
  applied_event_id uuid NULL UNIQUE,
  applied_ts timestamptz NULL,
  applied_source text NULL,
  status text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED','APPLIED','REJECTED','FAILED')),
  applied_changes jsonb NULL,
  cooldown_until timestamptz NULL,
  error text NULL,

  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_actions_ws_proposed_ts ON actions (workspace_id, proposed_ts DESC);
CREATE INDEX IF NOT EXISTS idx_actions_campaign_proposed_ts ON actions (campaign_id, proposed_ts DESC);
CREATE INDEX IF NOT EXISTS idx_actions_status_ts ON actions (status, proposed_ts DESC);

-- Outcomes of applied actions (evaluator-service)
CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  action_id uuid NOT NULL REFERENCES actions(action_id) ON DELETE CASCADE,

  workspace_id text NOT NULL,
  campaign_id text NOT NULL,

  bucket_key text NOT NULL,
  action_type text NOT NULL CHECK (action_type IN ('PAUSE_CREATIVE','REALLOC_BUDGET','ADJUST_BID','REFRESH_COPY')),

  eval_started_at timestamptz NOT NULL,
  eval_ended_at timestamptz NOT NULL,
  eval_window_seconds integer NOT NULL CHECK (eval_window_seconds >= 0),

  roas_before double precision NOT NULL CHECK (roas_before >= 0),
  roas_after double precision NOT NULL CHECK (roas_after >= 0),
  cpa_before double precision NOT NULL CHECK (cpa_before >= 0),
  cpa_after double precision NOT NULL CHECK (cpa_after >= 0),

  score_s double precision NOT NULL,
  success boolean NOT NULL,

  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_ws_ts ON outcomes (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcomes_campaign_ts ON outcomes (campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcomes_bucket_action ON outcomes (bucket_key, action_type, created_at DESC);

-- Bandit policy state (current alpha/beta per bucket per action)
CREATE TABLE IF NOT EXISTS policy_bandit (
  workspace_id text NOT NULL,
  bucket_key text NOT NULL,
  action_type text NOT NULL CHECK (action_type IN ('PAUSE_CREATIVE','REALLOC_BUDGET','ADJUST_BID','REFRESH_COPY')),

  alpha integer NOT NULL CHECK (alpha >= 0),
  beta integer NOT NULL CHECK (beta >= 0),

  updated_at timestamptz NOT NULL DEFAULT now(),

  PRIMARY KEY (workspace_id, bucket_key, action_type)
);

-- Audit log for policy updates (policy.updated topic)
CREATE TABLE IF NOT EXISTS policy_updates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  schema_version text NOT NULL CHECK (schema_version ~ '^[0-9]+\.[0-9]+$'),
  event_id uuid NOT NULL UNIQUE,
  event_type text NOT NULL CHECK (event_type = 'POLICY_UPDATED'),
  ts timestamptz NOT NULL,
  workspace_id text NOT NULL,
  source text NOT NULL,
  trace_id text NULL,

  action_id uuid NOT NULL REFERENCES actions(action_id) ON DELETE CASCADE,
  bucket_key text NOT NULL,
  action_type text NOT NULL CHECK (action_type IN ('PAUSE_CREATIVE','REALLOC_BUDGET','ADJUST_BID','REFRESH_COPY')),
  success boolean NOT NULL,

  reward jsonb NOT NULL,
  bandit_update jsonb NOT NULL,

  raw_event jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_policy_updates_ws_ts ON policy_updates (workspace_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_policy_updates_bucket_action ON policy_updates (bucket_key, action_type, ts DESC);

COMMIT;