"""Tests for the sliding window rate tracker."""

from mcp_authflow_resource.friction.window import SlidingWindow


class TestSlidingWindow:
    def test_empty_window(self) -> None:
        w = SlidingWindow(max_size=10)
        assert w.total == 0
        assert w.rate("foo") == 0.0
        assert w.count("foo") == 0
        assert not w.is_full

    def test_single_record(self) -> None:
        w = SlidingWindow(max_size=10)
        w.record("foo", 1.0)
        assert w.total == 1
        assert w.rate("foo") == 1.0
        assert w.count("foo") == 1

    def test_rate_calculation(self) -> None:
        w = SlidingWindow(max_size=10)
        w.record("foo", 1.0)
        w.record("bar", 2.0)
        w.record("foo", 3.0)
        assert w.rate("foo") == 2 / 3
        assert w.rate("bar") == 1 / 3

    def test_eviction(self) -> None:
        w = SlidingWindow(max_size=3)
        w.record("foo", 1.0)
        w.record("bar", 2.0)
        w.record("baz", 3.0)
        assert w.is_full

        # Evicts "foo"
        w.record("qux", 4.0)
        assert w.count("foo") == 0
        assert w.rate("foo") == 0.0
        assert w.total == 3

    def test_complete_eviction(self) -> None:
        """Tool fully evicted from counts dict."""
        w = SlidingWindow(max_size=2)
        w.record("foo", 1.0)
        w.record("foo", 2.0)
        w.record("bar", 3.0)  # evicts first foo
        w.record("bar", 4.0)  # evicts second foo
        assert w.count("foo") == 0
        assert "foo" not in w.rates()

    def test_multi_tool_rates(self) -> None:
        w = SlidingWindow(max_size=100)
        for i in range(60):
            w.record("read", float(i))
        for i in range(30):
            w.record("write", float(60 + i))
        for i in range(10):
            w.record("delete", float(90 + i))

        assert w.rate("read") == 0.6
        assert w.rate("write") == 0.3
        assert w.rate("delete") == 0.1

    def test_cost_tracking(self) -> None:
        w = SlidingWindow(max_size=10)
        w.record("foo", 1.0, cost=2.5)
        w.record("bar", 2.0, cost=1.0)
        assert w.total_cost() == 3.5
