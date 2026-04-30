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


def _friction_extra(
    client_id: str,
    tool_name: str,
    result: FrictionResult,
    event_type: str,
) -> dict[str, str | float | bool]:
    """Build structured extra fields for a friction log record."""
    return {
        "event_type": event_type,
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
    extra = _friction_extra(client_id, tool_name, result, "friction_check")
    logger.info("friction_check %s", json.dumps(extra, separators=(",", ":")))


def log_block(client_id: str, tool_name: str, result: FrictionResult) -> None:
    """Log a blocked tool call (WARNING level for alerting)."""
    extra = _friction_extra(client_id, tool_name, result, "friction_block")
    block_logger.warning("friction_block %s", json.dumps(extra, separators=(",", ":")))


def log_justification_required(client_id: str, tool_name: str, result: FrictionResult) -> None:
    """Log when justification threshold is reached."""
    extra = _friction_extra(client_id, tool_name, result, "friction_justification")
    logger.info("friction_justification %s", json.dumps(extra, separators=(",", ":")))


def log_saturation(
    client_id: str,
    tool_name: str,
    effective_target: float,
    original_target: float,
) -> None:
    """Log saturation detection event."""
    extra = {
        "event_type": "friction_saturation",
        "client_id": client_id,
        "tool_name": tool_name,
        "effective_target": round(effective_target, 6),
        "original_target": round(original_target, 6),
    }
    logger.warning("friction_saturation %s", json.dumps(extra, separators=(",", ":")))


def log_client_evicted(client_id: str, total_clients: int) -> None:
    """Log LRU eviction of a client's friction state."""
    extra = {
        "event_type": "friction_client_evicted",
        "client_id": client_id,
        "total_clients": total_clients,
    }
    registry_logger.info("friction_client_evicted %s", json.dumps(extra, separators=(",", ":")))


def log_client_created(client_id: str, total_clients: int) -> None:
    """Log creation of a new per-client friction controller."""
    extra = {
        "event_type": "friction_client_created",
        "client_id": client_id,
        "total_clients": total_clients,
    }
    registry_logger.debug("friction_client_created %s", json.dumps(extra, separators=(",", ":")))
