# GrowthPilot Ingestion Service

Consumes raw ad events from Redpanda topic `ad.events`, validates them against
`growthpilot_contracts` (Pydantic), normalizes fields, stores them in Postgres
table `growthpilot.ad_events`, and (optionally) emits normalized events to
`ad.events.normalized`.

## Environment variables (uses repo .env)
- POSTGRES_USER
- POSTGRES_PASSWORD
- POSTGRES_DB
- POSTGRES_HOST (docker: "postgres")
- POSTGRES_PORT
- REDPANDA_BROKERS (docker: "redpanda:9092")

Optional:
- INGESTION_GROUP_ID (default: growthpilot-ingestion)
- INGESTION_INPUT_TOPIC (default: ad.events)
- INGESTION_OUTPUT_TOPIC (default: ad.events.normalized)
- INGESTION_EMIT_NORMALIZED (default: false)
- INGESTION_LOG_LEVEL (default: INFO)

## Local dev
Prefer running via docker compose. Once wired into compose:
- `docker compose up -d ingestion-service`
- check logs: `docker compose logs -f ingestion-service`

Health:
- GET http://localhost:8001/health
- GET http://localhost:8001/metrics (basic counters)