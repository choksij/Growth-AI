import json
from pathlib import Path
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path("contracts/schemas")

def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)

def validate_schema(schema_name: str) -> None:
    schema = load_schema(schema_name)
    Draft202012Validator.check_schema(schema)

if __name__ == "__main__":
    schemas = [
        "envelope.schema.json",
        "ad_event.schema.json",
        "kpi_update.schema.json",
        "alert_raised.schema.json",
        "action_proposed.schema.json",
        "action_applied.schema.json",
        "policy_updated.schema.json",
    ]

    for s in schemas:
        validate_schema(s)
        print(f"OK: {s}")