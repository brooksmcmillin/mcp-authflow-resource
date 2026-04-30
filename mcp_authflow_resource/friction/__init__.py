"""Dynamic friction control for MCP tool calls.

Adjusts tool-call cost and access based on observed usage rates,
converging toward configured targets via a proportional feedback loop.
"""

from .controller import FrictionController
from .decorator import friction_controlled, init_friction, record_tool_call
from .models import (
    ControllerConfig,
    FrictionLevel,
    FrictionResult,
    ToolFrictionConfig,
    ToolGroupConfig,
)
from .registry import FrictionRegistry
from .window import SlidingWindow

__all__ = [
    "ControllerConfig",
    "FrictionController",
    "FrictionLevel",
    "FrictionRegistry",
    "FrictionResult",
    "SlidingWindow",
    "ToolFrictionConfig",
    "ToolGroupConfig",
    "friction_controlled",
    "init_friction",
    "record_tool_call",
]
