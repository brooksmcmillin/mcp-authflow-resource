"""Per-client FrictionController management with LRU eviction.

Each authenticated MCP client gets an independent friction controller so
that one client's usage patterns don't affect another's friction levels.
"""

import asyncio
import time
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

    LRU eviction keeps memory bounded.  Accrued friction survives eviction
    via a lightweight, non-evictable penalty store: when a client with
    meaningful friction is evicted, its per-tool friction levels are recorded
    with a TTL and restored (as a floor) onto the fresh controller it gets on
    reconnect.  This prevents a client from resetting its friction to zero by
    forcing its own LRU eviction (registering ``max_clients + 1`` distinct
    OAuth ``client_id``\\ s) and reconnecting.

    Args:
        default_config: Base controller config cloned for each client.
        tool_configs: Per-tool friction configs applied to every client.
        tool_groups: Aggregate group configs applied to every client.
        max_clients: Maximum concurrent client controllers before LRU eviction.
        penalty_ttl: Seconds an evicted client's friction penalty persists.
            Set to ``0.0`` (or negative) to disable the penalty store entirely
            and restore the legacy fresh-on-eviction behaviour.
        penalty_min_friction: Minimum per-tool friction worth persisting.
            Tools below this level are not carried across eviction, keeping the
            penalty store small and focused on clients that actually accrued
            friction.
    """

    def __init__(
        self,
        default_config: ControllerConfig | None = None,
        tool_configs: dict[str, ToolFrictionConfig] | None = None,
        tool_groups: dict[str, ToolGroupConfig] | None = None,
        max_clients: int = 1000,
        penalty_ttl: float = 3600.0,
        penalty_min_friction: float = 0.1,
    ) -> None:
        self._default_config = default_config or ControllerConfig()
        self._tool_configs = tool_configs or {}
        self._tool_groups = tool_groups or {}
        self._max_clients = max_clients
        self._penalty_ttl = penalty_ttl
        self._penalty_min_friction = penalty_min_friction

        self._controllers: dict[str, FrictionController] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._access_order: OrderedDict[str, None] = OrderedDict()
        self._global_lock = asyncio.Lock()

        # client_id -> (expiry_monotonic, {tool_name: peak_friction}).
        # Bounded by TTL-based pruning on each capture, keyed on the OAuth
        # client_id passed through from the token. (Keyed on client_id rather
        # than the OAuth ``sub`` claim, matching the controller granularity;
        # thread ``sub`` through here if per-user penalties are ever needed.)
        self._penalty_store: dict[str, tuple[float, dict[str, float]]] = {}

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

    async def _get_or_create(self, client_id: str) -> tuple[FrictionController, asyncio.Lock]:
        """Get or lazily create a controller, evicting LRU if needed.

        Returns the ``(controller, lock)`` pair atomically so callers never
        have to re-look up ``self._locks[client_id]`` outside a lock — a
        concurrent LRU eviction could delete that entry between the lookups.
        """
        if client_id in self._controllers:
            self._access_order.move_to_end(client_id)
            return self._controllers[client_id], self._locks[client_id]

        async with self._global_lock:
            # Double-check after acquiring lock
            if client_id in self._controllers:
                self._access_order.move_to_end(client_id)
                return self._controllers[client_id], self._locks[client_id]

            now = time.monotonic()

            # Evict LRU if at capacity, persisting accrued friction first.
            while len(self._controllers) >= self._max_clients:
                evicted_id, _ = self._access_order.popitem(last=False)
                self._capture_penalty(evicted_id, self._controllers[evicted_id], now)
                del self._controllers[evicted_id]
                del self._locks[evicted_id]
                friction_logging.log_client_evicted(evicted_id, len(self._controllers))

            controller = self._create_controller(client_id)
            self._restore_penalty(client_id, controller, now)
            lock = asyncio.Lock()
            self._controllers[client_id] = controller
            self._locks[client_id] = lock
            self._access_order[client_id] = None
            friction_logging.log_client_created(client_id, len(self._controllers))
            return controller, lock

    def _capture_penalty(
        self,
        client_id: str,
        controller: FrictionController,
        now: float,
    ) -> None:
        """Persist an evicted client's accrued friction for later restoration."""
        if self._penalty_ttl <= 0.0:
            return

        self._prune_penalties(now)

        penalized = {
            tool: friction
            for tool, friction in controller.peak_friction_levels().items()
            if friction >= self._penalty_min_friction
        }
        if not penalized:
            return

        # Merge with any unexpired existing penalty, keeping the per-tool peak.
        existing = self._penalty_store.get(client_id)
        if existing is not None and existing[0] > now:
            for tool, friction in existing[1].items():
                penalized[tool] = max(penalized.get(tool, 0.0), friction)

        self._penalty_store[client_id] = (now + self._penalty_ttl, penalized)
        friction_logging.log_penalty_captured(client_id, max(penalized.values()), self._penalty_ttl)

    def _restore_penalty(
        self,
        client_id: str,
        controller: FrictionController,
        now: float,
    ) -> None:
        """Restore a persisted friction penalty onto a fresh controller."""
        entry = self._penalty_store.pop(client_id, None)
        if entry is None:
            return

        expiry, levels = entry
        if expiry <= now or not levels:
            return

        controller.restore_friction_levels(levels)
        friction_logging.log_penalty_restored(client_id, max(levels.values()))

    def _prune_penalties(self, now: float) -> None:
        """Drop expired entries so the penalty store stays bounded."""
        expired = [cid for cid, (expiry, _) in self._penalty_store.items() if expiry <= now]
        for cid in expired:
            del self._penalty_store[cid]

    async def check_and_record(self, client_id: str, tool_name: str) -> FrictionResult:
        """Check friction and record a tool call atomically.

        This is the primary entry point from the ``@friction_controlled()``
        decorator.  It acquires the per-client lock, checks friction,
        logs the result, and records the call if allowed.

        Returns:
            FrictionResult with the check outcome (before recording).
        """
        controller, lock = await self._get_or_create(client_id)

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
        controller, lock = await self._get_or_create(client_id)

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
