"""Decorators for applying friction control to MCP tool handlers.

Usage::

    from mcp_authflow_resource.friction import friction_controlled, record_tool_call

    @app.tool()
    @guard_tool(input_params=["title"])
    @friction_controlled()
    async def create_task(title: str) -> str:
        ...

    @app.tool()
    @guard_tool(input_params=["status"])
    @record_tool_call()
    async def get_tasks(status: str) -> str:
        ...
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from .registry import FrictionRegistry

logger = logging.getLogger("mcp_authflow_resource.friction")

P = ParamSpec("P")
R = TypeVar("R")

_default_registry: FrictionRegistry | None = None


def init_friction(
    registry: FrictionRegistry,
) -> FrictionRegistry:
    """Set the default friction registry.  Call once at server startup.

    Returns the registry for convenience (allows ``r = init_friction(...)``).
    """
    global _default_registry  # noqa: PLW0603
    _default_registry = registry
    return registry


def _get_client_id() -> str | None:
    """Extract the authenticated client_id from the MCP auth context.

    Returns None if no auth context is available (e.g., in tests or
    unauthenticated endpoints).
    """
    try:
        from mcp.server.auth.middleware.auth_context import (  # noqa: PLC0415
            get_access_token,
        )

        access = get_access_token()
        if access is not None:
            return str(access.client_id)
    except Exception:
        pass
    return None


def friction_controlled(
    registry: FrictionRegistry | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that checks friction before executing a tool handler.

    If friction is too high the tool call is blocked and an error message
    is returned instead of executing the handler.

    Args:
        registry: Explicit registry for testing.  Falls back to the
            module-level default set by :func:`init_friction`.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:  # noqa: ANN401
            reg = registry or _default_registry
            if reg is None:
                return await func(*args, **kwargs)  # type: ignore[misc]

            client_id = _get_client_id()
            if client_id is None:
                logger.debug(
                    "friction_controlled: no auth context for %s, allowing",
                    func.__name__,
                )
                return await func(*args, **kwargs)  # type: ignore[misc]

            result = await reg.check_and_record(client_id, func.__name__)

            if not result.allowed:
                return (
                    f"Tool '{func.__name__}' is temporarily rate-limited. "
                    f"{result.message}. Please try a different approach or "
                    "wait before retrying."
                )

            return await func(*args, **kwargs)  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


def record_tool_call(
    registry: FrictionRegistry | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that records a tool call without applying friction.

    Use on read-only / unconfigured tools so the sliding window
    denominator is accurate.

    Args:
        registry: Explicit registry for testing.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:  # noqa: ANN401
            reg = registry or _default_registry
            if reg is not None:
                client_id = _get_client_id()
                if client_id is not None:
                    await reg.record_unconfigured(client_id, func.__name__)

            return await func(*args, **kwargs)  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
