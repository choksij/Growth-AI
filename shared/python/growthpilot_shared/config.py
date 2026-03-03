from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


@dataclass(frozen=True)
class SharedSettings:
    """
    Shared settings used across services.

    Keep this file dependency-light and side-effect free.
    """

    # App
    env: str = os.getenv("ENV", "dev")
    service_name: str = os.getenv("SERVICE_NAME", "unknown-service")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    json_logs: bool = _bool_env("JSON_LOGS", True)

    # Kafka / Redpanda
    redpanda_brokers: str = os.getenv("REDPANDA_BROKERS", "localhost:9092")

    # Postgres
    pg_user: str = os.getenv("POSTGRES_USER", "growthpilot")
    pg_pass: str = os.getenv("POSTGRES_PASSWORD", "growthpilot")
    pg_db: str = os.getenv("POSTGRES_DB", "growthpilot")
    pg_host: str = os.getenv("POSTGRES_HOST", "localhost")
    pg_port: str = os.getenv("POSTGRES_PORT", "5432")

    # Pooling (used by shared.db)
    pg_pool_min: int = _int_env("PG_POOL_MIN", 1)
    pg_pool_max: int = _int_env("PG_POOL_MAX", 5)
    pg_pool_timeout_s: int = _int_env("PG_POOL_TIMEOUT_S", 30)

    # Tracing (optional)
    ddtrace_enabled: bool = _bool_env("DDTRACE_ENABLED", False)
    ddtrace_version: str = os.getenv("DDTRACE_VERSION", "")

    @property
    def pg_conninfo(self) -> str:
        return f"postgresql://{self.pg_user}:{self.pg_pass}@{self.pg_host}:{self.pg_port}/{self.pg_db}"


def load_settings() -> SharedSettings:
    return SharedSettings()