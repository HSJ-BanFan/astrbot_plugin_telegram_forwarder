"""Tests for Forwarder pending removal behavior."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeStorage:
    def __init__(self, pending: list[dict]):
        self.pending = list(pending)
        self.removed: list[tuple[str, list[int]]] = []
        self.retry_updates: list[tuple[str, list[int], dict]] = []
        self.cleared: list[tuple[str, list[int]]] = []

    def get_all_pending(self) -> list[dict]:
        return list(self.pending)

    def cleanup_expired_pending(self, retention: int) -> int:
        return 0

    def remove_ids_from_pending(self, channel_name: str, msg_ids: list[int]) -> None:
        self.removed.append((channel_name, list(msg_ids)))
        self.pending = [
            item
            for item in self.pending
            if not (item["channel"] == channel_name and item["id"] in msg_ids)
        ]

    def mark_pending_retry(
        self, channel_name: str, msg_ids: list[int], **kwargs
    ) -> None:
        self.retry_updates.append((channel_name, list(msg_ids), kwargs))
        for item in self.pending:
            if item["channel"] != channel_name or item["id"] not in msg_ids:
                continue
            retry_count = item.get("retry_count", 0) + 1
            base_delay = kwargs.get("base_delay", 60)
            max_delay = kwargs.get("max_delay", 1800)
            attempted_at = kwargs.get("attempted_at", 0)
            delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)
            item.update(
                {
                    "retry_count": retry_count,
                    "next_retry_at": attempted_at + delay,
                    "last_error_type": kwargs.get("error_type", ""),
                    "last_error_code": "",
                    "last_attempt_at": attempted_at,
                    "last_target_session": kwargs.get("target_session", ""),
                }
            )

    def clear_pending_retry(self, channel_name: str, msg_ids: list[int]) -> None:
        self.cleared.append((channel_name, list(msg_ids)))
        for item in self.pending:
            if item["channel"] != channel_name or item["id"] not in msg_ids:
                continue
            item.update(
                {
                    "retry_count": 0,
                    "next_retry_at": 0,
                    "last_error_type": "",
                    "last_error_code": "",
                    "last_attempt_at": 0,
                    "last_target_session": "",
                    "last_tg_target": "",
                }
            )

    def mark_pending_tg_forwarded(
        self, channel_name: str, msg_ids: list[int], target_channel: str
    ) -> None:
        for item in self.pending:
            if item["channel"] != channel_name or item["id"] not in msg_ids:
                continue
            item["last_tg_target"] = target_channel


class QQSendSummary(SimpleNamespace):
    pass


def _snapshot_modules(*names: str) -> dict[str, object | None]:
    return {name: sys.modules.get(name) for name in names}


def _restore_modules(snapshot: dict[str, object | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


def _register_module(name: str, **attrs) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def load_forwarder_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "forwarder.py"
    snapshot = _snapshot_modules(
        "telethon",
        "telethon.tl",
        "telethon.tl.types",
        "astrbot",
        "astrbot.api",
        "astrbot_plugin_telegram_forwarder",
        "astrbot_plugin_telegram_forwarder.common",
        "astrbot_plugin_telegram_forwarder.core",
        "astrbot_plugin_telegram_forwarder.core.senders",
        "astrbot_plugin_telegram_forwarder.core.filters",
        "astrbot_plugin_telegram_forwarder.common.storage",
        "astrbot_plugin_telegram_forwarder.common.text_tools",
        "astrbot_plugin_telegram_forwarder.core.client",
        "astrbot_plugin_telegram_forwarder.core.downloader",
        "astrbot_plugin_telegram_forwarder.core.senders.telegram",
        "astrbot_plugin_telegram_forwarder.core.senders.qq",
        "astrbot_plugin_telegram_forwarder.core.filters.message_filter",
        "astrbot_plugin_telegram_forwarder.core.mergers",
        "astrbot_plugin_telegram_forwarder.core.forwarder",
    )

    try:
        _register_module("telethon")
        _register_module("telethon.tl")
        _register_module("telethon.tl.types", Message=object, PeerUser=object)

        logger = MagicMock()
        star = SimpleNamespace(Context=object)
        _register_module("astrbot")
        _register_module("astrbot.api", logger=logger, AstrBotConfig=dict, star=star)

        _register_module("astrbot_plugin_telegram_forwarder", __path__=[])
        _register_module("astrbot_plugin_telegram_forwarder.common", __path__=[])
        _register_module("astrbot_plugin_telegram_forwarder.core", __path__=[])
        _register_module("astrbot_plugin_telegram_forwarder.core.senders", __path__=[])
        _register_module("astrbot_plugin_telegram_forwarder.core.filters", __path__=[])

        _register_module("astrbot_plugin_telegram_forwarder.common.storage", Storage=object)
        _register_module(
            "astrbot_plugin_telegram_forwarder.common.text_tools",
            normalize_telegram_channel_name=lambda value: str(value).lstrip("@"),
            to_telethon_entity=lambda value: value,
            is_numeric_channel_id=lambda value: str(value).lstrip("-").isdigit(),
        )
        _register_module(
            "astrbot_plugin_telegram_forwarder.core.client", TelegramClientWrapper=object
        )
        _register_module(
            "astrbot_plugin_telegram_forwarder.core.downloader", MediaDownloader=object
        )
        _register_module(
            "astrbot_plugin_telegram_forwarder.core.senders.telegram", TelegramSender=object
        )
        _register_module(
            "astrbot_plugin_telegram_forwarder.core.senders.qq",
            QQSender=object,
            QQSendSummary=QQSendSummary,
        )
        _register_module(
            "astrbot_plugin_telegram_forwarder.core.filters.message_filter",
            MessageFilter=object,
        )
        _register_module(
            "astrbot_plugin_telegram_forwarder.core.mergers", MessageMerger=object
        )

        spec = importlib.util.spec_from_file_location(
            "astrbot_plugin_telegram_forwarder.core.forwarder",
            module_path,
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "astrbot_plugin_telegram_forwarder.core"
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        _restore_modules(snapshot)


def test_load_forwarder_module_restores_stubbed_modules(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(sys.modules, "astrbot", sentinel)
    monkeypatch.setitem(sys.modules, "astrbot.api", sentinel)

    load_forwarder_module()

    assert sys.modules["astrbot"] is sentinel
    assert sys.modules["astrbot.api"] is sentinel


def make_forwarder(forwarder_module, storage: FakeStorage, *, strict_ack: bool):
    forwarder = forwarder_module.Forwarder.__new__(forwarder_module.Forwarder)
    forwarder._stopping = False
    forwarder._send_dispatch_lock = asyncio.Lock()
    forwarder._global_send_lock = asyncio.Lock()
    forwarder._active_tasks = set()
    forwarder._shutdown_complete = asyncio.Event()
    forwarder._track_current_task = lambda: None
    forwarder._is_curfew = lambda: False
    forwarder._ensure_client_ready = AsyncMock(return_value=True)
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"],
        "has_exclusive_qq_sessions": False,
    }
    forwarder.storage = storage
    forwarder.client = MagicMock()
    forwarder.client.get_messages = AsyncMock(
        side_effect=lambda channel, ids: [
            type(
                "Msg",
                (),
                {
                    "id": msg_id,
                    "text": f"m-{msg_id}",
                    "date": msg_id,
                    "photo": None,
                    "video": None,
                    "voice": None,
                    "audio": None,
                    "document": None,
                    "reply_markup": None,
                },
            )()
            for msg_id in ids
        ]
    )
    forwarder.stats = {
        "forward_attempts": 0,
        "forward_success": 0,
        "forward_failed": 0,
    }
    forwarder.config = {
        "forward_config": {
            "batch_size_limit": 2,
            "retention_period": 86400,
            "send_result_strict_ack": strict_ack,
        },
        "source_channels": [{"channel_username": "demo", "priority": 0}],
    }
    return forwarder


@pytest.mark.asyncio
async def test_send_pending_qq_budget_does_not_throttle_batch_extraction():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 1001,
                "time": 4,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 1002,
                "time": 3,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 1003,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 1004,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"].update(
        {
            "batch_size_limit": 4,
            "qq_send_logical_unit_budget": 2,
        }
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0, 1, 2, 3),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    forwarder._send_sorted_messages_in_batches.assert_awaited_once()
    sent_batches = forwarder._send_sorted_messages_in_batches.await_args.args[0]
    assert len(sent_batches) == 4
    assert [msgs[0].id for msgs, _ in sent_batches] == [1001, 1002, 1003, 1004]


@pytest.mark.asyncio
async def test_send_pending_keeps_album_grouping_without_budget_throttling_extraction():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 1102,
                "time": 5,
                "grouped_id": 77,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 1101,
                "time": 4,
                "grouped_id": 77,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 1103,
                "time": 3,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"].update(
        {
            "batch_size_limit": 3,
            "qq_send_logical_unit_budget": 1,
        }
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0, 1),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    sent_batches = forwarder._send_sorted_messages_in_batches.await_args.args[0]
    assert len(sent_batches) == 2
    first_msgs, first_channel = sent_batches[0]
    second_msgs, second_channel = sent_batches[1]
    assert first_channel == "demo"
    assert second_channel == "demo"
    assert [msg.id for msg in first_msgs] == [1101, 1102]
    assert [msg.id for msg in second_msgs] == [1103]


@pytest.mark.asyncio
async def test_send_pending_removes_only_acked_batches_in_strict_mode():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 101,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 102,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(1,),
            deferred_batch_indexes=(),
            error_types={1: "timeout"},
        )
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [101])]
    assert [item["id"] for item in storage.pending] == [102]


@pytest.mark.asyncio
async def test_send_pending_marks_deferred_batches_for_retry_when_strict_ack_enabled():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 111,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 112,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"].update(
        {
            "pending_retry_base_delay_sec": 30,
            "pending_retry_max_delay_sec": 300,
        }
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(1,),
            error_types={1: "circuit_open"},
        )
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [111])]
    assert [item["id"] for item in storage.pending] == [112]
    assert len(storage.retry_updates) == 1
    channel, ids, retry_kwargs = storage.retry_updates[0]
    assert channel == "demo"
    assert ids == [112]
    assert retry_kwargs["error_type"] == "circuit_open"
    assert retry_kwargs["target_session"] == "test:GroupMessage:1"
    assert storage.pending[0]["next_retry_at"] == retry_kwargs["attempted_at"] + 30


@pytest.mark.asyncio
async def test_send_pending_keeps_legacy_remove_behavior_when_strict_ack_disabled():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 201,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 202,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=False)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(1,),
            deferred_batch_indexes=(),
            error_types={1: "timeout"},
        )
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [201, 202])]
    assert storage.pending == []


@pytest.mark.asyncio
async def test_send_pending_preserves_deferred_qq_batches_when_strict_ack_disabled():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 211,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 212,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=False)
    forwarder.config["forward_config"].update(
        {
            "batch_size_limit": 2,
            "qq_send_logical_unit_budget": 1,
        }
    )
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )
    forwarder.tg_sender = MagicMock()
    forwarder._get_display_name = AsyncMock(return_value="demo")

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [211])]
    assert [item["id"] for item in storage.pending] == [212]


@pytest.mark.asyncio
async def test_send_pending_does_not_remove_anything_when_sender_raises():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 301,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        side_effect=RuntimeError("sender exploded")
    )

    await forwarder.send_pending_messages()

    assert storage.removed == []
    assert [item["id"] for item in storage.pending] == [301]


@pytest.mark.asyncio
async def test_send_pending_keeps_legacy_remove_behavior_when_sender_raises_and_strict_ack_disabled():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 302,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 303,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=False)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        side_effect=RuntimeError("sender exploded")
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [302, 303])]
    assert storage.pending == []


@pytest.mark.asyncio
async def test_send_sorted_messages_in_batches_propagates_sender_summary_indexes():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(1,),
            deferred_batch_indexes=(),
            error_types={1: "timeout"},
        )
    )
    forwarder.tg_sender = MagicMock()
    forwarder._get_display_name = AsyncMock(return_value="demo")

    summary = await forwarder._send_sorted_messages_in_batches(
        [
            ([type("Msg", (), {"id": 401})()], "demo"),
            ([type("Msg", (), {"id": 402})()], "demo"),
        ]
    )

    assert summary.acked_batch_indexes == (0,)
    assert summary.failed_batch_indexes == (1,)
    assert summary.deferred_batch_indexes == ()
    assert summary.error_types == {1: "timeout"}


@pytest.mark.asyncio
async def test_send_sorted_messages_in_batches_keeps_empty_ack_when_no_qq_send_occurs():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.qq_sender = MagicMock()
    forwarder.tg_sender = MagicMock()
    forwarder._get_display_name = AsyncMock(return_value="demo")
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": [],
        "has_exclusive_qq_sessions": False,
    }

    summary = await forwarder._send_sorted_messages_in_batches(
        [
            ([type("Msg", (), {"id": 501})()], "demo"),
            ([type("Msg", (), {"id": 502})()], "demo"),
        ]
    )

    assert summary.acked_batch_indexes == ()
    assert set(summary.failed_batch_indexes) == {0, 1}
    assert summary.deferred_batch_indexes == ()
    assert summary.error_types == {}


@pytest.mark.asyncio
async def test_send_sorted_messages_in_batches_applies_qq_budget_without_blocking_tg():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"]["qq_send_logical_unit_budget"] = 1
    forwarder.config["target_channel"] = "tg-target"
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )
    forwarder.tg_sender = MagicMock()
    forwarder.tg_sender.send = AsyncMock(return_value=None)
    forwarder._get_display_name = AsyncMock(side_effect=lambda channel: channel)
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"]
        if channel in {"qq-a", "qq-b"}
        else [],
        "has_exclusive_qq_sessions": False,
    }

    summary = await forwarder._send_sorted_messages_in_batches(
        [
            ([type("Msg", (), {"id": 551})()], "qq-a"),
            ([type("Msg", (), {"id": 552})()], "tg-only"),
            ([type("Msg", (), {"id": 553})()], "qq-b"),
        ]
    )

    forwarder.qq_sender.send.assert_awaited_once()
    sent_to_qq = forwarder.qq_sender.send.await_args.kwargs["batches"]
    assert len(sent_to_qq) == 1
    assert [msg.id for msg in sent_to_qq[0]] == [551]

    # TG 转发不受 QQ budget 影响，仍会处理同轮中的非 QQ 批次（以及其余批次的 TG 侧）。
    assert forwarder.tg_sender.send.await_count == 3
    assert summary.acked_batch_indexes == (0,)
    assert summary.failed_batch_indexes == ()
    assert summary.deferred_batch_indexes == (2,)
    assert summary.error_types == {}


@pytest.mark.asyncio
async def test_send_sorted_messages_in_batches_counts_album_as_one_qq_budget_unit():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"].update(
        {"qq_send_logical_unit_budget": 1, "qq_merge_threshold": 0}
    )
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )
    forwarder.tg_sender = MagicMock()
    forwarder._get_display_name = AsyncMock(return_value="demo")

    album_batch = [
        type("Msg", (), {"id": 561, "grouped_id": 90})(),
        type("Msg", (), {"id": 562, "grouped_id": 90})(),
    ]
    summary = await forwarder._send_sorted_messages_in_batches(
        [
            (album_batch, "demo"),
            ([type("Msg", (), {"id": 563, "grouped_id": None})()], "demo"),
        ]
    )

    forwarder.qq_sender.send.assert_awaited_once()
    sent_to_qq = forwarder.qq_sender.send.await_args.kwargs["batches"]
    assert len(sent_to_qq) == 1
    assert [msg.id for msg in sent_to_qq[0]] == [561, 562]
    assert summary.acked_batch_indexes == (0,)
    assert summary.failed_batch_indexes == ()
    assert summary.deferred_batch_indexes == (1,)
    assert summary.error_types == {}


@pytest.mark.asyncio
async def test_send_sorted_messages_in_batches_sends_mixable_channels_in_sorted_order():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"].update(
        {"qq_big_merge_mode": "混合所有频道", "qq_merge_threshold": 99}
    )
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"],
        "has_exclusive_qq_sessions": False,
    }
    seen_channels = []

    async def fake_send_to_qq_and_count(
        *,
        src_channel,
        batches,
        batch_indexes,
        display_name,
        effective_cfg,
        is_mixed,
        involved_channels=None,
    ):
        seen_channels.append(src_channel)
        return QQSendSummary(
            acked_batch_indexes=tuple(batch_indexes),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )

    forwarder._send_to_qq_and_count = AsyncMock(side_effect=fake_send_to_qq_and_count)
    forwarder.tg_sender = MagicMock()
    forwarder._get_display_name = AsyncMock(side_effect=lambda channel: channel)

    with patch.object(
        forwarder_module,
        "sorted",
        return_value=["a-demo", "z-demo"],
        create=True,
    ) as sorted_mock:
        await forwarder._send_sorted_messages_in_batches(
            [
                ([type("Msg", (), {"id": 611})()], "z-demo"),
                ([type("Msg", (), {"id": 612})()], "a-demo"),
            ]
        )

    sorted_mock.assert_called_once()
    assert seen_channels == ["a-demo", "z-demo"]


@pytest.mark.asyncio
async def test_send_to_qq_and_count_counts_only_acked_batches_as_success():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(1,),
            failed_batch_indexes=(0,),
            deferred_batch_indexes=(),
            error_types={0: "send_failed"},
        )
    )

    batch_a = [type("Msg", (), {"id": 701})()]
    batch_b = [type("Msg", (), {"id": 702})(), type("Msg", (), {"id": 703})()]

    summary = await forwarder._send_to_qq_and_count(
        src_channel="demo",
        batches=[batch_a, batch_b],
        batch_indexes=[0, 1],
        display_name="demo",
        effective_cfg={},
    )

    assert forwarder.stats["forward_success"] == 2
    assert forwarder.stats["forward_failed"] == 0
    assert summary.acked_batch_indexes == (1,)
    assert summary.failed_batch_indexes == (0,)


@pytest.mark.asyncio
async def test_send_pending_removes_filtered_fetched_items_alongside_acked_batches():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 601,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 602,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": ["skip-me"],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"],
        "has_exclusive_qq_sessions": False,
    }
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )
    forwarder.client.get_messages = AsyncMock(
        return_value=[
            type(
                "Msg",
                (),
                {
                    "id": 601,
                    "text": "keep",
                    "date": 601,
                    "photo": None,
                    "video": None,
                    "voice": None,
                    "audio": None,
                    "document": None,
                    "reply_markup": None,
                },
            )(),
            type(
                "Msg",
                (),
                {
                    "id": 602,
                    "text": "skip-me",
                    "date": 602,
                    "photo": None,
                    "video": None,
                    "voice": None,
                    "audio": None,
                    "document": None,
                    "reply_markup": None,
                },
            )(),
        ]
    )

    await forwarder.send_pending_messages()

    removed_ids = sorted(msg_id for _, ids in storage.removed for msg_id in ids)
    assert removed_ids == [601, 602]
    assert storage.pending == []


@pytest.mark.asyncio
async def test_send_pending_increments_acked_messages_stat():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 801,
                "time": 3,
                "grouped_id": 91,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 802,
                "time": 2,
                "grouped_id": 91,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 803,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0, 1),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    assert forwarder.stats["acked_messages"] == 3


@pytest.mark.asyncio
async def test_send_pending_increments_failed_messages_stat():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 811,
                "time": 2,
                "grouped_id": 92,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 812,
                "time": 1,
                "grouped_id": 92,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(),
            failed_batch_indexes=(0,),
            deferred_batch_indexes=(),
            error_types={0: "timeout"},
        )
    )

    await forwarder.send_pending_messages()

    assert forwarder.stats["failed_messages"] == 2


@pytest.mark.asyncio
async def test_send_pending_increments_deferred_messages_stat():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 821,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(),
            failed_batch_indexes=(),
            deferred_batch_indexes=(0,),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    assert forwarder.stats["deferred_messages"] == 1


@pytest.mark.asyncio
async def test_send_pending_stats_not_mutated_on_sender_exception():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 831,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        side_effect=RuntimeError("sender exploded")
    )

    await forwarder.send_pending_messages()

    assert forwarder.stats["acked_messages"] == 0
    assert forwarder.stats["failed_messages"] == 1
    assert forwarder.stats["deferred_messages"] == 0


@pytest.mark.asyncio
async def test_send_pending_logs_round_summary():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 841,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 842,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(1,),
            deferred_batch_indexes=(),
            error_types={1: "timeout"},
        )
    )

    await forwarder.send_pending_messages()

    info_calls = [call.args[0] for call in forwarder_module.logger.info.call_args_list]
    assert any(
        "ACK" in message
        and "失败保留" in message
        and "延后重试" in message
        and "剩余队列" in message
        for message in info_calls
    )


@pytest.mark.asyncio
async def test_send_pending_removes_filtered_items_from_earlier_fetch_rounds():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 701,
                "time": 3,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 702,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 703,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"]["batch_size_limit"] = 2
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": ["skip-me"],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"],
        "has_exclusive_qq_sessions": False,
    }
    messages_by_id = {
        701: type(
            "Msg",
            (),
            {
                "id": 701,
                "text": "skip-me",
                "date": 701,
                "photo": None,
                "video": None,
                "voice": None,
                "audio": None,
                "document": None,
                "reply_markup": None,
            },
        )(),
        702: type(
            "Msg",
            (),
            {
                "id": 702,
                "text": "keep-702",
                "date": 702,
                "photo": None,
                "video": None,
                "voice": None,
                "audio": None,
                "document": None,
                "reply_markup": None,
            },
        )(),
        703: type(
            "Msg",
            (),
            {
                "id": 703,
                "text": "keep-703",
                "date": 703,
                "photo": None,
                "video": None,
                "voice": None,
                "audio": None,
                "document": None,
                "reply_markup": None,
            },
        )(),
    }
    forwarder.client.get_messages = AsyncMock(
        side_effect=lambda channel, ids: [messages_by_id[msg_id] for msg_id in ids]
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0, 1),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    removed_ids = sorted(msg_id for _, ids in storage.removed for msg_id in ids)
    assert removed_ids == [701, 702, 703]
    assert storage.pending == []


@pytest.mark.asyncio
async def test_send_pending_treats_same_message_id_as_channel_scoped_identity():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo-b",
                "id": 901,
                "time": 3,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo-a",
                "id": 901,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": ["skip-me"] if channel == "demo-a" else [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"],
        "has_exclusive_qq_sessions": False,
    }
    messages = {
        "demo-a": type(
            "Msg",
            (),
            {
                "id": 901,
                "text": "skip-me",
                "date": 901,
                "photo": None,
                "video": None,
                "voice": None,
                "audio": None,
                "document": None,
                "reply_markup": None,
            },
        )(),
        "demo-b": type(
            "Msg",
            (),
            {
                "id": 901,
                "text": "keep",
                "date": 901,
                "photo": None,
                "video": None,
                "voice": None,
                "audio": None,
                "document": None,
                "reply_markup": None,
            },
        )(),
    }
    forwarder.client.get_messages = AsyncMock(
        side_effect=lambda channel, ids: [messages[channel] for _ in ids]
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    forwarder._send_sorted_messages_in_batches.assert_awaited_once()
    sent_batches = forwarder._send_sorted_messages_in_batches.await_args.args[0]
    assert len(sent_batches) == 1
    sent_messages, sent_channel = sent_batches[0]
    assert sent_channel == "demo-b"
    assert [msg.id for msg in sent_messages] == [901]
    assert storage.removed == [("demo-b", [901]), ("demo-a", [901])]
    assert storage.pending == []


@pytest.mark.asyncio
async def test_send_pending_removes_batches_when_strict_ack_and_qq_not_attempted():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 801,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._get_effective_config = lambda channel: {
        "priority": 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": [],
        "has_exclusive_qq_sessions": False,
    }
    forwarder.tg_sender = MagicMock()
    forwarder.tg_sender.send = AsyncMock(return_value=None)

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [801])]
    assert storage.retry_updates == []
    assert storage.pending == []
    assert forwarder.stats["acked_messages"] == 0
    assert forwarder.stats["failed_messages"] == 0
    assert forwarder.stats["deferred_messages"] == 0


@pytest.mark.asyncio
async def test_send_pending_keeps_non_qq_batches_moving_on_sender_exception():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo-a",
                "id": 811,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo-b",
                "id": 812,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._get_effective_config = lambda channel: {
        "priority": 1 if channel == "demo-a" else 0,
        "forward_types": ["文字"],
        "max_file_size": 0,
        "filter_keywords": [],
        "filter_regex_patterns": [],
        "effective_target_qq_sessions": ["test:GroupMessage:1"]
        if channel == "demo-a"
        else [],
        "has_exclusive_qq_sessions": False,
    }
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        side_effect=RuntimeError("qq sender exploded")
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo-b", [812])]
    assert storage.retry_updates == [
        (
            "demo-a",
            [811],
            {
                "error_type": "send_failed",
                "target_session": "test:GroupMessage:1",
                "base_delay": 60,
                "max_delay": 1800,
                "attempted_at": storage.retry_updates[0][2]["attempted_at"],
            },
        )
    ]
    assert [item["channel"] for item in storage.pending] == ["demo-a"]


@pytest.mark.asyncio
async def test_send_pending_keeps_acked_qq_batches_removed_when_later_batch_raises():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo-a",
                "id": 820,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo-b",
                "id": 821,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"]["qq_merge_threshold"] = 0
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        side_effect=[
            QQSendSummary(
                acked_batch_indexes=(0,),
                failed_batch_indexes=(),
                deferred_batch_indexes=(),
                error_types={},
            ),
            RuntimeError("qq sender exploded"),
        ]
    )
    forwarder._get_display_name = AsyncMock(side_effect=lambda channel: channel)

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo-a", [820])]
    assert storage.cleared == [("demo-a", [820])]
    assert len(storage.retry_updates) == 1
    retry_channel, retry_ids, retry_meta = storage.retry_updates[0]
    assert retry_channel == "demo-b"
    assert retry_ids == [821]
    assert retry_meta["target_session"] == "test:GroupMessage:1"
    assert retry_meta["base_delay"] == 60
    assert retry_meta["max_delay"] == 1800
    assert retry_meta["attempted_at"] == storage.retry_updates[0][2]["attempted_at"]
    assert [item["id"] for item in storage.pending] == [821]


@pytest.mark.asyncio
async def test_send_pending_does_not_repeat_tg_forward_after_qq_failure_retry():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 821,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["target_channel"] = "tg-target"
    forwarder.qq_sender = MagicMock()
    forwarder.qq_sender.send = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(),
            failed_batch_indexes=(0,),
            deferred_batch_indexes=(),
            error_types={0: "timeout"},
        )
    )
    forwarder.tg_sender = MagicMock()
    forwarder.tg_sender.send = AsyncMock(return_value=None)
    forwarder._get_display_name = AsyncMock(return_value="demo")

    await forwarder.send_pending_messages()
    storage.pending[0]["next_retry_at"] = 0
    await forwarder.send_pending_messages()

    assert forwarder.qq_sender.send.await_count == 2
    forwarder.tg_sender.send.assert_awaited_once()
    assert len(storage.retry_updates) == 2
    assert [item["id"] for item in storage.pending] == [821]


@pytest.mark.asyncio
async def test_send_pending_marks_failed_batches_with_backoff():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 401,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"].update(
        {"pending_retry_base_delay_sec": 60, "pending_retry_max_delay_sec": 600}
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(),
            failed_batch_indexes=(0,),
            deferred_batch_indexes=(),
            error_types={0: "timeout"},
        )
    )

    await forwarder.send_pending_messages()

    assert storage.retry_updates == [
        (
            "demo",
            [401],
            {
                "error_type": "timeout",
                "target_session": "test:GroupMessage:1",
                "base_delay": 60,
                "max_delay": 600,
                "attempted_at": storage.retry_updates[0][2]["attempted_at"],
            },
        )
    ]
    assert storage.cleared == []


@pytest.mark.asyncio
async def test_send_pending_skips_batches_not_yet_due_for_retry():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 501,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
                "next_retry_at": 9999999999,
            },
            {
                "channel": "demo",
                "id": 502,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
                "next_retry_at": 0,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [502])]
    assert storage.cleared == [("demo", [502])]
    assert [item["id"] for item in storage.pending] == [501]


@pytest.mark.asyncio
async def test_send_pending_removes_refetch_miss_after_max_retries():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 701,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
                "retry_count": 2,
                "next_retry_at": 0,
                "last_error_type": "refetch_miss",
                "last_error_code": "",
                "last_attempt_at": 0,
                "last_target_session": "",
                "last_tg_target": "",
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"]["pending_refetch_miss_max_retries"] = 3
    forwarder.client.get_messages = AsyncMock(return_value=[])
    forwarder._send_sorted_messages_in_batches = AsyncMock()

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [701])]
    assert storage.pending == []
    forwarder._send_sorted_messages_in_batches.assert_not_called()


@pytest.mark.asyncio
async def test_send_pending_keeps_first_refetch_miss_after_other_retry_reason():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 702,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
                "retry_count": 2,
                "next_retry_at": 0,
                "last_error_type": "send_failed",
                "last_error_code": "",
                "last_attempt_at": 0,
                "last_target_session": "test:GroupMessage:1",
                "last_tg_target": "",
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"]["pending_refetch_miss_max_retries"] = 3
    forwarder.client.get_messages = AsyncMock(return_value=[])
    forwarder._send_sorted_messages_in_batches = AsyncMock()

    await forwarder.send_pending_messages()

    assert storage.removed == []
    assert storage.retry_updates and storage.retry_updates[0][0:2] == ("demo", [702])
    assert storage.pending[0]["last_error_type"] == "refetch_miss"
    forwarder._send_sorted_messages_in_batches.assert_not_called()


@pytest.mark.asyncio
async def test_send_pending_marks_refetch_miss_even_when_other_batches_send():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 703,
                "time": 2,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 704,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.client.get_messages = AsyncMock(
        return_value=[
            type(
                "Msg",
                (),
                {
                    "id": 703,
                    "text": "keep",
                    "date": 703,
                    "photo": None,
                    "video": None,
                    "voice": None,
                    "audio": None,
                    "document": None,
                    "reply_markup": None,
                },
            )()
        ]
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock(
        return_value=QQSendSummary(
            acked_batch_indexes=(0,),
            failed_batch_indexes=(),
            deferred_batch_indexes=(),
            error_types={},
        )
    )

    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [703])]
    assert storage.retry_updates and storage.retry_updates[0][0:2] == ("demo", [704])
    assert [item["id"] for item in storage.pending] == [704]


@pytest.mark.asyncio
async def test_send_pending_counts_refetch_miss_independently_across_cycles():
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 705,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
                "retry_count": 2,
                "next_retry_at": 0,
                "last_error_type": "send_failed",
                "last_error_code": "",
                "last_attempt_at": 0,
                "last_target_session": "test:GroupMessage:1",
                "last_tg_target": "",
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config["forward_config"]["pending_refetch_miss_max_retries"] = 3
    forwarder.client.get_messages = AsyncMock(return_value=[])
    forwarder._send_sorted_messages_in_batches = AsyncMock()

    await forwarder.send_pending_messages()

    assert storage.removed == []
    assert storage.pending[0]["last_error_type"] == "refetch_miss"

    storage.pending[0]["next_retry_at"] = 0
    await forwarder.send_pending_messages()

    assert storage.removed == []
    assert storage.pending[0]["id"] == 705

    storage.pending[0]["next_retry_at"] = 0
    await forwarder.send_pending_messages()

    assert storage.removed == [("demo", [705])]
    assert storage.pending == []


def test_fake_storage_clear_pending_retry_resets_last_tg_target():
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 601,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
                "retry_count": 1,
                "next_retry_at": 100,
                "last_error_type": "timeout",
                "last_error_code": "",
                "last_attempt_at": 50,
                "last_target_session": "test:GroupMessage:1",
                "last_tg_target": "tg-target",
            }
        ]
    )

    storage.clear_pending_retry("demo", [601])

    assert storage.pending[0]["last_target_session"] == ""
    assert storage.pending[0]["last_tg_target"] == ""
