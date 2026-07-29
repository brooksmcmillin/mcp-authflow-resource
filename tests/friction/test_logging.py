"""Tests for the structured friction log records consumed by Loki/LogQL.

The record text is part of the observability contract: every event is emitted as
``<event_type> <compact-json>`` with ``event_type`` as the first JSON key, so
both plain-text prefix matching and JSON parsing keep working.
"""

import json
import logging
from collections.abc import Callable

import pytest

from mcp_authflow_resource.friction import logging as friction_logging
from mcp_authflow_resource.friction.models import FrictionLevel, FrictionResult


def _result() -> FrictionResult:
    return FrictionResult(
        tool_name="search",
        message="",
        allowed=True,
        friction=0.123456,
        friction_level=FrictionLevel.LOW,
        current_rate=0.1234567,
        target_rate=0.5,
        effective_target=0.4999999,
        cost=1.234567,
        justification_required=False,
        saturation_detected=False,
    )


def _record(caplog: pytest.LogCaptureFixture, event_type: str) -> logging.LogRecord:
    matches = [r for r in caplog.records if r.getMessage().startswith(f"{event_type} ")]
    assert len(matches) == 1, f"expected exactly one {event_type} record, got {len(matches)}"
    return matches[0]


def _payload(record: logging.LogRecord) -> dict[str, object]:
    _, _, raw = record.getMessage().partition(" ")
    return json.loads(raw)


class TestRecordFormat:
    """Every log_* helper emits the same event-prefix + compact-JSON shape."""

    def test_check_message_is_event_prefix_plus_compact_json(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="mcp_authflow_resource.friction"):
            friction_logging.log_check("client-a", "search", _result())

        message = _record(caplog, "friction_check").getMessage()
        assert message == "friction_check " + json.dumps(
            {
                "event_type": "friction_check",
                "client_id": "client-a",
                "tool_name": "search",
                "friction": 0.1235,
                "friction_level": "LOW",
                "ema_rate": 0.123457,
                "target_rate": 0.5,
                "effective_target": 0.5,
                "cost": 1.2346,
                "allowed": True,
                "justification_required": False,
                "saturation_detected": False,
            },
            separators=(",", ":"),
        )
        # No spaces after separators — Loki line size matters.
        assert ", " not in message
        assert '": ' not in message

    @pytest.mark.parametrize(
        ("emit", "event_type", "logger_name", "level"),
        [
            (
                lambda: friction_logging.log_check("c", "t", _result()),
                "friction_check",
                "mcp_authflow_resource.friction",
                logging.INFO,
            ),
            (
                lambda: friction_logging.log_block("c", "t", _result()),
                "friction_block",
                "mcp_authflow_resource.friction.block",
                logging.WARNING,
            ),
            (
                lambda: friction_logging.log_justification_required("c", "t", _result()),
                "friction_justification",
                "mcp_authflow_resource.friction",
                logging.INFO,
            ),
            (
                lambda: friction_logging.log_saturation("c", "t", 0.25, 0.5),
                "friction_saturation",
                "mcp_authflow_resource.friction",
                logging.WARNING,
            ),
            (
                lambda: friction_logging.log_client_evicted("c", 3),
                "friction_client_evicted",
                "mcp_authflow_resource.friction.registry",
                logging.INFO,
            ),
            (
                lambda: friction_logging.log_penalty_captured("c", 0.5, 60.0),
                "friction_penalty_captured",
                "mcp_authflow_resource.friction.registry",
                logging.INFO,
            ),
            (
                lambda: friction_logging.log_penalty_restored("c", 0.5),
                "friction_penalty_restored",
                "mcp_authflow_resource.friction.registry",
                logging.INFO,
            ),
            (
                lambda: friction_logging.log_client_created("c", 1),
                "friction_client_created",
                "mcp_authflow_resource.friction.registry",
                logging.DEBUG,
            ),
        ],
    )
    def test_logger_level_and_event_type_key(
        self,
        caplog: pytest.LogCaptureFixture,
        emit: Callable[[], None],
        event_type: str,
        logger_name: str,
        level: int,
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            emit()

        record = _record(caplog, event_type)
        assert record.name == logger_name
        assert record.levelno == level
        payload = _payload(record)
        assert next(iter(payload)) == "event_type"
        assert payload["event_type"] == event_type
        assert payload["client_id"] == "c"


class TestEventFields:
    """Per-event field sets stay as documented for LogQL queries."""

    def test_saturation_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="mcp_authflow_resource.friction"):
            friction_logging.log_saturation("client-b", "fetch", 0.2500004, 0.5)

        assert _payload(_record(caplog, "friction_saturation")) == {
            "event_type": "friction_saturation",
            "client_id": "client-b",
            "tool_name": "fetch",
            "effective_target": 0.25,
            "original_target": 0.5,
        }

    def test_penalty_captured_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="mcp_authflow_resource.friction.registry"):
            friction_logging.log_penalty_captured("client-c", 0.87654, 60.98765)

        assert _payload(_record(caplog, "friction_penalty_captured")) == {
            "event_type": "friction_penalty_captured",
            "client_id": "client-c",
            "peak_friction": 0.8765,
            "ttl": 60.988,
        }

    def test_client_evicted_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="mcp_authflow_resource.friction.registry"):
            friction_logging.log_client_evicted("client-d", 7)

        assert _payload(_record(caplog, "friction_client_evicted")) == {
            "event_type": "friction_client_evicted",
            "client_id": "client-d",
            "total_clients": 7,
        }
