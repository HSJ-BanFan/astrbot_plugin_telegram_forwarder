import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def load_qq_group_cache_module(platforms, bot_for_platform):
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "qq_group_cache.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.qq_group_cache"
    package = ModuleType("astrbot_plugin_telegram_forwarder")
    package.__path__ = [str(root)]
    core_pkg = ModuleType("astrbot_plugin_telegram_forwarder.core")
    core_pkg.__path__ = [str(root / "core")]
    senders_pkg = ModuleType("astrbot_plugin_telegram_forwarder.core.senders")
    senders_pkg.__path__ = [str(root / "core" / "senders")]
    qq_runtime = ModuleType("astrbot_plugin_telegram_forwarder.core.senders.qq_runtime")
    qq_runtime.get_platform_instances = lambda _context: platforms
    qq_runtime.get_platform_bot = bot_for_platform

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
        "astrbot_plugin_telegram_forwarder.core.senders": senders_pkg,
        "astrbot_plugin_telegram_forwarder.core.senders.qq_runtime": qq_runtime,
    }
    original = {name: sys.modules.get(name) for name in stubbed}
    # 该模块会留在 sys.modules 里污染 test_web_admin 的真实导入，必须一并还原。
    original[module_name] = sys.modules.get(module_name)
    try:
        sys.modules.update(stubbed)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in original.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class FakeQQClient:
    def __init__(self, groups):
        self.groups = groups
        self.fail = False
        self.calls = 0

    async def call_action(self, action):
        self.calls += 1
        if self.fail:
            raise RuntimeError("napcat offline")
        return {"data": list(self.groups)}


def build_cache(client=None, *, platform_id="aiocqhttp"):
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id=platform_id, name="aiocqhttp")
    )
    platforms = [platform] if client is not None else []
    module = load_qq_group_cache_module(platforms, lambda _platform: client)
    plugin = SimpleNamespace(context=SimpleNamespace())
    return module.QQGroupCache(plugin, ttl_seconds=300, failure_cooldown=0.01)


@pytest.mark.asyncio
async def test_qq_group_cache_keeps_last_known_list_when_refresh_fails():
    client = FakeQQClient(
        [
            {"group_id": "111", "group_name": "群甲", "member_count": 5},
            {"group_id": "222", "group_name": "群乙", "member_count": 6},
        ]
    )
    cache = build_cache(client)

    first = await cache.list_groups([], force=True)
    assert first["available"] is True
    assert [item["group_id"] for item in first["groups"]] == ["111", "222"]

    client.fail = True
    second = await cache.list_groups([], force=True)

    assert second["available"] is False
    assert [item["group_id"] for item in second["groups"]] == ["111", "222"]
    assert all(item["source"] == "cached" for item in second["groups"])
    assert "last known" in second["message"]
    assert second["groups"][0]["group_name"] == "群甲"


@pytest.mark.asyncio
async def test_qq_group_cache_recovers_live_list_after_failure():
    client = FakeQQClient([{"group_id": "111", "group_name": "群甲"}])
    cache = build_cache(client)

    await cache.list_groups([], force=True)
    client.fail = True
    await cache.list_groups([], force=True)

    client.fail = False
    client.groups = [{"group_id": "333", "group_name": "群丙"}]
    recovered = await cache.list_groups([], force=True)

    assert recovered["available"] is True
    assert recovered["message"] == ""
    assert [item["group_id"] for item in recovered["groups"]] == ["333"]
    assert recovered["groups"][0]["source"] == "live"


@pytest.mark.asyncio
async def test_qq_group_cache_failure_without_cache_reports_plain_message():
    cache = build_cache(None)

    result = await cache.list_groups([], force=True)

    assert result["available"] is False
    assert result["groups"] == []
    assert result["message"] == "QQ platform is unavailable."


@pytest.mark.asyncio
async def test_qq_group_cache_failure_still_merges_configured_groups():
    client = FakeQQClient([{"group_id": "111", "group_name": "群甲"}])
    cache = build_cache(client)

    await cache.list_groups([], force=True)
    client.fail = True
    result = await cache.list_groups(["999"], force=True)

    by_id = {item["group_id"]: item for item in result["groups"]}
    assert by_id["111"]["source"] == "cached"
    assert by_id["999"]["source"] == "configured"
