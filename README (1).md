# GrowthPilot AI

**An autonomous AI agent that manages digital ad campaigns, learns from outcomes, and improves its own decision-making over time — without human intervention.**

---

## What It Does

GrowthPilot watches your campaign KPIs in real time. When ROAS drops or CPA spikes, it doesn't alert a human and wait — it diagnoses the problem, selects the best action using learned probabilities, executes it, measures whether it worked, and updates its own policy. Then it briefs the CMO in plain spoken English.

The full loop runs end-to-end in under 3 minutes.

```
Ad Events → KPI Rollups → Alerts → AI Diagnosis → Action → Evaluation → Policy Update → Voice Briefing
     ↑____________________________________________________________________|
```

---

## Architecture

GrowthPilot is an event-driven microservices system built on Kafka (Redpanda), Postgres, and Docker.

```
┌─────────────┐    ad.events      ┌─────────────┐   kpi.updates   ┌─────────────┐
│  Simulator  │ ──────────────►  │  Ingestion  │ ──────────────► │     KPI     │
│  Service    │                  │  Service    │                  │   Service   │
└─────────────┘                  └─────────────┘                  └──────┬──────┘
                                                                          │ alerts.raised
                                                                          ▼
┌─────────────┐  actions.applied  ┌─────────────┐ actions.proposed ┌─────────────┐
│  Evaluator  │ ◄──────────────  │  Executor   │ ◄──────────────  │Orchestrator │
│  Service    │                  │  Service    │                  │  + Airia    │
└──────┬──────┘                  └─────────────┘                  └─────────────┘
       │ policy.updated
       ▼
  Bandit Update + Voice Briefing (ElevenLabs) + Datadog Metrics
```

### Services

| Service | Port | Responsibility |
|---------|------|----------------|
| Simulator | 8002 | Generates synthetic ad events with configurable scenarios |
| Ingestion | 8001 | Normalizes raw events, publishes to Kafka |
| KPI | 8003 | Rolls up events into ROAS/CPA metrics, raises alerts |
| Orchestrator | 8004 | Receives alerts, calls Airia (LLM), runs Thompson Sampling |
| Executor | 8005 | Applies guardrails, executes actions, writes to DB |
| Evaluator | 8006 | Measures outcomes, updates bandit policy, generates voice briefing |

---

## How the Agent Learns — Thompson Sampling

GrowthPilot uses a **multi-armed bandit** approach. Each action type (`REALLOC_BUDGET`, `ADJUST_BID`, `PAUSE_CREATIVE`, `REFRESH_COPY`) per campaign bucket maintains a Beta distribution (α, β).

- **Success** → α + 1 (action gets selected more often going forward)
- **Failure** → β + 1 (action gets selected less often going forward)

Over time, the agent learns which actions work best for which campaign types — without being explicitly programmed with rules.

---

## Reward Model

After every evaluation window (2 minutes), the agent computes:

```
score_S = w1 * ΔROAS_normalized + w2 * ΔCPA_normalized - w3 * volatility
```

Where:
- `ΔROAS_normalized` = percentage change in ROAS vs baseline
- `ΔCPA_normalized` = percentage change in CPA vs baseline
- `volatility` = standard deviation of recent ROAS values
- Weights: `w1=0.6, w2=0.3, w3=0.1`

`score_S >= 0.05` → success → α + 1
`score_S < 0.05` → failure → β + 1

---

## Integrations

### Airia — AI Decision Engine
The orchestrator calls **Airia** (Claude Haiku 4.5) to diagnose each alert and recommend an action. The LLM outputs structured JSON:

```json
{
  "diagnosis": "ROAS dropped due to increased CPC without proportional conversion improvement",
  "recommended_action": "REALLOC_BUDGET",
  "confidence": 0.82,
  "reasoning": "Budget reallocation to higher-converting segments likely to restore ROAS"
}
```

Airia's confidence score is tracked in Datadog and informs how aggressively the bandit explores vs exploits.

### ElevenLabs — Voice CMO Briefing
After every evaluation, the evaluator generates a natural language summary and converts it to speech using **ElevenLabs TTS** (Rachel voice, `eleven_turbo_v2`):

> *"GrowthPilot agent briefing. Alert on campaign 003: ROAS dropped below baseline. The agent autonomously chose to reallocate budget. The action succeeded. ROAS improved by 9 percent, and cost per acquisition improved by 59 percent. Reward score: 0.10. The agent is learning. This action has a 48 percent win rate across 61 trials, and will be selected more frequently going forward."*

Available at:
- `GET /briefing/latest` — MP3 audio stream
- `GET /briefing/text` — JSON transcript

### Datadog — Observability
All agent metrics stream to **Datadog** in real time:

| Metric | Description |
|--------|-------------|
| `growthpilot.actions.proposed` | Action volume by type and campaign |
| `growthpilot.reward.score_s` | Reward scores over time |
| `growthpilot.reward.delta_roas` | ROAS delta per action |
| `growthpilot.bandit.win_rate` | Thompson Sampling win rates per bucket |
| `growthpilot.evaluations.completed` | Evaluation throughput |
| `growthpilot.airia.confidence` | LLM confidence distribution |
| `growthpilot.policy.updated` | Policy update events |

### Command Center Dashboard
A standalone HTML dashboard (`growthpilot-command-center.html`) connecting live to the evaluator API:

- Real-time KPI strip
- Agent decision feed with outcomes and reward scores
- Voice briefing player with animated waveform
- Bandit learning win rates updating in real time
- Pending evaluation countdown timers

---

## Project Structure

```
growthpilot-ai/
├── contracts/                     # Shared Pydantic v2 event contracts
│   └── growthpilot_contracts/
│       ├── actions.py
│       ├── alerts.py
│       ├── envelope.py
│       └── policy.py              # Reward, BanditUpdate, PolicyUpdatedPayload
├── shared/                        # Shared utilities
│   └── growthpilot_shared/
│       ├── db.py
│       ├── stream.py              # Kafka consumer/producer wrappers
│       ├── logging.py
│       ├── config.py
│       └── datadog.py
├── services/
│   ├── simulator-service/         # Synthetic ad event generator + scenario control
│   ├── ingestion-service/         # Event normalization pipeline
│   ├── kpi-service/               # KPI rollups + ROAS/CPA alert detection
│   ├── orchestrator-service/      # Alert handling + Airia LLM + Thompson Sampling
│   ├── executor-service/          # Guardrails + action execution
│   └── evaluator-service/
│       └── app/
│           ├── main.py            # FastAPI app + evaluation loop + voice endpoint
│           ├── voice.py           # ElevenLabs TTS client + briefing builder
│           ├── reward.py          # Reward model (score_S computation)
│           ├── bandit.py          # Thompson Sampling update logic
│           ├── scheduler.py       # Evaluation window scheduler
│           ├── bucketing.py       # Campaign bucket key extraction
│           └── repository.py      # Postgres queries
├── scripts/
│   ├── seed-demo.ps1              # Full pipeline seed + scenario activation
│   └── smoke-test.ps1             # Pipeline health verification
├── growthpilot-command-center.html  # Live frontend dashboard
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Quick Start

### Prerequisites
- Docker Desktop
- Python 3.11+ (for serving the dashboard locally)
- API keys: Anthropic (Airia), ElevenLabs, Datadog

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/growthpilot-ai.git
cd growthpilot-ai
cp .env.example .env
```

Edit `.env`:
```env
ANTHROPIC_API_KEY=sk-ant-...
ELEVENLABS_API_KEY=sk_...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
ELEVENLABS_MODEL=eleven_turbo_v2
DD_API_KEY=...
DD_APP_KEY=...
BRIEFING_DIR=/tmp/briefings
```

### 2. Start all services

```bash
docker compose up -d
```

### 3. Seed the pipeline

```powershell
.\scripts\seed-demo.ps1
```

### 4. Open the dashboard

```bash
# From the project root
python -m http.server 3000
```

Then open: `http://localhost:3000/growthpilot-command-center.html`

### 5. Wait 2 minutes

After the first evaluation window, the agent will have made decisions, measured outcomes, and updated its policy. The voice briefing will auto-play.

---

## Guardrails

The executor enforces safety limits before any action executes:

- Max **5 actions per campaign per hour**
- Idempotency enforcement (no duplicate actions within the same minute window)
- All guardrail blocks are logged and visible in Datadog

---

## API Reference

Evaluator service (`localhost:8006`):

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Service health |
| `GET /metrics` | Evaluation counters |
| `GET /pending` | Pending evaluations with countdown timers |
| `GET /briefing/text` | Latest briefing as JSON |
| `GET /briefing/latest` | Latest briefing as MP3 |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| API Framework | FastAPI + Uvicorn |
| Message Broker | Redpanda (Kafka-compatible) |
| Database | PostgreSQL |
| Containerization | Docker Compose |
| AI / LLM | Airia (Claude Haiku 4.5) |
| Voice | ElevenLabs TTS |
| Observability | Datadog |
| Event Contracts | Pydantic v2 |
| Async | asyncio + aiokafka |

---

## Roadmap

- [ ] Google Ads API integration — replace simulator with real campaign data
- [ ] Read-only observation mode — watch agent decisions before enabling execution
- [ ] Multi-workspace support — isolated bandit policies per advertiser
- [ ] Confidence threshold configuration — only execute when Airia confidence exceeds threshold
- [ ] Slack/email alerts for high-impact decisions

---

## License

MIT
