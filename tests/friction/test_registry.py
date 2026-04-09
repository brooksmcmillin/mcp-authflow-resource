"""Tests for the per-client friction registry."""

import asyncio

import pytest

from mcp_resource_framework.friction.models import (
    ControllerConfig,
    ToolFrictionConfig,
)
from mcp_resource_framework.friction.registry import FrictionRegistry


@pytest.fixture
def registry() -> FrictionRegistry:
    return FrictionRegistry(
        default_config=ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.0,
        ),
        tool_configs={
            "delete_task": ToolFrictionConfig(target_rate=0.05),
        },
    )


@pytest.fixture
def small_registry() -> FrictionRegistry:
    """Registry with max_clients=2 for eviction tests."""
    return FrictionRegistry(
        default_config=ControllerConfig(
            warmup_calls=0,
            time_decay_rate=0.0,
        ),
        tool_configs={
            "delete_task": ToolFrictionConfig(target_rate=0.05),
        },
        max_clients=2,
    )


class TestLazyCreation:
    @pytest.mark.asyncio
    async def test_controller_created_on_first_access(self, registry: FrictionRegistry) -> None:
        assert registry.client_count == 0
        await registry.check_and_record("client-a", "delete_task")
        assert registry.client_count == 1

    @pytest.mark.asyncio
    async def test_same_client_reuses_controller(self, registry: FrictionRegistry) -> None:
        await registry.check_and_record("client-a", "delete_task")
        await registry.check_and_record("client-a", "delete_task")
        assert registry.client_count == 1


class TestClientIsolation:
    @pytest.mark.asyncio
    async def test_independent_friction(self, registry: FrictionRegistry) -> None:
        # Client A spams delete_task
        for _ in range(30):
            await registry.check_and_record("client-a", "delete_task")

        # Client B makes one call — should have zero friction
        result = await registry.check_and_record("client-b", "delete_task")
        assert result.allowed is True

        # Client A should have accumulated friction
        status_a = registry.get_client_status("client-a")
        assert status_a is not None
        assert status_a["delete_task"]["friction"] > 0.0


class TestLRUEviction:
    @pytest.mark.asyncio
    async def test_oldest_evicted(self, small_registry: FrictionRegistry) -> None:
        await small_registry.check_and_record("client-a", "delete_task")
        await small_registry.check_and_record("client-b", "delete_task")
        assert small_registry.client_count == 2

        # Third client evicts client-a (oldest)
        await small_registry.check_and_record("client-c", "delete_task")
        assert small_registry.client_count == 2
        assert small_registry.get_client_status("client-a") is None
        assert small_registry.get_client_status("client-b") is not None
        assert small_registry.get_client_status("client-c") is not None

    @pytest.mark.asyncio
    async def test_access_refreshes_lru(self, small_registry: FrictionRegistry) -> None:
        await small_registry.check_and_record("client-a", "delete_task")
        await small_registry.check_and_record("client-b", "delete_task")

        # Touch client-a, making client-b the oldest
        await small_registry.check_and_record("client-a", "delete_task")

        # Third client should evict client-b
        await small_registry.check_and_record("client-c", "delete_task")
        assert small_registry.get_client_status("client-a") is not None
        assert small_registry.get_client_status("client-b") is None


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_same_client(self, registry: FrictionRegistry) -> None:
        """Concurrent calls from same client don't corrupt state."""
        results = await asyncio.gather(
            *[registry.check_and_record("client-a", "delete_task") for _ in range(20)]
        )
        assert all(r.tool_name == "delete_task" for r in results)
        assert registry.client_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_different_clients(self, registry: FrictionRegistry) -> None:
        """Concurrent calls from different clients don't block each other."""
        results = await asyncio.gather(
            *[registry.check_and_record(f"client-{i}", "delete_task") for i in range(10)]
        )
        assert len(results) == 10
        assert registry.client_count == 10


class TestUnconfiguredRecording:
    @pytest.mark.asyncio
    async def test_record_unconfigured(self, registry: FrictionRegistry) -> None:
        await registry.record_unconfigured("client-a", "get_tasks")
        status = registry.get_client_status("client-a")
        assert status is not None
        # delete_task is configured but wasn't called — should show 0 rate
        assert status["delete_task"]["raw_rate"] == 0.0
