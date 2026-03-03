import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = Path("contracts/schemas").resolve()
EX_DIR = Path("contracts/examples").resolve()

SCHEMA_MAP = {
    "ad_event.json": "ad_event.schema.json",
    "kpi_update.json": "kpi_update.schema.json",
    "alert_raised.json": "alert_raised.schema.json",
    "action_proposed.json": "action_proposed.schema.json",
    "action_applied.json": "action_applied.schema.json",
    "policy_updated.json": "policy_updated.schema.json",
}


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_registry() -> Registry:
    """
    Build a registry that can resolve:
    - absolute file:// URIs for schemas
    - $id URIs inside schemas
    - relative refs like "./envelope.schema.json"
    """
    registry = Registry()

    # Load every schema file in contracts/schemas and register it.
    for schema_path in SCHEMA_DIR.glob("*.json"):
        schema = load_json(schema_path)

        # Register under its file URI (so relative refs work)
        file_uri = schema_path.as_uri()
        registry = registry.with_resource(
            file_uri,
            Resource.from_contents(schema, default_specification=DRAFT202012),
        )

        # Also register under $id if present (nice-to-have)
        schema_id = schema.get("$id")
        if schema_id:
            registry = registry.with_resource(
                schema_id,
                Resource.from_contents(schema, default_specification=DRAFT202012),
            )

    return registry


def main() -> None:
    registry = build_registry()

    for ex_file, schema_file in SCHEMA_MAP.items():
        schema_path = (SCHEMA_DIR / schema_file)
        schema = load_json(schema_path)

        # IMPORTANT: validator must know what URI this schema "lives" at
        schema_uri = schema_path.as_uri()

        validator = Draft202012Validator(
            schema,
            registry=registry,
        )

        instance = load_json(EX_DIR / ex_file)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))

        if errors:
            print(f"\nFAILED: {ex_file} against {schema_file}")
            for err in errors:
                print(f"  - path={list(err.path)}: {err.message}")
            raise SystemExit(1)

        print(f"OK: {ex_file}")

    print("\nAll examples validated successfully.")


if __name__ == "__main__":
    main()