from __future__ import annotations

import contextvars
import logging
import sys
import time
from typing import Any, Dict, Optional

import orjson


_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)


def bind_trace_id(trace_id: Optional[str]) -> None:
    _trace_id_var.set(trace_id)


def get_trace_id() -> Optional[str]:
    return _trace_id_var.get()


class JsonFormatter(logging.Formatter):
    """
    Minimal structured JSON logs:
    - stable keys: ts, level, logger, service, msg
    - optional trace_id
    - exception info when present
    - attaches extra fields under "extra"
    """

    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        base: Dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "msg": record.getMessage(),
        }

        trace_id = get_trace_id()
        if trace_id:
            base["trace_id"] = trace_id

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)

        # Keep python logging internals out of "extra"
        skip = {
            "name", "msg", "args", "levelname", "levelno",
            "pathname", "filename", "module",
            "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs",
            "relativeCreated", "thread", "threadName",
            "processName", "process",
        }

        extras: Dict[str, Any] = {}
        for k, v in record.__dict__.items():
            if k.startswith("_") or k in skip:
                continue
            extras[k] = v

        if extras:
            base["extra"] = extras

        return orjson.dumps(base).decode("utf-8")


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = True,
    service_name: str = "unknown-service",
    force: bool = True,
) -> None:
    """
    Configure root logging once per process.

    force=True clears existing handlers (useful inside containers where uvicorn
    or other libs may pre-configure logging).
    """
    root = logging.getLogger()

    if force:
        root.handlers.clear()

    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter(service_name=service_name))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)