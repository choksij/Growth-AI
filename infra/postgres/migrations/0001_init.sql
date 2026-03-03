-- 0001_init.sql
-- Base schema + extensions + raw event storage + campaign state
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE SCHEMA IF NOT EXISTS growthpilot;

-- Keep objects in growthpilot schema by default for this session
SET search_path TO growthpilot, public;

-- Raw event store (append-only)
-- Stores normalized or raw events emitted by simulator/ingestion
CREATE TABLE IF NOT EXISTS ad_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- envelope
  schema_version text NOT NULL CHECK (schema_version ~ '^[0-9]+\.[0-9]+$'),
  event_id uuid NOT NULL UNIQUE,
  event_type text NOT NULL CHECK (event_type IN ('IMPRESSION','CLICK','CONVERSION','SPEND','REVENUE')),
  ts timestamptz NOT NULL,
  workspace_id text NOT NULL,
  source text NOT NULL,
  trace_id text NULL,

  -- payload fields (canonical)
  campaign_id text NOT NULL,
  adset_id text NULL,
  ad_id text NULL,
  segment_id text NULL,

  value double precision NOT NULL,
  currency char(3) NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- full event for replay/debugging
  raw_event jsonb NOT NULL,

  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_events_ws_ts ON ad_events (workspace_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ad_events_campaign_ts ON ad_events (campaign_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ad_events_type_ts ON ad_events (event_type, ts DESC);

-- Campaign "world state" for simulator/executor adapters
CREATE TABLE IF NOT EXISTS campaign_state (
  workspace_id text NOT NULL,
  campaign_id text NOT NULL,

  -- free-form state (budget allocations, paused ads, bid modifiers, etc.)
  state jsonb NOT NULL DEFAULT '{}'::jsonb,

  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, campaign_id)
);

COMMIT;