"""Per-client FrictionController management with LRU eviction.

Each authenticated MCP client gets an independent friction controller so
that one client's usage patterns don't affect another's friction levels.
"""

import asyncio
from collections import OrderedDict

from . import logging as friction_logging
from .controller import FrictionController
from .models import (
    ControllerConfig,
    FrictionResult,
    ToolFrictionConfig,
    ToolGroupConfig,
)


class FrictionRegistry:
    """Manages per-client :class:`FrictionController` instances.

    Thread/async-safe: each client gets its own ``asyncio.Lock`` so that
    concurrent tool calls from the same client serialize through the
    controller, while calls from different clients run in parallel.

    LRU eviction keeps memory bounded.  Evicted clients simply get a
    fresh controller on reconnect (equivalent to the POC's FRESH
    persistence mode).

    Args:
        default_config: Base controller config cloned for each client.
        tool_configs: Per-tool friction configs applied to every client.
        tool_groups: Aggregate group configs applied to every client.
        max_clients: Maximum concurrent client controllers before LRU eviction.
    """

    def __init__(
        self,
        default_config: ControllerConfig | None = None,
        tool_configs: dict[str, ToolFrictionConfig] | None = None,
        tool_groups: dict[str, ToolGroupConfig] | None = None,
        max_clients: int = 1000,
    ) -> None:
        self._default_config = default_config or ControllerConfig()
        self._tool_configs = tool_configs or {}
        self._tool_groups = tool_groups or {}
        self._max_clients = max_clients

        self._controllers: dict[str, FrictionController] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._access_order: OrderedDict[str, None] = OrderedDict()
        self._global_lock = asyncio.Lock()

    def _create_controller(self, client_id: str) -> FrictionController:
        """Create a new controller for a client with the shared config."""
        controller = FrictionController(
            ControllerConfig(
                window_size=self._default_config.window_size,
                ema_alpha=self._default_config.ema_alpha,
                adjustment_rate=self._default_config.adjustment_rate,
                asymmetric_decay=self._default_config.asymmetric_decay,
                dead_zone=self._default_config.dead_zone,
                warmup_calls=self._default_config.warmup_calls,
                time_decay_rate=self._default_config.time_decay_rate,
                default_budget=self._default_config.default_budget,
                saturation_threshold=self._default_config.saturation_threshold,
                saturation_window=self._default_config.saturation_window,
                saturation_relief_rate=self._default_config.saturation_relief_rate,
            )
        )
        for tool_name, tc in self._tool_configs.items():
            controller.configure_tool(tool_name, tc)
        for group_name, gc in self._tool_groups.items():
            controller.configure_group(group_name, gc)
        return controller

    async def _get_or_create(self, client_id: str) -> FrictionController:
        """Get or lazily create a controller, evicting LRU if needed."""
        if client_id in self._controllers:
            self._access_order.move_to_end(client_id)
            return self._controllers[client_id]

        async with self._global_lock:
            # Double-check after acquiring lock
            if client_id in self._controllers:
                self._access_order.move_to_end(client_id)
                return self._controllers[client_id]

            # Evict LRU if at capacity
            while len(self._controllers) >= self._max_clients:
                evicted_id, _ = self._access_order.popitem(last=False)
                del self._controllers[evicted_id]
                del self._locks[evicted_id]
                friction_logging.log_client_evicted(evicted_id, len(self._controllers))

            controller = self._create_controller(client_id)
            self._controllers[client_id] = controller
            self._locks[client_id] = asyncio.Lock()
            self._access_order[client_id] = None
            friction_logging.log_client_created(client_id, len(self._controllers))
            return controller

    async def check_and_record(self, client_id: str, tool_name: str) -> FrictionResult:
        """Check friction and record a tool call atomically.

        This is the primary entry point from the ``@friction_controlled()``
        decorator.  It acquires the per-client lock, checks friction,
        logs the result, and records the call if allowed.

        Returns:
            FrictionResult with the check outcome (before recording).
        """
        controller = await self._get_or_create(client_id)
        lock = self._locks[client_id]

        async with lock:
            result = controller.check(tool_name)

            friction_logging.log_check(client_id, tool_name, result)

            if not result.allowed:
                friction_logging.log_block(client_id, tool_name, result)
                return result

            if result.justification_required:
                friction_logging.log_justification_required(client_id, tool_name, result)

            controller.record_call(tool_name)
            return result

    async def record_unconfigured(self, client_id: str, tool_name: str) -> None:
        """Record a call to an unconfigured tool (denominator tracking)."""
        controller = await self._get_or_create(client_id)
        lock = self._locks[client_id]

        async with lock:
            controller.record_call_unconfigured(tool_name)

    def get_client_status(self, client_id: str) -> dict[str, dict] | None:
        """Get friction status for a specific client (for diagnostics)."""
        controller = self._controllers.get(client_id)
        if controller is None:
            return None
        return controller.get_status()

    @property
    def client_count(self) -> int:
        """Number of active client controllers."""
        return len(self._controllers)
