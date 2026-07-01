"""Dynamic difficulty adjustment controller for agent tool-use friction.

Monitors per-tool usage rates in a sliding window and adjusts friction
(cost multiplier + justification requirements) to converge on configured
target rates.  Analogous to PoW difficulty adjustment.
"""

import math
import time

from .models import (
    ControllerConfig,
    FrictionLevel,
    FrictionResult,
    ToolFrictionConfig,
    ToolGroupConfig,
)
from .window import SlidingWindow


class FrictionController:
    """Adjusts tool-use friction dynamically based on observed usage rates.

    The controller maintains:
    - A sliding window of recent tool calls
    - Per-tool EMA-smoothed usage rates
    - Per-tool friction multipliers that adjust toward target rates

    Refinements:
    1. **Asymmetric decay**: friction decreases faster than it increases.
    2. **Dead zone**: no adjustment near the target rate.
    3. **Warmup period**: no adjustment during the first N calls.
    4. **Time decay**: friction decays exponentially during idle periods.
    5. **Saturation detection**: auto-raises unreachable targets.
    6. **Aggregate groups**: prevents tool displacement (whack-a-mole).
    """

    def __init__(self, config: ControllerConfig | None = None) -> None:
        self.config = config or ControllerConfig()
        self.window = SlidingWindow(max_size=self.config.window_size)
        self._budget_remaining = self.config.default_budget
        self._total_calls = 0

        self._friction_levels: dict[str, float] = {}
        self._ema_rates: dict[str, float] = {}
        self._last_call_time: float | None = None

        # Saturation detection
        self._saturation_counters: dict[str, int] = {}
        self._effective_targets: dict[str, float] = {}
        self._saturation_detected: dict[str, bool] = {}

        # Aggregate group state
        self._group_ema_rates: dict[str, float] = {}

    def configure_tool(self, tool_name: str, config: ToolFrictionConfig) -> None:
        """Register or update friction config for a tool."""
        self.config.tool_configs[tool_name] = config
        if tool_name not in self._friction_levels:
            self._friction_levels[tool_name] = 0.0
            self._ema_rates[tool_name] = 0.0
            self._saturation_counters[tool_name] = 0
            self._effective_targets[tool_name] = config.target_rate
            self._saturation_detected[tool_name] = False

    def configure_group(self, group_name: str, config: ToolGroupConfig) -> None:
        """Register or update an aggregate rate budget group."""
        self.config.tool_groups[group_name] = config
        if group_name not in self._group_ema_rates:
            self._group_ema_rates[group_name] = 0.0

    def check(self, tool_name: str) -> FrictionResult:
        """Check friction for a proposed tool call WITHOUT recording it."""
        tc = self.config.tool_configs.get(tool_name)
        if tc is None:
            return FrictionResult(
                tool_name=tool_name,
                allowed=True,
                cost=0.0,
                friction_level=FrictionLevel.NONE,
                current_rate=self.window.rate(tool_name),
                target_rate=0.0,
                justification_required=False,
                message="No friction configured",
            )

        friction = self._friction_levels.get(tool_name, 0.0)
        cost = tc.min_cost + friction * (tc.max_cost - tc.min_cost)
        current_rate = self._ema_rates.get(tool_name, 0.0)
        effective_target = self._effective_targets.get(tool_name, tc.target_rate)
        saturated = self._saturation_detected.get(tool_name, False)

        justification_required = friction >= tc.justification_threshold
        blocked = friction >= tc.hard_block_threshold

        if blocked:
            level = FrictionLevel.BLOCKED
        elif justification_required:
            level = FrictionLevel.HIGH
        elif friction > 0.3:
            level = FrictionLevel.MEDIUM
        elif friction > 0.0:
            level = FrictionLevel.LOW
        else:
            level = FrictionLevel.NONE

        budget_ok = self._budget_remaining >= cost
        allowed = not blocked and budget_ok

        if not budget_ok:
            message = f"Insufficient budget: {self._budget_remaining:.1f} < {cost:.1f}"
        elif blocked:
            message = f"Blocked: friction {friction:.2f} >= threshold {tc.hard_block_threshold}"
        elif justification_required:
            message = f"Justification required: friction {friction:.2f}, cost {cost:.1f}"
        else:
            message = f"Allowed: friction {friction:.2f}, cost {cost:.1f}"

        return FrictionResult(
            tool_name=tool_name,
            allowed=allowed,
            cost=cost,
            friction_level=level,
            current_rate=current_rate,
            target_rate=tc.target_rate,
            justification_required=justification_required,
            message=message,
            friction=friction,
            effective_target=effective_target,
            saturation_detected=saturated,
        )

    def record_call(
        self,
        tool_name: str,
        timestamp: float | None = None,
    ) -> FrictionResult:
        """Record a tool call and adjust friction levels.

        Returns FrictionResult reflecting state AFTER this call.
        """
        ts = timestamp if timestamp is not None else time.monotonic()

        self._apply_time_decay(ts)

        result = self.check(tool_name)
        cost = result.cost

        self.window.record(tool_name, ts, cost)
        self._total_calls += 1

        self._adjust_friction()
        self._budget_remaining -= cost
        self._last_call_time = ts

        return self.check(tool_name)

    def record_call_unconfigured(
        self,
        tool_name: str,
        timestamp: float | None = None,
    ) -> None:
        """Record a call to an unconfigured tool (for rate tracking only)."""
        ts = timestamp if timestamp is not None else time.monotonic()

        self._apply_time_decay(ts)

        self.window.record(tool_name, ts, 0.0)
        self._total_calls += 1

        self._adjust_friction()
        self._last_call_time = ts

    def _apply_time_decay(self, timestamp: float) -> None:
        """Decay friction and EMA rates based on elapsed wall-clock time."""
        rate = self.config.time_decay_rate
        if rate <= 0.0 or self._last_call_time is None:
            return

        dt = timestamp - self._last_call_time
        if dt <= 0.0:
            return

        decay_factor = math.exp(-rate * dt)
        for name in self._friction_levels:
            self._friction_levels[name] *= decay_factor
            if name in self._ema_rates:
                self._ema_rates[name] *= decay_factor

    def _adjust_friction(self) -> None:
        """Update EMA rates and adjust friction for all configured tools."""
        in_warmup = self._total_calls < self.config.warmup_calls
        alpha = self.config.ema_alpha

        for name, tc in self.config.tool_configs.items():
            raw_rate = self.window.rate(name)
            old_ema = self._ema_rates.get(name, 0.0)
            new_ema = alpha * raw_rate + (1 - alpha) * old_ema
            self._ema_rates[name] = new_ema

            if in_warmup:
                continue

            effective_target = self._effective_targets.get(name, tc.target_rate)
            error = new_ema - effective_target

            if abs(error) < self.config.dead_zone:
                continue

            delta = self.config.adjustment_rate * error
            if delta < 0:
                delta *= self.config.asymmetric_decay

            old_friction = self._friction_levels.get(name, 0.0)
            new_friction = max(0.0, min(1.0, old_friction + delta))
            self._friction_levels[name] = new_friction

        if in_warmup:
            return

        self._detect_saturation()
        self._apply_aggregate_pressure()

    def _detect_saturation(self) -> None:
        """Detect saturated tools and apply relief via gradient ascent."""
        window = self.config.saturation_window
        if window <= 0:
            return

        threshold = self.config.saturation_threshold
        step = self.config.saturation_relief_rate

        for name, tc in self.config.tool_configs.items():
            friction = self._friction_levels.get(name, 0.0)

            if friction >= threshold:
                self._saturation_counters[name] = self._saturation_counters.get(name, 0) + 1
            else:
                self._saturation_counters[name] = 0

            counter = self._saturation_counters.get(name, 0)

            if counter >= window and not self._saturation_detected.get(name, False):
                self._saturation_detected[name] = True

            if self._saturation_detected.get(name, False) and counter >= window:
                current_target = self._effective_targets.get(name, tc.target_rate)
                self._effective_targets[name] = min(current_target + step, 1.0)

    def _apply_aggregate_pressure(self) -> None:
        """Apply cross-tool friction from aggregate rate budgets."""
        alpha = self.config.ema_alpha

        for group_name, group in self.config.tool_groups.items():
            aggregate_rate = sum(self._ema_rates.get(tool, 0.0) for tool in group.tools)

            old_group_ema = self._group_ema_rates.get(group_name, 0.0)
            new_group_ema = alpha * aggregate_rate + (1 - alpha) * old_group_ema
            self._group_ema_rates[group_name] = new_group_ema

            aggregate_error = new_group_ema - group.aggregate_target
            if aggregate_error <= self.config.dead_zone:
                continue

            for tool in group.tools:
                if tool not in self.config.tool_configs:
                    continue

                tool_ema = self._ema_rates.get(tool, 0.0)
                share = (
                    tool_ema / aggregate_rate if aggregate_rate > 0 else (1.0 / len(group.tools))
                )
                delta = (
                    self.config.adjustment_rate * aggregate_error * share * group.pressure_weight
                )

                old_friction = self._friction_levels.get(tool, 0.0)
                self._friction_levels[tool] = max(0.0, min(1.0, old_friction + delta))

    def peak_friction_levels(self) -> dict[str, float]:
        """Snapshot current per-tool friction levels for persistence.

        Used by the registry to carry accrued friction across LRU eviction
        so a client cannot reset its friction to zero by forcing its own
        eviction and reconnecting.
        """
        return dict(self._friction_levels)

    def restore_friction_levels(self, levels: dict[str, float]) -> None:
        """Seed friction levels from a persisted snapshot.

        Applied as a floor: a restored value never lowers an existing
        friction level, and every value is clamped to ``[0.0, 1.0]``.
        """
        for tool, friction in levels.items():
            clamped = max(0.0, min(1.0, friction))
            self._friction_levels[tool] = max(self._friction_levels.get(tool, 0.0), clamped)

    def reset_budget(self, budget: float | None = None) -> None:
        """Reset the budget (e.g., at the start of a new session)."""
        self._budget_remaining = budget if budget is not None else self.config.default_budget

    @property
    def budget_remaining(self) -> float:
        return self._budget_remaining

    def get_status(self) -> dict[str, dict]:
        """Get current friction status for all configured tools."""
        status: dict[str, dict] = {}
        for name in self.config.tool_configs:
            result = self.check(name)
            status[name] = {
                "friction": self._friction_levels.get(name, 0.0),
                "ema_rate": self._ema_rates.get(name, 0.0),
                "raw_rate": self.window.rate(name),
                "target_rate": result.target_rate,
                "effective_target": self._effective_targets.get(name, result.target_rate),
                "saturation_detected": self._saturation_detected.get(name, False),
                "cost": result.cost,
                "level": result.friction_level.name,
                "allowed": result.allowed,
            }
        return status
