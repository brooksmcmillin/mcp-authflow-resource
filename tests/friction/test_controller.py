"""Tests for the friction controller."""

from mcp_authflow_resource.friction.controller import FrictionController
from mcp_authflow_resource.friction.models import (
    ControllerConfig,
    FrictionLevel,
    ToolFrictionConfig,
    ToolGroupConfig,
)


class TestCheckUnconfiguredTool:
    def test_unconfigured_tool_allowed(self) -> None:
        c = FrictionController()
        result = c.check("unknown_tool")
        assert result.allowed is True
        assert result.cost == 0.0
        assert result.friction_level == FrictionLevel.NONE

    def test_unconfigured_tool_message(self) -> None:
        c = FrictionController()
        result = c.check("unknown_tool")
        assert "No friction configured" in result.message


class TestCheckConfiguredTool:
    def test_initial_state(self) -> None:
        c = FrictionController()
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.05))
        result = c.check("delete")
        assert result.allowed is True
        assert result.friction_level == FrictionLevel.NONE
        assert result.cost == 0.5  # min_cost at friction=0

    def test_cost_interpolation(self) -> None:
        c = FrictionController()
        c.configure_tool(
            "delete",
            ToolFrictionConfig(min_cost=1.0, max_cost=10.0),
        )
        # Manually set friction to 0.5
        c._friction_levels["delete"] = 0.5
        result = c.check("delete")
        assert result.cost == 5.5  # 1.0 + 0.5 * 9.0

    def test_justification_threshold(self) -> None:
        c = FrictionController()
        c.configure_tool(
            "delete",
            ToolFrictionConfig(justification_threshold=0.6),
        )
        c._friction_levels["delete"] = 0.7
        result = c.check("delete")
        assert result.justification_required is True
        assert result.friction_level == FrictionLevel.HIGH

    def test_hard_block(self) -> None:
        c = FrictionController()
        c.configure_tool(
            "delete",
            ToolFrictionConfig(hard_block_threshold=0.95),
        )
        c._friction_levels["delete"] = 0.96
        result = c.check("delete")
        assert result.allowed is False
        assert result.friction_level == FrictionLevel.BLOCKED


class TestRecordCall:
    def test_friction_increases_on_overuse(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.0,
        )
        c = FrictionController(config)
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.05))

        # Spam the tool — friction should rise
        for i in range(30):
            c.record_call("delete", timestamp=float(i))

        assert c._friction_levels["delete"] > 0.0

    def test_friction_decreases_when_underused(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.0,
        )
        c = FrictionController(config)
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.30))

        # Artificially set friction high
        c._friction_levels["delete"] = 0.8
        c._ema_rates["delete"] = 0.8

        # Use other tools — rate drops, friction should decrease
        for i in range(50):
            c.record_call_unconfigured("read", timestamp=float(i))

        assert c._friction_levels["delete"] < 0.8

    def test_warmup_suppresses_adjustment(self) -> None:
        config = ControllerConfig(warmup_calls=10, time_decay_rate=0.0)
        c = FrictionController(config)
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.01))

        for i in range(9):
            c.record_call("delete", timestamp=float(i))

        assert c._friction_levels["delete"] == 0.0

    def test_dead_zone(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            dead_zone=0.05,
            time_decay_rate=0.0,
        )
        c = FrictionController(config)
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.50))

        # Fill window with 50/50 split
        for i in range(50):
            c.record_call("delete", timestamp=float(i * 2))
            c.record_call_unconfigured("read", timestamp=float(i * 2 + 1))

        # Rate ≈ target, dead zone should prevent friction from moving
        assert c._friction_levels["delete"] < 0.01


class TestTimeDecay:
    def test_default_decay_is_nonzero(self) -> None:
        """Production default has time decay enabled."""
        config = ControllerConfig()
        assert config.time_decay_rate == 0.001

    def test_friction_decays_over_time(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.01,  # fast decay for testing
        )
        c = FrictionController(config)
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.05))

        # Build up friction
        for i in range(30):
            c.record_call("delete", timestamp=float(i))

        friction_before = c._friction_levels["delete"]
        assert friction_before > 0.0

        # Long idle gap
        c.record_call_unconfigured("read", timestamp=1000.0)

        # Friction should have decayed substantially
        assert c._friction_levels["delete"] < friction_before * 0.5

    def test_ema_also_decays(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.01,
        )
        c = FrictionController(config)
        c.configure_tool("delete", ToolFrictionConfig(target_rate=0.05))

        for i in range(30):
            c.record_call("delete", timestamp=float(i))

        ema_before = c._ema_rates["delete"]

        # Long gap — both friction and EMA should decay
        c.record_call_unconfigured("read", timestamp=1000.0)
        assert c._ema_rates["delete"] < ema_before


class TestSaturationDetection:
    def test_saturation_raises_effective_target(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.0,
            saturation_window=5,
            saturation_threshold=0.9,
            saturation_relief_rate=0.01,
        )
        c = FrictionController(config)
        c.configure_tool(
            "delete",
            ToolFrictionConfig(target_rate=0.01),
        )

        # Drive friction to saturation
        c._friction_levels["delete"] = 0.95

        for i in range(20):
            c.record_call("delete", timestamp=float(i))

        assert c._saturation_detected.get("delete", False) is True
        assert c._effective_targets["delete"] > 0.01


class TestAggregateGroups:
    def test_aggregate_pressure_distributes(self) -> None:
        config = ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.0,
        )
        c = FrictionController(config)
        c.configure_tool("create", ToolFrictionConfig(target_rate=0.10))
        c.configure_tool("update", ToolFrictionConfig(target_rate=0.10))
        c.configure_group(
            "mutations",
            ToolGroupConfig(
                tools=["create", "update"],
                aggregate_target=0.10,
            ),
        )

        # Heavily use both tools (combined > aggregate target)
        for i in range(40):
            c.record_call("create", timestamp=float(i * 2))
            c.record_call("update", timestamp=float(i * 2 + 1))

        # Both should have non-zero friction from aggregate pressure
        assert c._friction_levels["create"] > 0.0
        assert c._friction_levels["update"] > 0.0
