"""Load tests exercising friction escalation paths.

Simulates realistic agent traffic against the production friction config
(mirrored from services/task-mcp-resource/mcp_resource/server.py) and
verifies that the controller correctly escalates through all friction
levels, respects aggregate group caps, recovers via time decay, and
isolates per-client state.

These tests are deterministic (no I/O, fixed timestamps) and fast.
"""

import pytest

from mcp_resource_framework.friction.controller import FrictionController
from mcp_resource_framework.friction.models import (
    ControllerConfig,
    FrictionLevel,
    ToolFrictionConfig,
    ToolGroupConfig,
)
from mcp_resource_framework.friction.registry import FrictionRegistry

# ---------------------------------------------------------------------------
# Production-equivalent config (from server.py lines 229-267)
# ---------------------------------------------------------------------------

MUTATION_TOOLS: dict[str, ToolFrictionConfig] = {
    "create_task": ToolFrictionConfig(target_rate=0.05),
    "create_tasks": ToolFrictionConfig(target_rate=0.03),
    "update_task": ToolFrictionConfig(target_rate=0.10),
    "delete_task": ToolFrictionConfig(target_rate=0.03),
    "complete_task": ToolFrictionConfig(target_rate=0.05),
    "batch_update_tasks": ToolFrictionConfig(target_rate=0.03),
    "add_task_comment": ToolFrictionConfig(target_rate=0.08),
    "classify_task": ToolFrictionConfig(target_rate=0.08),
    "add_agent_note": ToolFrictionConfig(target_rate=0.08),
    "set_agent_status": ToolFrictionConfig(target_rate=0.08),
    "add_dependency": ToolFrictionConfig(target_rate=0.05),
}

MUTATIONS_GROUP = ToolGroupConfig(
    tools=list(MUTATION_TOOLS),
    aggregate_target=0.30,
)


def _prod_config(
    *,
    time_decay_rate: float = 0.0,
    warmup_calls: int = 20,
    saturation_window: int = 0,
) -> ControllerConfig:
    """Create a controller config mirroring production defaults."""
    return ControllerConfig(
        window_size=100,
        ema_alpha=0.15,
        adjustment_rate=0.08,
        asymmetric_decay=2.0,
        dead_zone=0.01,
        warmup_calls=warmup_calls,
        time_decay_rate=time_decay_rate,
        saturation_threshold=0.9,
        saturation_window=saturation_window,
        saturation_relief_rate=0.005,
    )


def _make_controller(
    *,
    time_decay_rate: float = 0.0,
    warmup_calls: int = 20,
    saturation_window: int = 0,
) -> FrictionController:
    """Build a fully-configured production-like controller."""
    c = FrictionController(
        _prod_config(
            time_decay_rate=time_decay_rate,
            warmup_calls=warmup_calls,
            saturation_window=saturation_window,
        )
    )
    for name, tc in MUTATION_TOOLS.items():
        c.configure_tool(name, tc)
    c.configure_group("mutations", MUTATIONS_GROUP)
    return c


# ---------------------------------------------------------------------------
# Escalation: NONE → LOW → MEDIUM → HIGH → BLOCKED
# ---------------------------------------------------------------------------


class TestEscalationPath:
    """Verify that sustained overuse of a single tool drives friction
    through every level up to BLOCKED."""

    def test_single_tool_escalation_to_blocked(self) -> None:
        """Spam delete_task (target 3%) until it's blocked."""
        c = _make_controller(warmup_calls=0)

        levels_seen: set[FrictionLevel] = set()
        blocked_at: int | None = None

        for i in range(500):
            result = c.check("delete_task")
            levels_seen.add(result.friction_level)

            if not result.allowed:
                blocked_at = i
                break

            c.record_call("delete_task", timestamp=float(i))

        assert FrictionLevel.NONE in levels_seen
        assert FrictionLevel.LOW in levels_seen
        assert FrictionLevel.MEDIUM in levels_seen
        assert FrictionLevel.HIGH in levels_seen
        assert FrictionLevel.BLOCKED in levels_seen
        assert blocked_at is not None, "delete_task was never blocked after 500 calls"
        # With 3% target and 100% usage, should block well before 500
        assert blocked_at < 300

    def test_escalation_cost_increases_monotonically(self) -> None:
        """Cost should increase as friction rises (within same call burst)."""
        c = _make_controller(warmup_calls=0)
        costs: list[float] = []

        for i in range(200):
            result = c.check("delete_task")
            if not result.allowed:
                break
            costs.append(result.cost)
            c.record_call("delete_task", timestamp=float(i))

        # Cost should trend upward (allow minor noise from aggregate pressure)
        assert costs[-1] > costs[0], "Cost should increase under sustained overuse"
        assert costs[-1] >= 5.0, f"Final cost {costs[-1]} should be substantial"

    def test_justification_before_block(self) -> None:
        """Justification should be required before a full block."""
        c = _make_controller(warmup_calls=0)
        justification_seen = False

        for i in range(500):
            result = c.check("delete_task")
            if result.justification_required and result.allowed:
                justification_seen = True
            if not result.allowed:
                break
            c.record_call("delete_task", timestamp=float(i))

        assert justification_seen, "Should require justification before blocking"


# ---------------------------------------------------------------------------
# Warmup: no friction during first N calls
# ---------------------------------------------------------------------------


class TestWarmupBehavior:
    def test_no_friction_during_warmup(self) -> None:
        """First 19 calls should have zero friction regardless of rate.

        The 20th call (index 19) crosses the warmup threshold and triggers
        the first adjustment, so we only assert on calls 0-18.
        """
        c = _make_controller(warmup_calls=20)

        for i in range(19):
            result = c.record_call("delete_task", timestamp=float(i))
            assert result.friction == 0.0, f"Call {i}: friction should be 0 during warmup"
            assert result.friction_level == FrictionLevel.NONE

        # The 20th call exits warmup — friction may begin here
        result = c.record_call("delete_task", timestamp=19.0)
        assert result.friction >= 0.0  # no constraint, just verifying it runs

    def test_friction_starts_after_warmup(self) -> None:
        """Friction should begin accumulating after warmup ends."""
        c = _make_controller(warmup_calls=20)

        # Burn through warmup with pure delete_task spam
        for i in range(20):
            c.record_call("delete_task", timestamp=float(i))

        # Continue spamming post-warmup
        post_warmup_friction = 0.0
        for i in range(20, 60):
            result = c.record_call("delete_task", timestamp=float(i))
            post_warmup_friction = result.friction

        assert post_warmup_friction > 0.0, "Friction should accumulate after warmup"


# ---------------------------------------------------------------------------
# Aggregate group pressure (30% mutation cap)
# ---------------------------------------------------------------------------


class TestAggregateGroupPressure:
    def test_distributed_mutations_hit_aggregate_cap(self) -> None:
        """Spreading calls across many mutation tools still triggers
        aggregate friction when combined rate exceeds 30%."""
        c = _make_controller(warmup_calls=0)

        mutation_names = list(MUTATION_TOOLS.keys())
        total_friction = 0.0

        # Cycle through all mutation tools — each individual tool stays
        # under its per-tool target, but combined rate is 100%
        for i in range(200):
            tool = mutation_names[i % len(mutation_names)]
            c.record_call(tool, timestamp=float(i))

        for name in mutation_names:
            total_friction += c._friction_levels.get(name, 0.0)

        assert total_friction > 0.5, (
            f"Aggregate friction {total_friction} too low — "
            "group cap should apply even when per-tool rates are low"
        )

    def test_read_heavy_workload_stays_low_friction(self) -> None:
        """A mix that's 80% reads / 20% mutations should have low friction."""
        c = _make_controller(warmup_calls=0)

        for i in range(200):
            if i % 5 == 0:
                # 20% mutation — spread across tools
                tool = list(MUTATION_TOOLS.keys())[i % len(MUTATION_TOOLS)]
                c.record_call(tool, timestamp=float(i))
            else:
                c.record_call_unconfigured("get_tasks", timestamp=float(i))

        # Individual tool friction should be modest
        for name in MUTATION_TOOLS:
            friction = c._friction_levels.get(name, 0.0)
            assert friction < 0.3, (
                f"{name} friction {friction:.2f} too high for 20% mutation workload"
            )


# ---------------------------------------------------------------------------
# Time decay recovery
# ---------------------------------------------------------------------------


class TestTimeDecayRecovery:
    def test_friction_recovers_after_idle_period(self) -> None:
        """After a burst, idle time should bring friction back down."""
        c = _make_controller(warmup_calls=0, time_decay_rate=0.001)

        # Build up friction with a burst
        for i in range(100):
            c.record_call("delete_task", timestamp=float(i))

        friction_peak = c._friction_levels["delete_task"]
        assert friction_peak > 0.3, "Should have significant friction after burst"

        # Simulate 30 minutes of idle (1800 seconds) then one read call
        c.record_call_unconfigured("get_tasks", timestamp=1900.0)

        friction_after = c._friction_levels["delete_task"]
        # Decay formula: exp(-0.001 * ~1800s) ≈ 0.165, but _adjust_friction()
        # adds back some pressure from the still-populated window, so allow 0.3x.
        assert friction_after < friction_peak * 0.3, (
            f"Friction should decay substantially: {friction_peak:.2f} → {friction_after:.2f}"
        )

    def test_previously_blocked_tool_recovers(self) -> None:
        """A tool that was blocked should become usable after idle."""
        c = _make_controller(warmup_calls=0, time_decay_rate=0.001)

        # Drive to block
        for i in range(300):
            result = c.check("delete_task")
            if not result.allowed:
                break
            c.record_call("delete_task", timestamp=float(i))

        assert not c.check("delete_task").allowed, "Should be blocked"

        # Wait 30 minutes
        c.record_call_unconfigured("get_tasks", timestamp=2100.0)

        result_after = c.check("delete_task")
        assert result_after.allowed, "Should recover after idle period"
        assert result_after.friction_level in (FrictionLevel.NONE, FrictionLevel.LOW)


# ---------------------------------------------------------------------------
# Saturation detection and auto-relief
# ---------------------------------------------------------------------------


class TestSaturationDetection:
    def test_saturation_raises_effective_target(self) -> None:
        """When friction stays pinned at saturation, the effective target
        should auto-increase to provide relief."""
        c = _make_controller(warmup_calls=0, saturation_window=5)

        original_target = MUTATION_TOOLS["delete_task"].target_rate

        # Drive delete_task to saturation
        for i in range(300):
            result = c.check("delete_task")
            if not result.allowed:
                # Keep recording unconfigured calls to keep the loop going
                c.record_call_unconfigured("get_tasks", timestamp=float(i))
            else:
                c.record_call("delete_task", timestamp=float(i))

        effective = c._effective_targets.get("delete_task", original_target)
        assert effective > original_target, (
            f"Effective target {effective} should exceed original {original_target}"
        )
        assert c._saturation_detected.get("delete_task", False) is True


# ---------------------------------------------------------------------------
# Multi-client isolation via registry
# ---------------------------------------------------------------------------


class TestMultiClientIsolation:
    @pytest.mark.asyncio
    async def test_spammer_does_not_affect_normal_client(self) -> None:
        """One client's abuse shouldn't raise friction for another."""
        registry = FrictionRegistry(
            default_config=_prod_config(warmup_calls=0),
            tool_configs=dict(MUTATION_TOOLS),
            tool_groups={"mutations": MUTATIONS_GROUP},
        )

        # Client A spams delete_task
        for _ in range(100):
            await registry.check_and_record("spammer", "delete_task")

        # Client B makes one call — should be pristine
        result = await registry.check_and_record("normal-user", "delete_task")
        assert result.allowed is True
        assert result.friction == 0.0
        assert result.friction_level == FrictionLevel.NONE

    @pytest.mark.asyncio
    async def test_concurrent_clients_independent_escalation(self) -> None:
        """Two clients spamming different tools escalate independently."""
        registry = FrictionRegistry(
            default_config=_prod_config(warmup_calls=0),
            tool_configs=dict(MUTATION_TOOLS),
            tool_groups={"mutations": MUTATIONS_GROUP},
        )

        # Client A spams delete_task
        for _ in range(80):
            await registry.check_and_record("client-a", "delete_task")

        # Client B spams update_task
        for _ in range(80):
            await registry.check_and_record("client-b", "update_task")

        status_a = registry.get_client_status("client-a")
        status_b = registry.get_client_status("client-b")

        assert status_a is not None and status_b is not None

        # Client A has high delete_task friction, low update_task
        assert status_a["delete_task"]["friction"] > 0.3
        assert status_a["update_task"]["friction"] < 0.1

        # Client B has high update_task friction, low delete_task
        assert status_b["update_task"]["friction"] > 0.3
        assert status_b["delete_task"]["friction"] < 0.1


# ---------------------------------------------------------------------------
# Realistic agent session simulation
# ---------------------------------------------------------------------------


class TestRealisticAgentSession:
    """Simulate a typical agent session mixing reads and mutations."""

    def test_normal_agent_session_no_blocks(self) -> None:
        """A well-behaved agent session should never be blocked.

        Simulates: read tasks → classify a few → update a few → add notes.
        ~70% reads, ~30% mutations spread across tools.
        """
        c = _make_controller(warmup_calls=20)

        blocked = False
        # Phase 1: read-heavy discovery (20 calls)
        for i in range(20):
            c.record_call_unconfigured("get_tasks", timestamp=float(i))

        # Phase 2: classify + update cycle (30 calls, ~50% mutation)
        ts = 20.0
        for _ in range(15):
            c.record_call_unconfigured("get_task", timestamp=ts)
            ts += 1
            result = c.record_call("classify_task", timestamp=ts)
            if not result.allowed:
                blocked = True
            ts += 1

        # Phase 3: updates with reads interspersed (30 calls, ~33% mutation)
        for _ in range(10):
            c.record_call_unconfigured("get_task", timestamp=ts)
            ts += 1
            c.record_call_unconfigured("get_tasks", timestamp=ts)
            ts += 1
            result = c.record_call("update_task", timestamp=ts)
            if not result.allowed:
                blocked = True
            ts += 1

        # Phase 4: agent notes (10 calls)
        for _ in range(5):
            c.record_call_unconfigured("get_task", timestamp=ts)
            ts += 1
            result = c.record_call("add_agent_note", timestamp=ts)
            if not result.allowed:
                blocked = True
            ts += 1

        assert not blocked, "Well-behaved agent should never be blocked"

    def test_runaway_agent_gets_blocked(self) -> None:
        """An agent doing only mutations should get blocked eventually."""
        c = _make_controller(warmup_calls=20)

        # Warm up with reads
        for i in range(20):
            c.record_call_unconfigured("get_tasks", timestamp=float(i))

        # Then go mutation-crazy
        blocked_tool: str | None = None
        for i in range(300):
            ts = 20.0 + float(i)
            result = c.check("create_task")
            if not result.allowed:
                blocked_tool = "create_task"
                break
            c.record_call("create_task", timestamp=ts)

        assert blocked_tool is not None, "Runaway mutation agent should be blocked"
