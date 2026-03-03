# GrowthPilot KPI Service

Consumes ad events from Redpanda, builds per-minute campaign rollups, detects anomalies, and emits alerts.

## Endpoints
- GET /health
- GET /metrics

## Env vars
- REDPANDA_BROKERS (default: localhost:9092)
- KPI_INPUT_TOPIC (default: ad.events)
- KPI_GROUP_ID (default: growthpilot-kpi)
- KPI_ALERTS_TOPIC (default: alerts.raised)

- POSTGRES_* (same as other services)
- PG_POOL_MIN / PG_POOL_MAX / PG_POOL_TIMEOUT_S

- KPI_WINDOW_SECONDS (default: 60)
- KPI_HISTORY_MINUTES (default: 30)
- KPI_ZSCORE_THRESHOLD (default: 3.0)

## Notes
- Rollups are upserted per (workspace_id, campaign_id, window_start).
- Alerts are written to Postgres and optionally emitted to Kafka.