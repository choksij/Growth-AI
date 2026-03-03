-- infra/postgres/init.sql
-- Phase 0 init: extensions, schema, and basic defaults.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Keep everything under one logical schema
CREATE SCHEMA IF NOT EXISTS growthpilot;

-- Optional: set a default search_path for this DB (dev-friendly)
-- NOTE: this affects only the current DB.
ALTER DATABASE growthpilot SET search_path TO growthpilot, public;

-- Optional: sanity defaults for development
-- (You can adjust later; not required)
-- ALTER DATABASE growthpilot SET statement_timeout TO '30s';
-- ALTER DATABASE growthpilot SET idle_in_transaction_session_timeout TO '30s';