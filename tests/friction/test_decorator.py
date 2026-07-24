"""Tests for the friction decorators."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mcp_authflow_resource.friction.decorator import (
    _get_client_id,
    friction_controlled,
    record_tool_call,
)
from mcp_authflow_resource.friction.models import (
    ControllerConfig,
    FrictionLevel,
    FrictionResult,
    ToolFrictionConfig,
)
from mcp_authflow_resource.friction.registry import FrictionRegistry


@pytest.fixture
def registry() -> FrictionRegistry:
    return FrictionRegistry(
        default_config=ControllerConfig(warmup_calls=0, time_decay_rate=0.0),
        tool_configs={"my_tool": ToolFrictionConfig(target_rate=0.05)},
    )


def _make_result(
    allowed: bool = True,
    blocked: bool = False,
    justification: bool = False,
) -> FrictionResult:
    if blocked:
        level = FrictionLevel.BLOCKED
    elif justification:
        level = FrictionLevel.HIGH
    else:
        level = FrictionLevel.NONE
    return FrictionResult(
        tool_name="my_tool",
        allowed=allowed,
        cost=1.0,
        friction_level=level,
        current_rate=0.05,
        target_rate=0.05,
        justification_required=justification,
        message="Blocked: test" if blocked else "Allowed: test",
    )


class TestFrictionControlled:
    @pytest.mark.asyncio
    async def test_allows_call(self, registry: FrictionRegistry) -> None:
        registry.check_and_record = AsyncMock(return_value=_make_result(allowed=True))

        @friction_controlled(registry=registry)
        async def my_tool() -> str:
            return "ok"

        with patch(
            "mcp_authflow_resource.friction.decorator._get_client_id",
            return_value="test-client",
        ):
            result = await my_tool()

        assert result == "ok"
        registry.check_and_record.assert_called_once_with("test-client", "my_tool")

    @pytest.mark.asyncio
    async def test_blocks_call(self, registry: FrictionRegistry) -> None:
        registry.check_and_record = AsyncMock(
            return_value=_make_result(allowed=False, blocked=True)
        )

        @friction_controlled(registry=registry)
        async def my_tool() -> str:
            return "ok"

        with patch(
            "mcp_authflow_resource.friction.decorator._get_client_id",
            return_value="test-client",
        ):
            result = await my_tool()

        assert "rate-limited" in result
        assert "my_tool" in result

    @pytest.mark.asyncio
    async def test_no_auth_context_allows(self, registry: FrictionRegistry) -> None:
        """When there's no auth context, tool runs without friction."""

        @friction_controlled(registry=registry)
        async def my_tool() -> str:
            return "ok"

        with patch(
            "mcp_authflow_resource.friction.decorator._get_client_id",
            return_value=None,
        ):
            result = await my_tool()

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_registry_allows(self) -> None:
        """When no registry is configured, tool runs without friction."""

        @friction_controlled(registry=None)
        async def my_tool() -> str:
            return "ok"

        result = await my_tool()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self, registry: FrictionRegistry) -> None:
        @friction_controlled(registry=registry)
        async def my_special_tool() -> str:
            """A special tool."""
            return "ok"

        assert my_special_tool.__name__ == "my_special_tool"
        assert my_special_tool.__doc__ == "A special tool."


class TestGetClientId:
    """Exercise the real client-id extraction, not a mock of it.

    ``_get_client_id`` reads the authenticated client from the MCP auth
    context.  A wrong read or a silent ``None`` here determines whether a
    friction/budget penalty is attributed to the right client, so the real
    path — including the fail-open ``except`` branch — needs coverage.
    """

    def test_returns_client_id_from_context(self) -> None:
        # Stand-in for mcp's AccessToken; _get_client_id only reads client_id.
        token = SimpleNamespace(client_id="client-123")

        with patch(
            "mcp.server.auth.middleware.auth_context.get_access_token",
            return_value=token,
        ):
            assert _get_client_id() == "client-123"

    def test_returns_none_when_no_token(self) -> None:
        with patch(
            "mcp.server.auth.middleware.auth_context.get_access_token",
            return_value=None,
        ):
            assert _get_client_id() is None

    def test_swallows_exception_returns_none(self) -> None:
        """Fail-open: an error reading the context must not crash the tool.

        The context var is unset outside a request (e.g. in tests or on
        unauthenticated endpoints); ``_get_client_id`` returns ``None`` so
        the caller allows the call rather than raising.
        """

        with patch(
            "mcp.server.auth.middleware.auth_context.get_access_token",
            side_effect=RuntimeError("no auth context"),
        ):
            assert _get_client_id() is None


class TestRecordToolCall:
    @pytest.mark.asyncio
    async def test_records_and_allows(self, registry: FrictionRegistry) -> None:
        registry.record_unconfigured = AsyncMock()

        @record_tool_call(registry=registry)
        async def get_tasks() -> str:
            return "tasks"

        with patch(
            "mcp_authflow_resource.friction.decorator._get_client_id",
            return_value="test-client",
        ):
            result = await get_tasks()

        assert result == "tasks"
        registry.record_unconfigured.assert_called_once_with("test-client", "get_tasks")

    @pytest.mark.asyncio
    async def test_no_registry_still_works(self) -> None:
        @record_tool_call(registry=None)
        async def get_tasks() -> str:
            return "tasks"

        result = await get_tasks()
        assert result == "tasks"
