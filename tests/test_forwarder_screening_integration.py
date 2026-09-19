"""Forwarder 与 ContentScreeningPipeline 双阶段审核集成测试。"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pathlib import Path
import sys

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    from test_forwarder_send_pending import (
        FakeStorage,
        load_forwarder_module,
        make_forwarder,
    )
except (ImportError, ValueError):
    from .test_forwarder_send_pending import (  # type: ignore
        FakeStorage,
        load_forwarder_module,
        make_forwarder,
    )
from core.filters.screening_pipeline import (
    ContentScreeningPipeline,
    ScreeningStage,
    ScreeningVerdict,
    VerdictAction,
)


@pytest.mark.asyncio
async def test_stage_1_fast_path_drops_message_before_storage():
    """Stage 1 快速审查：拦截关键词消息，合法消息入队，进度推进至批次最大 ID。"""
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config = {
        "forward_config": {
            "filter_keywords": ["广告推广", "casino"],
        },
        "source_channels": [{"channel_username": "demo"}],
    }
    forwarder.screening_pipeline.reload_config(forwarder.config)
    forwarder._channel_locks = {}
    forwarder._channel_last_check = {}
    forwarder._get_effective_config = lambda channel: {
        "check_interval": 60,
        "msg_limit": 10,
        "filter_keywords": ["广告推广", "casino"],
    }
    forwarder.message_merger = SimpleNamespace(
        find_defer_from_index=MagicMock(return_value=None),
        merge_messages=MagicMock(side_effect=lambda pairs: pairs),
    )
    now = datetime.now()
    forwarder._fetch_channel_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=101,
                text="这是一个正常的技术资讯分享",
                date=now,
                grouped_id=None,
                reply_markup=None,
            ),
            SimpleNamespace(
                id=102,
                text="点击链接进入 casino 免费领取金币",
                date=now,
                grouped_id=None,
                reply_markup=None,
            ),
        ]
    )
    forwarder._prepare_album_boundaries = AsyncMock(
        side_effect=lambda channel, msgs, limit: msgs
    )
    forwarder._is_monitor_matched = MagicMock(return_value=False)

    await forwarder.check_updates(force=True)

    # 验证：仅正常消息入队
    assert len(storage.pending) == 1
    assert storage.pending[0]["id"] == 101
    # 验证：last_post_id 依然推进至包含被拦截消息的批次最大 ID (102)，避免无限重复拉取
    assert storage.get_channel_data("demo")["last_post_id"] == 102


@pytest.mark.asyncio
async def test_stage_1_all_dropped_batch_advances_last_id():
    """Stage 1 快速审查：若整批消息均被拦截，队列为空，但 last_post_id 依然正常推进。"""
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config = {
        "forward_config": {
            "filter_keywords": ["博彩", "贷款"],
        },
        "source_channels": [{"channel_username": "demo"}],
    }
    forwarder.screening_pipeline.reload_config(forwarder.config)
    forwarder._channel_locks = {}
    forwarder._channel_last_check = {}
    forwarder._get_effective_config = lambda channel: {
        "check_interval": 60,
        "msg_limit": 10,
        "filter_keywords": ["博彩", "贷款"],
    }
    forwarder.message_merger = SimpleNamespace(
        find_defer_from_index=MagicMock(return_value=None),
        merge_messages=MagicMock(side_effect=lambda pairs: pairs),
    )
    now = datetime.now()
    forwarder._fetch_channel_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=201,
                text="无抵押极速贷款",
                date=now,
                grouped_id=None,
                reply_markup=None,
            ),
            SimpleNamespace(
                id=202,
                text="最新博彩网站入口",
                date=now,
                grouped_id=None,
                reply_markup=None,
            ),
        ]
    )
    forwarder._prepare_album_boundaries = AsyncMock(
        side_effect=lambda channel, msgs, limit: msgs
    )
    forwarder._is_monitor_matched = MagicMock(return_value=False)

    await forwarder.check_updates(force=True)

    # 验证：队列完全为空
    assert len(storage.pending) == 0
    # 验证：last_post_id 正确更新至 202
    assert storage.get_channel_data("demo")["last_post_id"] == 202


@pytest.mark.asyncio
async def test_stage_1_channel_specific_override():
    """频道专属规则覆盖：特定频道的专属关键词拦截生效，其他频道不受影响。"""
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config = {
        "forward_config": {
            "filter_keywords": [],
        },
        "source_channels": [
            {
                "channel_username": "restricted_ch",
                "filter_keywords": ["crypto"],
            },
            {
                "channel_username": "general_ch",
                "filter_keywords": [],
            },
        ],
    }
    forwarder.screening_pipeline.reload_config(forwarder.config)
    forwarder._channel_locks = {}
    forwarder._channel_last_check = {}
    forwarder._get_effective_config = lambda channel: {
        "check_interval": 60,
        "msg_limit": 10,
        "filter_keywords": ["crypto"] if channel == "restricted_ch" else [],
    }
    forwarder.message_merger = SimpleNamespace(
        find_defer_from_index=MagicMock(return_value=None),
        merge_messages=MagicMock(side_effect=lambda pairs: pairs),
    )
    now = datetime.now()
    forwarder._prepare_album_boundaries = AsyncMock(
        side_effect=lambda channel, msgs, limit: msgs
    )
    forwarder._is_monitor_matched = MagicMock(return_value=False)

    # 1. 抓取 restricted_ch 的 crypto 消息，应被拦截
    forwarder._fetch_channel_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=301,
                text="Buy crypto now!",
                date=now,
                grouped_id=None,
                reply_markup=None,
            )
        ]
    )
    forwarder.config["source_channels"] = [
        {"channel_username": "restricted_ch", "filter_keywords": ["crypto"]}
    ]
    forwarder.screening_pipeline.reload_config(forwarder.config)
    await forwarder.check_updates(force=True)
    assert len(storage.pending) == 0

    # 2. 抓取 general_ch 的 crypto 消息，应被放行入队
    forwarder._fetch_channel_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=302,
                text="Buy crypto now!",
                date=now,
                grouped_id=None,
                reply_markup=None,
            )
        ]
    )
    forwarder.config["source_channels"] = [{"channel_username": "general_ch"}]
    forwarder.screening_pipeline.reload_config(forwarder.config)
    await forwarder.check_updates(force=True)
    assert len(storage.pending) == 1
    assert storage.pending[0]["id"] == 302


@pytest.mark.asyncio
async def test_stage_2_cycle_budget_management():
    """Stage 2 周期配额管理：send_pending_messages 启动时重置 AI 配额。"""
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 999,
                "time": 1,
                "grouped_id": None,
                "is_cold_start": False,
                "is_monitored": False,
            }
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config = {
        "forward_config": {
            "ai_filter_enabled": True,
            "ai_filter_max_calls_per_cycle": 7,
            "batch_size_limit": 2,
        },
        "source_channels": [{"channel_username": "demo", "priority": 0}],
    }
    forwarder.screening_pipeline.reload_config(forwarder.config)
    # 人工消耗配额至 0
    forwarder.screening_pipeline._ai_calls_remaining = 0
    assert forwarder.screening_pipeline.get_remaining_ai_budget() == 0

    # 执行发送周期，应自动重置配额至 7
    await forwarder.send_pending_messages()
    assert forwarder.screening_pipeline.get_remaining_ai_budget() == 7
    assert forwarder._content_safety_calls_remaining == 7


@pytest.mark.asyncio
async def test_stage_2_dispatch_screening_drops_grouped_album():
    """Stage 2 调度审查：当相册中某张图片未通过安全审核时，整组相册均被跳过。"""
    forwarder_module = load_forwarder_module()
    storage = FakeStorage(
        [
            {
                "channel": "demo",
                "id": 401,
                "time": 1,
                "grouped_id": 99,
                "is_cold_start": False,
                "is_monitored": False,
            },
            {
                "channel": "demo",
                "id": 402,
                "time": 2,
                "grouped_id": 99,
                "is_cold_start": False,
                "is_monitored": False,
            },
        ]
    )
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config = {"forward_config": {}}
    # 模拟 402 在 Stage 2 审核中被拒绝
    forwarder._is_content_safety_matched = AsyncMock(
        side_effect=lambda msg: msg.id == 402
    )
    forwarder._send_sorted_messages_in_batches = AsyncMock()
    forwarder.client.get_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=401,
                text="safe photo",
                photo=object(),
                document=None,
                reply_markup=None,
            ),
            SimpleNamespace(
                id=402,
                text="risky photo",
                photo=object(),
                document=None,
                reply_markup=None,
            ),
        ]
    )

    await forwarder.send_pending_messages()

    # 验证：未执行发送（整组 99 因 402 触发被丢弃）
    forwarder._send_sorted_messages_in_batches.assert_not_awaited()
    # 验证：已将过滤项从存储中移除
    assert any(channel == "demo" and 401 in ids for channel, ids in storage.removed)
    assert any(channel == "demo" and 402 in ids for channel, ids in storage.removed)


def test_reload_runtime_config_syncs_with_pipeline():
    """热重载：Forwarder.reload_runtime_config 实时同步更新 screening_pipeline 配置。"""
    forwarder_module = load_forwarder_module()
    storage = FakeStorage([])
    storage.reset_inactive_channels = MagicMock()
    forwarder = make_forwarder(forwarder_module, storage, strict_ack=True)
    forwarder.config = {
        "forward_config": {
            "filter_keywords": ["initial_bad_word"],
        }
    }
    forwarder.screening_pipeline.reload_config(forwarder.config)
    assert forwarder.screening_pipeline.resolve_channel_policy().keywords == ("initial_bad_word",)

    # 动态更新配置
    forwarder.config = {
        "forward_config": {
            "filter_keywords": ["new_bad_word_1", "new_bad_word_2"],
        }
    }
    forwarder.reload_runtime_config()

    policy = forwarder.screening_pipeline.resolve_channel_policy()
    assert "new_bad_word_1" in policy.keywords
    assert "new_bad_word_2" in policy.keywords
    assert "initial_bad_word" not in policy.keywords
