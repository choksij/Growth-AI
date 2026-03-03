# Redpanda (local)

We use Redpanda as a Kafka-compatible event stream for local dev.

## Create topics
Run:
- `infra/stream/redpanda/create-topics.sh`

This will create all required topics used by GrowthPilot services:
- ad.events
- ad.events.normalized
- kpi.updates
- alerts.raised
- actions.proposed
- actions.applied
- policy.updated
- decisions.audit