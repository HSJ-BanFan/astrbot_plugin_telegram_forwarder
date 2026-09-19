from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


def load_platform_directory_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "platform_directory.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.platform_directory"
    package = ModuleType("astrbot_plugin_telegram_forwarder")
    package.__path__ = [str(root)]
    core_pkg = ModuleType("astrbot_plugin_telegram_forwarder.core")
    core_pkg.__path__ = [str(root / "core")]

    stubbed = {
        "astrbot": ModuleType("astrbot"),
        "astrbot.api": SimpleNamespace(
            logger=SimpleNamespace(
                warning=lambda *a, **k: None,
                debug=lambda *a, **k: None,
                info=lambda *a, **k: None,
            )
        ),
        "astrbot_plugin_telegram_forwarder": package,
        "astrbot_plugin_telegram_forwarder.core": core_pkg,
    }
    with patch.dict(sys.modules, stubbed):
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


mod = load_platform_directory_module()
PlatformDirectory = mod.PlatformDirectory
BaseDirectoryAdapter = mod.BaseDirectoryAdapter
DirectoryFetchResult = mod.DirectoryFetchResult
DirectoryAdapter = mod.DirectoryAdapter


class MockDirectoryAdapter(BaseDirectoryAdapter):
    def __init__(
        self,
        items: list[dict] | None = None,
        *,
        fail: bool = False,
        delay: float = 0.0,
        partial: bool = False,
        message: str = "",
        error: Exception | None = None,
    ):
        self.items = items or []
        self.fail = fail
        self.delay = delay
        self.partial = partial
        self.message = message
        self.error = error
        self.fetch_calls = 0
        self.last_configured_ids: list[str] | None = None

    async def fetch_items(
        self, configured_ids: list[str] | None = None
    ) -> DirectoryFetchResult:
        self.fetch_calls += 1
        self.last_configured_ids = configured_ids
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.fail:
            return DirectoryFetchResult(
                items=[],
                available=False,
                partial=False,
                message=self.message or "Fetch failed",
            )
        return DirectoryFetchResult(
            items=[dict(item) for item in self.items],
            available=True,
            partial=self.partial,
            message=self.message,
        )

    def get_item_id(self, item: dict) -> str:
        return str(item.get("id", "") or "").strip()

    def get_item_aliases(self, item: dict) -> list[str]:
        aliases = [self.get_item_id(item)]
        if "alias" in item:
            aliases.append(str(item["alias"]))
        return [a for a in aliases if a]

    def create_fallback(self, target_id: str) -> dict | None:
        if not target_id:
            return None
        return {
            "id": target_id,
            "name": f"Item {target_id}",
            "source": "configured",
        }

    def sort_items(self, items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda x: str(x.get("id", "")))


@pytest.mark.asyncio
async def test_concurrency_locking_prevents_thundering_herd():
    adapter = MockDirectoryAdapter(
        items=[{"id": "1", "name": "Item 1", "source": "live"}],
        delay=0.05,
    )
    directory = PlatformDirectory(adapter, ttl_seconds=60)

    results = await asyncio.gather(*(directory.list_items() for _ in range(10)))

    assert adapter.fetch_calls == 1
    assert len(results) == 10
    for res in results:
        assert res["available"] is True
        assert len(res["items"]) == 1
        assert res["items"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_ttl_expiration_and_cache_hit():
    adapter = MockDirectoryAdapter(
        items=[{"id": "1", "name": "Live 1", "source": "live"}]
    )
    directory = PlatformDirectory(adapter, ttl_seconds=60)

    first = await directory.list_items()
    assert adapter.fetch_calls == 1
    assert first["items"][0]["id"] == "1"

    second = await directory.list_items()
    assert adapter.fetch_calls == 1
    assert second["items"][0]["id"] == "1"

    # Simulate TTL expiry
    directory._last_refresh_at -= 61.0
    third = await directory.list_items()
    assert adapter.fetch_calls == 2
    assert third["items"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_partial_ttl_expiration():
    adapter = MockDirectoryAdapter(
        items=[{"id": "1", "source": "live"}],
        partial=True,
    )
    directory = PlatformDirectory(
        adapter, ttl_seconds=3600, partial_ttl_seconds=100.0
    )

    result = await directory.list_items()
    assert result["available"] is True
    assert result["partial"] is True
    assert directory._is_fresh() is True

    # 150 seconds past partial TTL -> should be stale
    directory._last_refresh_at -= 150.0
    assert directory._is_fresh() is False

    # But if partial was False, 150 seconds would still be fresh under 3600s TTL
    directory._partial = False
    assert directory._is_fresh() is True


@pytest.mark.asyncio
async def test_failure_cooldown_and_graceful_cached_fallback():
    adapter = MockDirectoryAdapter(
        items=[{"id": "1", "name": "Alpha", "source": "live"}]
    )
    directory = PlatformDirectory(
        adapter,
        ttl_seconds=300,
        failure_cooldown=20.0,
        item_name="items",
    )

    first = await directory.list_items(force=True)
    assert first["available"] is True
    assert first["items"][0]["source"] == "live"
    assert adapter.fetch_calls == 1

    # Adapter starts failing
    adapter.fail = True
    adapter.message = "Network down"
    second = await directory.list_items(force=True)
    assert adapter.fetch_calls == 2
    assert second["available"] is False
    assert "last known" in second["message"]
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] == "1"
    assert second["items"][0]["source"] == "cached"

    # During 20s cooldown, list_items(force=False) will NOT trigger probe
    third = await directory.list_items(force=False)
    assert adapter.fetch_calls == 2
    assert third["available"] is False
    assert third["items"][0]["source"] == "cached"

    # Advance past 20s cooldown and recover
    directory._last_failure_at -= 25.0
    adapter.fail = False
    adapter.message = ""
    adapter.items = [{"id": "2", "name": "Beta", "source": "live"}]
    recovered = await directory.list_items()
    assert adapter.fetch_calls == 3
    assert recovered["available"] is True
    assert recovered["message"] == ""
    assert len(recovered["items"]) == 1
    assert recovered["items"][0]["id"] == "2"
    assert recovered["items"][0]["source"] == "live"


@pytest.mark.asyncio
async def test_failure_on_cold_start_reports_failure_and_merges_configured():
    adapter = MockDirectoryAdapter(fail=True, message="Down from start")
    directory = PlatformDirectory(adapter, failure_cooldown=20.0)

    result = await directory.list_items(configured_ids=["99"])
    assert result["available"] is False
    assert result["message"] == "Down from start"
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "99"
    assert result["items"][0]["source"] == "configured"


@pytest.mark.asyncio
async def test_merging_unindexed_configured_target_ids():
    adapter = MockDirectoryAdapter(
        items=[
            {"id": "10", "alias": "alias-10", "name": "Live 10", "source": "live"}
        ]
    )
    directory = PlatformDirectory(adapter)

    result = await directory.list_items(
        configured_ids=["10", "alias-10", "20", "30"]
    )
    assert result["available"] is True
    items_by_id = {item["id"]: item for item in result["items"]}

    assert len(items_by_id) == 3
    assert items_by_id["10"]["source"] == "live"
    assert items_by_id["20"]["source"] == "configured"
    assert items_by_id["30"]["source"] == "configured"
    assert "alias-10" not in items_by_id


@pytest.mark.asyncio
async def test_warm_up_idempotency_and_force():
    adapter = MockDirectoryAdapter(
        items=[{"id": "1", "name": "Live", "source": "live"}]
    )
    directory = PlatformDirectory(adapter, ttl_seconds=300)

    # First warm up
    await directory.warm_up()
    assert adapter.fetch_calls == 1
    assert directory._available is True

    # Second warm up without force is a no-op
    await directory.warm_up(force=False)
    assert adapter.fetch_calls == 1

    # Forced warm up refreshes
    await directory.warm_up(force=True)
    assert adapter.fetch_calls == 2


@pytest.mark.asyncio
async def test_invalidate_clears_cache_and_freshness():
    adapter = MockDirectoryAdapter(
        items=[{"id": "1", "name": "Live", "source": "live"}]
    )
    directory = PlatformDirectory(adapter, ttl_seconds=300)

    await directory.warm_up()
    assert directory._available is True
    assert len(directory._items) == 1

    directory.invalidate()
    assert directory._available is False
    assert directory._items == []
    assert directory._last_refresh_at == 0.0
    assert directory._last_failure_at == 0.0
    assert directory._is_fresh() is False

    # Calling list_items after invalidation triggers fresh fetch
    result = await directory.list_items()
    assert adapter.fetch_calls == 2
    assert result["available"] is True
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_adapter_exception_resilience():
    adapter = MockDirectoryAdapter(error=RuntimeError("Connection exploded"))
    directory = PlatformDirectory(adapter)

    result = await directory.list_items()
    assert result["available"] is False
    assert "Platform fetch failed: Connection exploded" in result["message"]
    assert result["items"] == []


def test_directory_adapter_protocol_compliance():
    adapter = MockDirectoryAdapter()
    assert isinstance(adapter, DirectoryAdapter)
