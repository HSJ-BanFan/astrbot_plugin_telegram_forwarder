import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def load_tg_channel_cache_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "tg_channel_cache.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.tg_channel_cache"
    package = ModuleType("astrbot_plugin_telegram_forwarder")
    package.__path__ = [str(root)]
    core_pkg = ModuleType("astrbot_plugin_telegram_forwarder.core")
    core_pkg.__path__ = [str(root / "core")]
    common_pkg = ModuleType("astrbot_plugin_telegram_forwarder.common")
    common_pkg.__path__ = [str(root / "common")]
    text_tools = ModuleType("astrbot_plugin_telegram_forwarder.common.text_tools")

    def normalize_telegram_channel_name(value):
        return str(value or "").strip().lstrip("@")

    text_tools.normalize_telegram_channel_name = normalize_telegram_channel_name
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
        "astrbot_plugin_telegram_forwarder.common": common_pkg,
        "astrbot_plugin_telegram_forwarder.common.text_tools": text_tools,
    }
    original = {name: sys.modules.get(name) for name in stubbed}
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


class SlowDialogClient:
    def __init__(self, count=5, delay=0.0, entities=None):
        self.count = count
        self.delay = delay
        self.seen = 0
        self.entities = entities or {}

    def is_connected(self):
        return True

    async def is_user_authorized(self):
        return True

    async def get_entity(self, ref):
        await asyncio.sleep(0)
        if ref not in self.entities:
            raise ValueError(f"missing {ref}")
        return self.entities[ref]

    def get_dialogs(self, limit=None):
        async def _load():
            out = []
            n = self.count if limit is None else min(self.count, int(limit))
            for i in range(n):
                self.seen += 1
                if self.delay:
                    await asyncio.sleep(self.delay)
                entity = SimpleNamespace(
                    id=1000 + i,
                    username=f"ch{i}",
                    title=f"Channel {i}",
                    megagroup=False,
                    broadcast=True,
                    participants_count=1,
                )
                out.append(
                    SimpleNamespace(
                        title=f"Channel {i}",
                        entity=entity,
                        is_channel=True,
                        is_user=False,
                    )
                )
            return out

        return _load()


@pytest.mark.asyncio
async def test_tg_channel_cache_caps_dialog_scan():
    module = load_tg_channel_cache_module()
    client = SlowDialogClient(count=20, delay=0.0)
    plugin = SimpleNamespace(
        client_wrapper=SimpleNamespace(client=client, is_authorized=lambda: True)
    )
    cache = module.TGChannelCache(
        plugin, ttl_seconds=30, max_dialogs=5, refresh_timeout=5.0
    )
    result = await cache.list_channels([])
    assert result["available"] is True
    assert client.seen == 5
    assert len(result["channels"]) == 5


@pytest.mark.asyncio
async def test_tg_channel_cache_resolves_configured_when_dialogs_timeout():
    module = load_tg_channel_cache_module()
    entity = SimpleNamespace(
        id=42,
        username="wtmsd",
        title="无聊的备忘录",
        megagroup=False,
        broadcast=True,
        participants_count=10,
    )
    client = SlowDialogClient(
        count=50,
        delay=0.2,
        entities={"wtmsd": entity, "acg_meme": SimpleNamespace(
            id=7, username="acg_meme", title="ACG", megagroup=False, broadcast=True, participants_count=1
        )},
    )
    plugin = SimpleNamespace(
        client_wrapper=SimpleNamespace(client=client, is_authorized=lambda: True)
    )
    cache = module.TGChannelCache(
        plugin, ttl_seconds=30, max_dialogs=50, refresh_timeout=0.05, failure_cooldown=1.0
    )
    result = await cache.list_channels(["wtmsd", "acg_meme"])
    assert result["available"] is True
    refs = {ch["channel_ref"]: ch for ch in result["channels"]}
    assert "wtmsd" in refs
    assert refs["wtmsd"]["title"] == "无聊的备忘录"
    assert refs["wtmsd"]["source"] == "resolved"


@pytest.mark.asyncio
async def test_tg_channel_cache_failure_uses_short_cooldown():
    module = load_tg_channel_cache_module()
    cache = module.TGChannelCache(
        SimpleNamespace(client_wrapper=None),
        ttl_seconds=90,
        failure_cooldown=1.0,
    )
    cache._available = False
    cache._channels = []
    cache._last_failure_at = time.time()
    cache._last_refresh_at = cache._last_failure_at
    assert cache._is_fresh() is True
    cache._last_failure_at -= 1.5
    cache._last_refresh_at = cache._last_failure_at
    assert cache._is_fresh() is False
