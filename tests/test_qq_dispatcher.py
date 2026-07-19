"""qq_dispatcher 分发引擎单元测试。

聚焦可独立验证的发送策略单元：
- `_is_probably_delivered` 纯函数
- `dispatch_processed_batches_to_targets` 的普通发送路径（成功 / 熔断延后 / 失败快速止损 / 疑似已送达）
- `send_processed_batch` 的相册合并与普通发送路径

大合并（big-merge）的完整分块降级树过于重型，由 test_qq_sender.py 间接覆盖，此处不重复。
"""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import conftest as plugin_conftest

_repo_root = Path(__file__).resolve().parents[1]


def load_dispatcher_module():
    previous = plugin_conftest._register_mock_package_tree()
    try:
        full_name = "astrbot_plugin_telegram_forwarder.core.senders.qq_dispatcher"
        path = _repo_root / "core" / "senders" / "qq_dispatcher.py"
        spec = importlib.util.spec_from_file_location(full_name, str(path))
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder.core.senders"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        plugin_conftest._restore_mock_package_tree(previous)


def _plain_component():
    """一个非 Record/File/Video 的组件替身，分发器会把它当作普通(common)组件。"""
    return SimpleNamespace()


def _batch(batch_index=0, nodes=None):
    return {
        "batch_index": batch_index,
        "nodes_data": nodes if nodes is not None else [[_plain_component()]],
    }


async def _nosleep(*_args, **_kwargs):
    """用于 monkeypatch asyncio.sleep，消除测试中的真实等待。"""
    return None


class TestIsProbablyDelivered:
    def test_confirmation_timeout_is_probable(self):
        m = load_dispatcher_module()
        assert m._is_probably_delivered("sendmsg_confirmation_timeout") is True

    def test_other_error_not_probable(self):
        m = load_dispatcher_module()
        assert m._is_probably_delivered("retcode_1200") is False


class TestDispatchSimplePath:
    @pytest.mark.asyncio
    async def test_single_target_success_records_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _nosleep)
        m = load_dispatcher_module()
        send_batch = AsyncMock()
        successes = {0: set()}
        record_success = MagicMock()
        result = await m.dispatch_processed_batches_to_targets(
            context_target_sessions=["g1"],
            real_batch_count=1,
            processed_batches=[_batch()],
            target_successes=successes,
            target_failures={},
            deferred_batch_indexes=set(),
            use_big_merge=False,
            is_mixed_big_merge=False,
            forward_cfg={},
            self_id=123,
            node_name="bot",
            get_lock=lambda s: asyncio.Lock(),
            target_is_open=lambda s, now: False,
            record_target_success=record_success,
            record_target_failure=MagicMock(),
            classify_send_error=lambda e: "retcode_1200",
            send_processed_batch_fn=send_batch,
            send_message_fn=AsyncMock(),
            fail_fast_limit=3,
            target_circuit_fail_threshold=3,
            target_circuit_cooldown_sec=300,
        )
        send_batch.assert_awaited_once()
        assert "g1" in successes[0]
        record_success.assert_called_once_with("g1")
        assert result.deferred_batch_indexes == set()
        assert result.target_failures == {}

    @pytest.mark.asyncio
    async def test_circuit_open_defers_pending_batches(self):
        m = load_dispatcher_module()
        send_batch = AsyncMock()
        deferred = set()
        result = await m.dispatch_processed_batches_to_targets(
            context_target_sessions=["g1"],
            real_batch_count=1,
            processed_batches=[_batch()],
            target_successes={0: set()},
            target_failures={},
            deferred_batch_indexes=deferred,
            use_big_merge=False,
            is_mixed_big_merge=False,
            forward_cfg={},
            self_id=123,
            node_name="bot",
            get_lock=lambda s: asyncio.Lock(),
            target_is_open=lambda s, now: True,
            record_target_success=MagicMock(),
            record_target_failure=MagicMock(),
            classify_send_error=lambda e: "retcode_1200",
            send_processed_batch_fn=send_batch,
            send_message_fn=AsyncMock(),
            fail_fast_limit=3,
            target_circuit_fail_threshold=3,
            target_circuit_cooldown_sec=300,
        )
        send_batch.assert_not_awaited()
        assert 0 in deferred
        assert result.deferred_batch_indexes == {0}

    @pytest.mark.asyncio
    async def test_failure_records_error_and_stops_at_fail_fast(self):
        m = load_dispatcher_module()
        send_batch = AsyncMock(side_effect=RuntimeError("boom"))
        failures = {}
        record_failure = MagicMock()
        await m.dispatch_processed_batches_to_targets(
            context_target_sessions=["g1"],
            real_batch_count=1,
            processed_batches=[_batch()],
            target_successes={0: set()},
            target_failures=failures,
            deferred_batch_indexes=set(),
            use_big_merge=False,
            is_mixed_big_merge=False,
            forward_cfg={},
            self_id=123,
            node_name="bot",
            get_lock=lambda s: asyncio.Lock(),
            target_is_open=lambda s, now: False,
            record_target_success=MagicMock(),
            record_target_failure=record_failure,
            classify_send_error=lambda e: "retcode_1200",
            send_processed_batch_fn=send_batch,
            send_message_fn=AsyncMock(),
            fail_fast_limit=1,
            target_circuit_fail_threshold=3,
            target_circuit_cooldown_sec=300,
        )
        assert failures == {0: "retcode_1200"}
        record_failure.assert_called_once()
        # fail_fast_limit=1 → 失败一次后立即止损，不会重试
        assert send_batch.await_count == 1

    @pytest.mark.asyncio
    async def test_probable_delivery_error_counts_as_success(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _nosleep)
        m = load_dispatcher_module()
        send_batch = AsyncMock(side_effect=RuntimeError("confirmation timeout"))
        successes = {0: set()}
        record_success = MagicMock()
        await m.dispatch_processed_batches_to_targets(
            context_target_sessions=["g1"],
            real_batch_count=1,
            processed_batches=[_batch()],
            target_successes=successes,
            target_failures={},
            deferred_batch_indexes=set(),
            use_big_merge=False,
            is_mixed_big_merge=False,
            forward_cfg={},
            self_id=123,
            node_name="bot",
            get_lock=lambda s: asyncio.Lock(),
            target_is_open=lambda s, now: False,
            record_target_success=record_success,
            record_target_failure=MagicMock(),
            classify_send_error=lambda e: "sendmsg_confirmation_timeout",
            send_processed_batch_fn=send_batch,
            send_message_fn=AsyncMock(),
            fail_fast_limit=3,
            target_circuit_fail_threshold=3,
            target_circuit_cooldown_sec=300,
        )
        assert "g1" in successes[0]
        record_success.assert_called_once_with("g1")

    @pytest.mark.asyncio
    async def test_empty_target_session_skipped(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _nosleep)
        m = load_dispatcher_module()
        send_batch = AsyncMock()
        await m.dispatch_processed_batches_to_targets(
            context_target_sessions=["", "g1"],
            real_batch_count=1,
            processed_batches=[_batch()],
            target_successes={0: set()},
            target_failures={},
            deferred_batch_indexes=set(),
            use_big_merge=False,
            is_mixed_big_merge=False,
            forward_cfg={},
            self_id=123,
            node_name="bot",
            get_lock=lambda s: asyncio.Lock(),
            target_is_open=lambda s, now: False,
            record_target_success=MagicMock(),
            record_target_failure=MagicMock(),
            classify_send_error=lambda e: "retcode_1200",
            send_processed_batch_fn=send_batch,
            send_message_fn=AsyncMock(),
            fail_fast_limit=3,
            target_circuit_fail_threshold=3,
            target_circuit_cooldown_sec=300,
        )
        # 空字符串目标被跳过，只对 "g1" 发送一次
        send_batch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_big_merge_splits_when_video_present(self, monkeypatch):
        """大合并块含 Video 时不再走 Nodes，改为逐批 send_processed_batch。"""
        monkeypatch.setattr(asyncio, "sleep", _nosleep)
        m = load_dispatcher_module()
        send_batch = AsyncMock()
        send_message = AsyncMock()
        video = m.Video(file="a.mp4")
        batch = _batch(nodes=[[video], [_plain_component()]])
        successes = {0: set()}
        await m.dispatch_processed_batches_to_targets(
            context_target_sessions=["g1"],
            real_batch_count=1,
            processed_batches=[batch],
            target_successes=successes,
            target_failures={},
            deferred_batch_indexes=set(),
            use_big_merge=True,
            is_mixed_big_merge=False,
            forward_cfg={"qq_merge_chunk_size": 10, "qq_merge_chunk_delay": 0},
            self_id=123,
            node_name="bot",
            get_lock=lambda s: asyncio.Lock(),
            target_is_open=lambda s, now: False,
            record_target_success=MagicMock(),
            record_target_failure=MagicMock(),
            classify_send_error=lambda e: "retcode_1200",
            send_processed_batch_fn=send_batch,
            send_message_fn=send_message,
            fail_fast_limit=3,
            target_circuit_fail_threshold=3,
            target_circuit_cooldown_sec=300,
        )
        send_batch.assert_awaited_once()
        assert send_batch.await_args.kwargs["allow_forward_nodes"] is False
        # 不应走 big_merge 的 Nodes 直发
        for call in send_message.await_args_list:
            assert call.kwargs.get("send_kind") != "big_merge"
        assert "g1" in successes[0]


class TestSendProcessedBatch:
    @pytest.mark.asyncio
    async def test_album_merge_when_should_merge(self):
        m = load_dispatcher_module()
        send_fn = AsyncMock()
        batch = _batch(nodes=[[_plain_component()], [_plain_component()]])
        await m.send_processed_batch(
            batch_data=batch,
            unified_msg_origin="umo",
            self_id=123,
            node_name="bot",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            should_merge=lambda bd: True,
            allow_forward_nodes=True,
        )
        send_fn.assert_awaited_once()
        assert send_fn.await_args.kwargs["send_kind"] == "album_merge"

    @pytest.mark.asyncio
    async def test_plain_send_when_not_merge(self):
        m = load_dispatcher_module()
        send_fn = AsyncMock()
        batch = _batch(nodes=[[_plain_component()]])
        await m.send_processed_batch(
            batch_data=batch,
            unified_msg_origin="umo",
            self_id=123,
            node_name="bot",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            should_merge=lambda bd: False,
            allow_forward_nodes=True,
        )
        send_fn.assert_awaited_once()
        assert send_fn.await_args.kwargs["send_kind"] == "plain"
