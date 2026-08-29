"""自动撤回生命周期的行为测试。"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from astrbot_plugin_telegram_forwarder.core.recall import (
    RecallRegistry,
    SendReceipt,
    TerminalRecallError,
    TransientRecallError,
    extract_message_ids,
)


def make_receipt() -> SendReceipt:
    return SendReceipt(
        platform="telegram",
        target_session="target",
        message_id="42",
        source_channel="source",
        sent_at=datetime.now(timezone.utc),
        kind="forward",
    )


@pytest.mark.asyncio
async def test_registry_runs_recall_and_removes_completed_task():
    registry = RecallRegistry(max_pending=2)
    callback = AsyncMock()
    receipt = make_receipt()

    task = registry.schedule(0, callback, receipt)

    assert task is not None
    await task
    await asyncio.sleep(0)

    callback.assert_awaited_once_with(receipt)
    assert registry.tasks == set()


@pytest.mark.asyncio
async def test_registry_rejects_receipts_at_capacity_without_leaking_coroutine():
    registry = RecallRegistry(max_pending=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(_receipt):
        started.set()
        await release.wait()

    first = registry.schedule(0, wait_for_release, make_receipt())
    assert first is not None
    await started.wait()

    second = registry.schedule(0, AsyncMock(), make_receipt())

    assert second is None
    release.set()
    await first
    await asyncio.sleep(0)
    assert registry.tasks == set()


@pytest.mark.asyncio
async def test_registry_retry_once_retries_a_transient_recall_error(monkeypatch):
    registry = RecallRegistry(max_pending=2, on_error="retry_once")
    real_sleep = asyncio.sleep
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = 0

    async def flaky_recall(_receipt):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientRecallError("temporary network failure")

    task = registry.schedule(0, flaky_recall, make_receipt())
    assert task is not None
    await task

    assert calls == 2
    assert sleeps == [0, 1.0]


@pytest.mark.asyncio
async def test_registry_does_not_retry_terminal_recall_error():
    registry = RecallRegistry(max_pending=2, on_error="retry_once")
    callback = AsyncMock(side_effect=TerminalRecallError("already deleted"))

    task = registry.schedule(0, callback, make_receipt())
    assert task is not None
    await task

    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_registry_does_not_retry_unknown_error():
    registry = RecallRegistry(max_pending=2, on_error="retry_once")
    callback = AsyncMock(side_effect=RuntimeError("programming error"))

    task = registry.schedule(0, callback, make_receipt())
    assert task is not None
    await task

    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_registry_close_is_idempotent_and_cancels_pending_tasks():
    registry = RecallRegistry(max_pending=2)
    task = registry.schedule(60, AsyncMock(), make_receipt())
    assert task is not None

    await registry.close()
    await registry.close()

    assert task.cancelled()
    assert registry.tasks == set()


@pytest.mark.asyncio
async def test_registry_reconfigure_updates_policy_without_dropping_pending_tasks():
    registry = RecallRegistry(max_pending=1, on_error="log")
    first = registry.schedule(60, AsyncMock(), make_receipt())
    assert first is not None

    registry.reconfigure_from_config(
        {
            "forward_config": {
                "auto_recall": {
                    "max_pending": 3,
                    "on_error": "retry_once",
                }
            }
        }
    )

    assert registry.pending_count == 1
    assert registry.max_pending == 3
    assert registry.on_error == "retry_once"
    assert first in registry.tasks
    second = registry.schedule(60, AsyncMock(), make_receipt())
    assert second is not None
    await registry.close()


def test_send_receipt_is_frozen_and_keeps_platform_scope():
    receipt = make_receipt()

    assert receipt.platform == "telegram"
    assert receipt.message_id == "42"
    with pytest.raises((AttributeError, TypeError)):
        receipt.message_id = "43"


def test_registry_normalizes_removed_keep_policy_to_log():
    assert RecallRegistry(on_error="keep").on_error == "log"


def test_extract_message_ids_rejects_non_numeric_metadata():
    assert extract_message_ids({"data": {"message_id": "not-a-message-id"}}) == []
    assert (
        extract_message_ids(
            [{"message_id": 0}, {"message_id": -1}, {"message_id": "0"}, {"id": "-2"}]
        )
        == []
    )
    assert extract_message_ids([SimpleNamespace(id=42), {"message_id": "43"}]) == [
        "42",
        "43",
    ]


@pytest.mark.asyncio
async def test_telegram_sender_schedules_each_forwarded_message_for_recall():
    from astrbot_plugin_telegram_forwarder.core.senders.telegram import TelegramSender

    entity = object()
    client = SimpleNamespace(
        get_entity=AsyncMock(return_value=entity),
        forward_messages=AsyncMock(
            return_value=[SimpleNamespace(id=42), None, SimpleNamespace(id=43)]
        ),
        delete_messages=AsyncMock(),
    )
    registry = RecallRegistry(max_pending=4)
    sender = TelegramSender(
        client,
        {
            "target_channel": "target",
            "forward_config": {
                "auto_recall": {
                    "telegram_enabled": True,
                    "telegram_delay_seconds": 0,
                }
            },
        },
        recall_registry=registry,
    )

    await sender.send([[SimpleNamespace(id=1)]], "source")

    assert registry.pending_count == 2
    tasks = tuple(registry.tasks)
    await asyncio.gather(*tasks)
    client.delete_messages.assert_any_await(entity, [42], revoke=True)
    client.delete_messages.assert_any_await(entity, [43], revoke=True)
    assert client.delete_messages.await_count == 2


@pytest.mark.asyncio
async def test_telegram_sender_preserves_non_numeric_negative_target_name():
    from astrbot_plugin_telegram_forwarder.core.senders.telegram import TelegramSender

    entity = object()
    client = SimpleNamespace(
        get_entity=AsyncMock(return_value=entity),
        forward_messages=AsyncMock(return_value=[]),
    )
    sender = TelegramSender(
        client,
        {"target_channel": "-channel-name"},
        recall_registry=RecallRegistry(max_pending=1),
    )

    await sender.send([[SimpleNamespace(id=1)]], "source")

    client.get_entity.assert_awaited_once_with("-channel-name")
    client.forward_messages.assert_awaited_once_with(entity, [SimpleNamespace(id=1)])


@pytest.mark.asyncio
async def test_qq_sender_captures_onebot_receipt_and_recalls_it():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    registry = RecallRegistry(max_pending=2)
    context = SimpleNamespace(send_message=AsyncMock())
    config = {
        "forward_config": {
            "auto_recall": {
                "qq_enabled": True,
                "qq_delay_seconds": 0,
            }
        }
    }
    sender = qq_module.QQSender(
        context=context,
        config=config,
        downloader=SimpleNamespace(),
        recall_registry=registry,
    )

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def call_action(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "send_group_msg":
                return {"data": {"message_id": 99}}
            return {}

    bot = FakeBot()
    sender.bot = bot
    sender.platform_id = "aiocqhttp"
    chain = qq_module.MessageChain([qq_module.Plain("hello")])

    await sender._send_with_timeout(
        "aiocqhttp:GroupMessage:123",
        chain,
        send_kind="plain",
        source_channel="source",
    )

    assert bot.calls[0] == (
        "send_group_msg",
        {"group_id": 123, "message": [{"type": "text", "data": {"text": "hello"}}]},
    )
    assert context.send_message.await_count == 0
    assert registry.pending_count == 1

    await next(iter(registry.tasks))
    assert bot.calls[1] == ("delete_msg", {"message_id": 99})


@pytest.mark.asyncio
async def test_qq_onebot_returns_empty_receipt_after_success_without_message_id():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    fallback_send = AsyncMock(return_value={"message_id": 101})

    class FakeBot:
        async def call_action(self, _action, **_kwargs):
            return {"status": "ok"}

    adapter = qq_module.QQOneBotAdapter(FakeBot(), {}, None)
    chain = qq_module.MessageChain([qq_module.Plain("hello")])

    result = await adapter.send(
        "aiocqhttp:GroupMessage:123",
        chain,
        target_session="aiocqhttp:GroupMessage:123",
        source_channel="source",
        kind="plain",
        fallback_send=fallback_send,
    )

    assert result == []
    fallback_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_qq_recall_maps_transport_errors_to_transient():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()

    class FakeBot:
        async def call_action(self, _action, **_kwargs):
            raise TimeoutError("OneBot request timed out")

    adapter = qq_module.QQOneBotAdapter(FakeBot(), {}, None)
    with pytest.raises(TransientRecallError):
        await adapter.recall(make_receipt())


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        ({"status": "failed", "retcode": 1200}, TransientRecallError),
        ({"status": "failed", "message": "message not found"}, TerminalRecallError),
        ({"status": "failed", "message": "request timeout"}, TransientRecallError),
    ],
)
@pytest.mark.asyncio
async def test_qq_recall_classifies_onebot_failure_response(response, error_type):
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()

    class FakeBot:
        async def call_action(self, _action, **_kwargs):
            return response

    adapter = qq_module.QQOneBotAdapter(FakeBot(), {}, None)
    with pytest.raises(error_type):
        await adapter.recall(make_receipt())


@pytest.mark.asyncio
async def test_qq_sender_falls_back_to_context_when_onebot_is_unavailable():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    context = SimpleNamespace(send_message=AsyncMock())
    sender = qq_module.QQSender(
        context=context, config={}, downloader=SimpleNamespace()
    )
    chain = qq_module.MessageChain([qq_module.Plain("hello")])

    await sender._send_with_timeout(
        "aiocqhttp:GroupMessage:123",
        chain,
        send_kind="plain",
        source_channel="source",
    )

    context.send_message.assert_awaited_once_with(
        "aiocqhttp:GroupMessage:123",
        chain,
    )


@pytest.mark.asyncio
async def test_qq_sender_falls_back_to_context_when_onebot_action_fails():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    context = SimpleNamespace(send_message=AsyncMock())
    sender = qq_module.QQSender(
        context=context, config={}, downloader=SimpleNamespace()
    )

    class FailingBot:
        async def call_action(self, _action, **_kwargs):
            raise RuntimeError("OneBot is not ready")

    sender.bot = FailingBot()
    chain = qq_module.MessageChain([qq_module.Plain("hello")])

    await sender._send_with_timeout(
        "aiocqhttp:GroupMessage:123",
        chain,
        send_kind="plain",
        source_channel="source",
    )

    context.send_message.assert_awaited_once_with(
        "aiocqhttp:GroupMessage:123",
        chain,
    )


@pytest.mark.asyncio
async def test_qq_onebot_does_not_fallback_after_success_without_receipt():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    fallback_send = AsyncMock(return_value={"message_id": 101})

    class FakeBot:
        async def call_action(self, _action, **_kwargs):
            return {"status": "ok"}

    adapter = qq_module.QQOneBotAdapter(FakeBot(), {}, None)
    chain = qq_module.MessageChain([qq_module.Plain("hello")])

    result = await adapter.send(
        "aiocqhttp:GroupMessage:123",
        chain,
        target_session="aiocqhttp:GroupMessage:123",
        source_channel="source",
        kind="plain",
        fallback_send=fallback_send,
    )

    assert result == []
    fallback_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_qq_sender_does_not_fallback_when_onebot_succeeds_without_receipt():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    registry = RecallRegistry(max_pending=2)
    context = SimpleNamespace(send_message=AsyncMock())
    config = {
        "forward_config": {
            "auto_recall": {
                "qq_enabled": True,
                "qq_delay_seconds": 0,
            }
        }
    }
    sender = qq_module.QQSender(
        context=context,
        config=config,
        downloader=SimpleNamespace(),
        recall_registry=registry,
    )

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def call_action(self, action, **kwargs):
            self.calls.append((action, kwargs))
            return {"status": "ok"}

    bot = FakeBot()
    sender.bot = bot
    chain = qq_module.MessageChain([qq_module.Plain("hello")])

    await sender._send_with_timeout(
        "aiocqhttp:GroupMessage:123",
        chain,
        send_kind="plain",
        source_channel="source",
    )

    context.send_message.assert_not_awaited()
    assert registry.pending_count == 0
    assert bot.calls == [
        (
            "send_group_msg",
            {"group_id": 123, "message": [{"type": "text", "data": {"text": "hello"}}]},
        ),
    ]


@pytest.mark.asyncio
async def test_qq_sender_does_not_fallback_for_mixed_onebot_receipts():
    import conftest as plugin_conftest

    qq_module = plugin_conftest.load_qq_module()
    registry = RecallRegistry(max_pending=4)
    context = SimpleNamespace(send_message=AsyncMock())
    config = {
        "forward_config": {
            "auto_recall": {
                "qq_enabled": True,
                "qq_delay_seconds": 0,
            }
        }
    }
    sender = qq_module.QQSender(
        context=context,
        config=config,
        downloader=SimpleNamespace(),
        recall_registry=registry,
    )

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def call_action(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "send_group_forward_msg":
                return {"data": {"message_id": 201}}
            return {"status": "ok"}

    bot = FakeBot()
    sender.bot = bot
    node = type("Node", (), {})()
    chain = qq_module.MessageChain([node, qq_module.Plain("tail")])

    await sender._send_with_timeout(
        "aiocqhttp:GroupMessage:123",
        chain,
        send_kind="plain",
        source_channel="source",
    )

    context.send_message.assert_not_awaited()
    assert registry.pending_count == 1

    await asyncio.gather(*tuple(registry.tasks))
    assert bot.calls[-1] == ("delete_msg", {"message_id": 201})


def test_schema_declares_disabled_auto_recall_defaults():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    auto_recall = schema["forward_config"]["items"]["auto_recall"]
    items = auto_recall["items"]

    assert items["qq_enabled"]["default"] is False
    assert items["qq_delay_seconds"]["default"] == 120
    assert items["telegram_enabled"]["default"] is False
    assert items["telegram_delay_seconds"]["default"] == 120
    assert items["max_pending"]["default"] == 1000
    assert items["on_error"]["options"] == ["log", "retry_once"]
    assert items["on_error"]["default"] == "log"

    for key in (
        "qq_enabled",
        "qq_delay_seconds",
        "telegram_enabled",
        "telegram_delay_seconds",
        "max_pending",
        "on_error",
    ):
        assert items[key]["description"]
        assert items[key]["hint"]

    assert "0" in items["qq_delay_seconds"]["hint"]
    assert "0" in items["telegram_delay_seconds"]["hint"]
    assert all(policy in items["on_error"]["hint"] for policy in ("log", "retry_once"))
    assert "keep" not in items["on_error"]["hint"]
