import json
from pathlib import Path

from growthpilot_contracts import AdEvent, KPIUpdate, AlertRaised, ActionProposed, ActionApplied, PolicyUpdated

EX = Path("contracts/examples")

def load(name: str):
    return json.loads((EX / name).read_text(encoding="utf-8"))

def main():
    AdEvent.model_validate(load("ad_event.json"))
    KPIUpdate.model_validate(load("kpi_update.json"))
    AlertRaised.model_validate(load("alert_raised.json"))
    ActionProposed.model_validate(load("action_proposed.json"))
    ActionApplied.model_validate(load("action_applied.json"))
    PolicyUpdated.model_validate(load("policy_updated.json"))
    print("Pydantic models validate all examples ✅")

if __name__ == "__main__":
    main()