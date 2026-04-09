"""Sliding window for tracking tool-use rates."""

from collections import deque
from dataclasses import dataclass


@dataclass
class ToolCallRecord:
    """A single recorded tool call."""

    tool_name: str
    timestamp: float
    cost: float = 0.0


class SlidingWindow:
    """Count-based sliding window tracking tool call distribution.

    Maintains the last ``max_size`` tool calls and computes per-tool
    usage rates as fraction of the window.
    """

    def __init__(self, max_size: int = 100) -> None:
        self.max_size = max_size
        self._records: deque[ToolCallRecord] = deque(maxlen=max_size)
        self._counts: dict[str, int] = {}

    def record(self, tool_name: str, timestamp: float, cost: float = 0.0) -> None:
        """Record a tool call, evicting the oldest if at capacity."""
        if len(self._records) == self.max_size:
            evicted = self._records[0]
            self._counts[evicted.tool_name] -= 1
            if self._counts[evicted.tool_name] == 0:
                del self._counts[evicted.tool_name]

        self._records.append(ToolCallRecord(tool_name, timestamp, cost))
        self._counts[tool_name] = self._counts.get(tool_name, 0) + 1

    def rate(self, tool_name: str) -> float:
        """Get current usage rate for a tool (0.0-1.0)."""
        total = len(self._records)
        if total == 0:
            return 0.0
        return self._counts.get(tool_name, 0) / total

    def count(self, tool_name: str) -> int:
        """Get raw count of a tool in the current window."""
        return self._counts.get(tool_name, 0)

    @property
    def total(self) -> int:
        """Total calls in the window."""
        return len(self._records)

    @property
    def is_full(self) -> bool:
        """Whether the window has reached capacity."""
        return len(self._records) == self.max_size

    def rates(self) -> dict[str, float]:
        """Get usage rates for all tools in the window."""
        total = len(self._records)
        if total == 0:
            return {}
        return {name: count / total for name, count in self._counts.items()}

    def total_cost(self) -> float:
        """Sum of costs for all calls in the window."""
        return sum(r.cost for r in self._records)
