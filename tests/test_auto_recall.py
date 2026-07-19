import asyncio
import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _snapshot_modules(*names: str) -> dict[str, ModuleType | None]:
    return {name: sys.modules.get(name) for name in names}


def _restore_modules(snapshot: dict[str, ModuleType | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


def load_auto_recall_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "senders" / "auto_recall.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.senders.auto_recall"
    snapshot = _snapshot_modules("astrbot", "astrbot.api", module_name)
    try:
        sys.modules["astrbot"] = MagicMock()
        api_module = ModuleType("astrbot.api")
        api_module.logger = MagicMock()
        sys.modules["astrbot.api"] = api_module

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        # dataclass 在类定义时会查 sys.modules[cls.__module__]
        sys.modules[module_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        _restore_modules(snapshot)


def load_storage_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "common" / "storage.py"
    snapshot = _snapshot_modules("astrbot", "astrbot.api")
    try:
        sys.modules["astrbot"] = MagicMock()
        api_module = ModuleType("astrbot.api")
        api_module.logger = MagicMock()
        sys.modules["astrbot.api"] = api_module

        spec = importlib.util.spec_from_file_location(
            "astrbot_plugin_telegram_forwarder.common.storage_auto_recall",
            module_path,
        )
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        _restore_modules(snapshot)


def make_test_dir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"auto-recall-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_extract_message_ids_from_common_onebot_shapes():
    module = load_auto_recall_module()
    assert module.extract_message_ids({"data": {"message_id": 42}}) == [42]
    assert module.extract_message_ids({"message_id": "1001"}) == [1001]
    assert module.extract_message_ids({"data": {"message_ids": [1, 2, 2, 0]}}) == [1, 2]
    assert module.extract_message_ids(None) == []
    assert module.extract_message_ids(SimpleNamespace(message_id=9)) == [9]


def test_auto_recall_default_window_is_120_and_disabled_by_default():
    module = load_auto_recall_module()
    manager = module.AutoRecallManager(config={"forward_config": {}})
    assert manager.is_enabled() is False
    assert manager.window_seconds() == 120

    manager.reload_config(
        {
            "forward_config": {
                "auto_recall_enabled": True,
                "auto_recall_window_seconds": 90,
            }
        }
    )
    assert manager.is_enabled() is True
    assert manager.window_seconds() == 90


def test_schedule_persists_queue_and_process_due_deletes_qq_message():
    async def _run():
        module = load_auto_recall_module()
        storage_module = load_storage_module()
        tmp_dir = make_test_dir()
        data_path = tmp_dir / "data.json"
        try:
            storage = storage_module.Storage(data_path)
            bot = MagicMock()
            bot.call_action = AsyncMock(return_value={"status": "ok"})

            manager = module.AutoRecallManager(
                config={
                    "forward_config": {
                        "auto_recall_enabled": True,
                        "auto_recall_window_seconds": 120,
                    }
                },
                storage=storage,
                get_qq_bot=lambda: bot,
            )

            item = manager.schedule(
                platform="qq",
                message_ids=[12345],
                target="aiocqhttp:GroupMessage:10001",
                source_channel="demo",
                window_seconds=1,
                now_ts=1_000.0,
            )
            assert item is not None
            assert item.recall_at == 1_001.0
            assert manager.pending_count() == 1

            reloaded = storage_module.Storage(data_path)
            queue = reloaded.get_auto_recall_queue()
            assert len(queue) == 1
            assert queue[0]["message_ids"] == [12345]

            # not due yet
            stats_early = await manager.process_due(now_ts=1_000.5)
            assert stats_early["due"] == 0
            assert bot.call_action.await_count == 0

            stats = await manager.process_due(now_ts=1_001.0)
            assert stats["due"] == 1
            assert stats["success"] == 1
            bot.call_action.assert_awaited_once_with("delete_msg", message_id=12345)
            assert manager.pending_count() == 0

            reloaded2 = storage_module.Storage(data_path)
            assert reloaded2.get_auto_recall_queue() == []
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    asyncio.run(_run())


def test_schedule_skips_when_disabled_or_no_ids():
    module = load_auto_recall_module()
    manager = module.AutoRecallManager(
        config={"forward_config": {"auto_recall_enabled": False}}
    )
    assert manager.schedule(platform="qq", message_ids=[1]) is None

    manager.reload_config({"forward_config": {"auto_recall_enabled": True}})
    assert manager.schedule(platform="qq", message_ids=[]) is None
    assert manager.schedule(platform="qq", message_ids=["bad"]) is None


def test_capture_bot_message_ids_wraps_send_methods():
    async def _run():
        module = load_auto_recall_module()

        class FakeBot:
            async def send_group_msg(self, **kwargs):
                return {"data": {"message_id": 777}}

            async def call_action(self, action, **kwargs):
                return {"message_id": 888}

        bot = FakeBot()
        async with module.capture_bot_message_ids(bot) as captured:
            result = await bot.send_group_msg(group_id=1, message="hi")
            assert result["data"]["message_id"] == 777
            await bot.call_action("send_group_forward_msg", group_id=1)
        assert captured == [777, 888]

        # wrappers restored
        result2 = await bot.send_group_msg(group_id=1, message="hi")
        assert result2["data"]["message_id"] == 777

    asyncio.run(_run())


def test_storage_auto_recall_queue_roundtrip():
    storage_module = load_storage_module()
    tmp_dir = make_test_dir()
    data_path = tmp_dir / "data.json"
    try:
        storage = storage_module.Storage(data_path)
        storage.set_auto_recall_queue(
            [
                {
                    "platform": "qq",
                    "message_ids": [1],
                    "target": "x",
                    "recall_at": 10,
                    "created_at": 1,
                }
            ]
        )
        raw = json.loads(data_path.read_text(encoding="utf-8"))
        assert "auto_recall_queue" in raw
        reloaded = storage_module.Storage(data_path)
        assert reloaded.get_auto_recall_queue()[0]["message_ids"] == [1]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_telegram_recall_uses_delete_messages():
    async def _run():
        module = load_auto_recall_module()
        client = MagicMock()
        client.delete_messages = AsyncMock(return_value=True)
        manager = module.AutoRecallManager(
            config={
                "forward_config": {
                    "auto_recall_enabled": True,
                    "auto_recall_window_seconds": 120,
                }
            },
            get_tg_client=lambda: client,
        )
        manager.schedule(
            platform="telegram",
            message_ids=[11, 12],
            target="-100123",
            window_seconds=1,
            now_ts=100.0,
        )
        stats = await manager.process_due(now_ts=101.0)
        assert stats["success"] == 1
        client.delete_messages.assert_awaited_once_with(-100123, [11, 12])

    asyncio.run(_run())
