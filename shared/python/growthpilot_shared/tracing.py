from __future__ import annotations

from .logging import get_logger

log = get_logger("shared.tracing")


def try_enable_ddtrace(
    *,
    enabled: bool,
    service: str = "",
    env: str = "",
    version: str = "",
) -> bool:
    """
    Enables ddtrace auto-instrumentation if ddtrace is installed.
    Returns True if enabled, False otherwise.
    """
    if not enabled:
        return False

    try:
        import ddtrace  # type: ignore
        from ddtrace import patch_all  # type: ignore

        if service:
            ddtrace.config.service = service
        if env:
            ddtrace.config.env = env
        if version:
            ddtrace.config.version = version

        patch_all()
        log.info("ddtrace enabled", extra={"service": service, "env": env, "version": version})
        return True
    except Exception as e:
        log.warning("ddtrace not enabled", extra={"error": str(e)})
        return False