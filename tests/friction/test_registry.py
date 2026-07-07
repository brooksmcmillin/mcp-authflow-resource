"""Tests for the per-client friction registry."""

import asyncio

import pytest

from mcp_authflow_resource.friction.models import (
    ControllerConfig,
    ToolFrictionConfig,
)
from mcp_authflow_resource.friction.registry import FrictionRegistry


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


class TestPenaltyPersistence:
    """Accrued friction must survive LRU eviction (CWE-799 bypass fix)."""

    @staticmethod
    def _penalty_registry(**kwargs: object) -> FrictionRegistry:
        params: dict[str, object] = {
            "default_config": ControllerConfig(warmup_calls=0, time_decay_rate=0.0),
            "tool_configs": {"delete_task": ToolFrictionConfig(target_rate=0.05)},
            "max_clients": 1,
            "penalty_min_friction": 0.05,
        }
        params.update(kwargs)
        return FrictionRegistry(**params)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_friction_survives_forced_eviction(self) -> None:
        reg = self._penalty_registry()

        # Client A accrues high friction.
        for _ in range(40):
            await reg.check_and_record("client-a", "delete_task")
        friction_a = reg.get_client_status("client-a")["delete_task"]["friction"]  # type: ignore[index]
        assert friction_a >= 0.05

        # Forcing A's eviction (max_clients=1) then reconnecting must not
        # reset friction — the bypass the issue describes.
        await reg.check_and_record("client-b", "delete_task")
        assert reg.get_client_status("client-a") is None

        await reg.check_and_record("client-a", "delete_task")
        restored = reg.get_client_status("client-a")["delete_task"]["friction"]  # type: ignore[index]
        assert restored >= friction_a

    @pytest.mark.asyncio
    async def test_penalty_store_disabled(self) -> None:
        reg = self._penalty_registry(penalty_ttl=0.0)

        for _ in range(40):
            await reg.check_and_record("client-a", "delete_task")
        await reg.check_and_record("client-b", "delete_task")  # evicts A, no capture
        await reg.check_and_record("client-a", "delete_task")  # fresh controller

        # Only freshly-accrued friction from the reconnect call, not the
        # ~1.0 penalty that would be restored.
        restored = reg.get_client_status("client-a")["delete_task"]["friction"]  # type: ignore[index]
        assert restored < 0.1

    @pytest.mark.asyncio
    async def test_expired_penalty_not_restored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import mcp_authflow_resource.friction.registry as registry_module

        clock = {"now": 1000.0}
        monkeypatch.setattr(registry_module.time, "monotonic", lambda: clock["now"])

        reg = self._penalty_registry(penalty_ttl=10.0)
        for _ in range(40):
            await reg.check_and_record("client-a", "delete_task")

        await reg.check_and_record("client-b", "delete_task")  # captures A at t=1000

        # Advance past the penalty TTL before A reconnects.
        clock["now"] = 1000.0 + 11.0
        await reg.check_and_record("client-a", "delete_task")

        restored = reg.get_client_status("client-a")["delete_task"]["friction"]  # type: ignore[index]
        assert restored < 0.1

    @pytest.mark.asyncio
    async def test_low_friction_not_persisted(self) -> None:
        reg = self._penalty_registry(penalty_min_friction=0.9)

        # A single call keeps friction well below the persist threshold.
        await reg.check_and_record("client-a", "delete_task")
        await reg.check_and_record("client-b", "delete_task")  # evicts A
        await reg.check_and_record("client-a", "delete_task")

        restored = reg.get_client_status("client-a")["delete_task"]["friction"]  # type: ignore[index]
        assert restored < 0.1


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

    @pytest.mark.asyncio
    async def test_get_or_create_returns_matching_lock(self, registry: FrictionRegistry) -> None:
        """The lock is returned atomically with the controller.

        Callers must never re-look up ``self._locks[client_id]`` after the
        controller lookup: a concurrent LRU eviction could delete that entry
        in between, raising a KeyError. The returned lock must be the exact
        lock the registry tracks for the client.
        """
        controller, lock = await registry._get_or_create("client-a")
        assert controller is registry._controllers["client-a"]
        assert lock is registry._locks["client-a"]

        # Existing-client path returns the same pair.
        controller2, lock2 = await registry._get_or_create("client-a")
        assert controller2 is controller
        assert lock2 is lock

    @pytest.mark.asyncio
    async def test_get_or_create_lock_survives_concurrent_eviction(
        self, small_registry: FrictionRegistry
    ) -> None:
        """A returned lock stays valid even if the client is later evicted.

        Reproduces the TOCTOU the issue describes: hold the (controller, lock)
        pair for one client, then force its eviction from another coroutine.
        The held lock must still be usable — it is not looked up again.
        """
        _, lock_a = await small_registry._get_or_create("client-a")
        await small_registry.check_and_record("client-b", "delete_task")

        # Evict client-a by registering a third client (max_clients=2).
        await small_registry.check_and_record("client-c", "delete_task")
        assert small_registry.get_client_status("client-a") is None
        assert "client-a" not in small_registry._locks

        # The previously-returned lock is still a usable asyncio.Lock, so any
        # in-flight caller holding it does not hit a KeyError.
        async with lock_a:
            assert lock_a.locked()


class TestUnconfiguredRecording:
    @pytest.mark.asyncio
    async def test_record_unconfigured(self, registry: FrictionRegistry) -> None:
        await registry.record_unconfigured("client-a", "get_tasks")
        status = registry.get_client_status("client-a")
        assert status is not None
        # delete_task is configured but wasn't called — should show 0 rate
        assert status["delete_task"]["raw_rate"] == 0.0
