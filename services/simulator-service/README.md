# GrowthPilot Simulator Service

Produces synthetic ad events into Redpanda topic `ad.events`.

Purpose:
- Provide real-time signals for ingestion -> DB -> KPI -> alerts -> actions -> policy updates.
- Enable demo scenarios: ROAS drop, CPA spike, creative fatigue.

## Environment variables
- REDPANDA_BROKERS (docker: "redpanda:9092", local: "localhost:9092")
- SIM_WORKSPACE_ID (default: ws_demo)
- SIM_TOPIC (default: ad.events)
- SIM_RATE_EPS (events per second, default: 10)
- SIM_CAMPAIGNS (default: 3)
- SIM_SEED (optional)

Optional API:
- SIM_API_PORT (default: 8002)
- SIM_ENABLE_API (default: true)

## Endpoints (if enabled)
- GET /health
- POST /scenario/apply  { "name": "roas_drop", "duration_s": 120 }
- GET /world