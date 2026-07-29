"""Structured friction event logging for Loki/Grafana observability.

All friction events are emitted as structured JSON log records via Python's
standard logging module.  The Alloy log collector picks up pod stdout and
ships it to Loki with labels ``{namespace, pod, container, app}``.

Logger hierarchy::

    mcp_authflow_resource.friction          -- check / record events
    mcp_authflow_resource.friction.block    -- blocked tool calls (WARNING)
    mcp_authflow_resource.friction.registry -- client lifecycle events

See ``packages/mcp-authflow-resource/CLAUDE.md`` for LogQL query examples.
"""

import json
import logging

from .models import FrictionResult

logger = logging.getLogger("mcp_authflow_resource.friction")
block_logger = logging.getLogger("mcp_authflow_resource.friction.block")
registry_logger = logging.getLogger("mcp_authflow_resource.friction.registry")


LogFields = dict[str, str | float | bool]


def _emit(
    target_logger: logging.Logger,
    level: int,
    event_type: str,
    fields: LogFields,
) -> None:
    """Emit ``<event_type> <compact-json>`` on ``target_logger``.

    ``event_type`` is also injected as the first key of the JSON payload, so the
    record text is identical whether it is matched by the plain-text prefix or
    parsed as JSON by LogQL.
    """
    payload: LogFields = {"event_type": event_type, **fields}
    target_logger.log(level, "%s %s", event_type, json.dumps(payload, separators=(",", ":")))


def _friction_fields(
    client_id: str,
    tool_name: str,
    result: FrictionResult,
) -> LogFields:
    """Build structured fields for a friction log record."""
    return {
        "client_id": client_id,
        "tool_name": tool_name,
        "friction": round(result.friction, 4),
        "friction_level": result.friction_level.name,
        "ema_rate": round(result.current_rate, 6),
        "target_rate": round(result.target_rate, 6),
        "effective_target": round(result.effective_target, 6),
        "cost": round(result.cost, 4),
        "allowed": result.allowed,
        "justification_required": result.justification_required,
        "saturation_detected": result.saturation_detected,
    }


def log_check(client_id: str, tool_name: str, result: FrictionResult) -> None:
    """Log a friction check (every tool call that passes through the decorator)."""
    _emit(logger, logging.INFO, "friction_check", _friction_fields(client_id, tool_name, result))


def log_block(client_id: str, tool_name: str, result: FrictionResult) -> None:
    """Log a blocked tool call (WARNING level for alerting)."""
    _emit(
        block_logger,
        logging.WARNING,
        "friction_block",
        _friction_fields(client_id, tool_name, result),
    )


def log_justification_required(client_id: str, tool_name: str, result: FrictionResult) -> None:
    """Log when justification threshold is reached."""
    _emit(
        logger,
        logging.INFO,
        "friction_justification",
        _friction_fields(client_id, tool_name, result),
    )


def log_saturation(
    client_id: str,
    tool_name: str,
    effective_target: float,
    original_target: float,
) -> None:
    """Log saturation detection event."""
    _emit(
        logger,
        logging.WARNING,
        "friction_saturation",
        {
            "client_id": client_id,
            "tool_name": tool_name,
            "effective_target": round(effective_target, 6),
            "original_target": round(original_target, 6),
        },
    )


def log_client_evicted(client_id: str, total_clients: int) -> None:
    """Log LRU eviction of a client's friction state."""
    _emit(
        registry_logger,
        logging.INFO,
        "friction_client_evicted",
        {"client_id": client_id, "total_clients": total_clients},
    )


def log_penalty_captured(client_id: str, peak_friction: float, ttl: float) -> None:
    """Log persistence of an evicted client's accrued friction penalty."""
    _emit(
        registry_logger,
        logging.INFO,
        "friction_penalty_captured",
        {
            "client_id": client_id,
            "peak_friction": round(peak_friction, 4),
            "ttl": round(ttl, 3),
        },
    )


def log_penalty_restored(client_id: str, peak_friction: float) -> None:
    """Log restoration of a persisted friction penalty onto a fresh controller."""
    _emit(
        registry_logger,
        logging.INFO,
        "friction_penalty_restored",
        {"client_id": client_id, "peak_friction": round(peak_friction, 4)},
    )


def log_client_created(client_id: str, total_clients: int) -> None:
    """Log creation of a new per-client friction controller."""
    _emit(
        registry_logger,
        logging.DEBUG,
        "friction_client_created",
        {"client_id": client_id, "total_clients": total_clients},
    )
