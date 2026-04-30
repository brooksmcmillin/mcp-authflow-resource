"""Data models for the friction controller."""

from dataclasses import dataclass, field
from enum import Enum, auto


class FrictionLevel(Enum):
    """Qualitative friction level for display/logging."""

    NONE = auto()
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    BLOCKED = auto()


@dataclass(frozen=True)
class FrictionResult:
    """Result of a friction check for a specific tool call."""

    tool_name: str
    allowed: bool
    cost: float
    friction_level: FrictionLevel
    current_rate: float
    target_rate: float
    justification_required: bool
    message: str
    friction: float = 0.0
    effective_target: float = 0.0
    saturation_detected: bool = False

    @property
    def over_target(self) -> bool:
        return self.current_rate > self.target_rate


@dataclass
class ToolGroupConfig:
    """Configuration for an aggregate rate budget across a group of tools.

    Constrains the combined rate of all tools in the group, preventing
    displacement (whack-a-mole) when shifting usage between tools.

    Args:
        tools: Tool names belonging to this group.
        aggregate_target: Desired combined usage rate (0.0-1.0).
        pressure_weight: How aggressively aggregate overshoot adds friction.
    """

    tools: list[str] = field(default_factory=list)
    aggregate_target: float = 0.20
    pressure_weight: float = 1.0


@dataclass
class ToolFrictionConfig:
    """Configuration for friction on a specific tool.

    Args:
        target_rate: Desired usage rate as fraction of total calls (0.0-1.0).
        min_cost: Floor for dynamic cost (friction=0).
        max_cost: Ceiling for dynamic cost (friction=1).
        justification_threshold: Friction level triggering justification.
        hard_block_threshold: Friction level blocking calls outright.
    """

    target_rate: float = 0.05
    min_cost: float = 0.5
    max_cost: float = 10.0
    justification_threshold: float = 0.6
    hard_block_threshold: float = 0.95


@dataclass
class ControllerConfig:
    """Global configuration for the FrictionController.

    Args:
        window_size: Number of recent calls tracked in the sliding window.
        ema_alpha: Smoothing factor for EMA rate (higher = more responsive).
        adjustment_rate: How aggressively friction changes per step.
        asymmetric_decay: Multiplier for downward friction adjustment.
        dead_zone: Half-width of the no-adjustment band around target_rate.
        warmup_calls: Number of initial calls before adjustment begins.
        time_decay_rate: Exponential time-based friction decay rate.
            Half-life = ln(2) / time_decay_rate. Default 0.001 (~11.5 min).
        default_budget: Starting budget for tool-use cost tracking.
            Defaults to inf (disabled). Set to a finite value to enable.
        saturation_threshold: Friction level considered "saturated".
        saturation_window: Consecutive saturated calls before relief.
            Set to 0 to disable.
        saturation_relief_rate: Target increase per saturated call.
    """

    window_size: int = 100
    ema_alpha: float = 0.15
    adjustment_rate: float = 0.08
    asymmetric_decay: float = 2.0
    dead_zone: float = 0.01
    warmup_calls: int = 20
    time_decay_rate: float = 0.001
    default_budget: float = float("inf")
    tool_configs: dict[str, ToolFrictionConfig] = field(default_factory=dict)
    tool_groups: dict[str, ToolGroupConfig] = field(default_factory=dict)
    saturation_threshold: float = 0.9
    saturation_window: int = 0
    saturation_relief_rate: float = 0.005
