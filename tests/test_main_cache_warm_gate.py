"""启动预热与抓取/发送调度任务之间的先后关系。"""

import asyncio
import shutil
from unittest.mock import AsyncMock

import pytest
from test_client_session_schema import load_main_module, make_test_dir


def build_main():
    tmp_dir = make_test_dir()
    main_module = load_main_module(tmp_dir)
    plugin = main_module.Main.__new__(main_module.Main)
    plugin._cache_warm_task = None
    plugin.forwarder = type(
        "Forwarder",
        (),
        {"check_updates": AsyncMock(), "send_pending_messages": AsyncMock()},
    )()
    return plugin, tmp_dir


@pytest.mark.asyncio
async def test_check_updates_job_waits_for_cache_warm():
    plugin, tmp_dir = build_main()
    try:
        order = []
        warm_started = asyncio.Event()

        async def warm():
            warm_started.set()
            await asyncio.sleep(0.05)
            order.append("warm")

        plugin._cache_warm_task = asyncio.create_task(warm())
        await warm_started.wait()

        async def check_updates():
            order.append("check")

        plugin.forwarder.check_updates = AsyncMock(side_effect=check_updates)

        await plugin._run_check_updates_job()

        assert order == ["warm", "check"]
        plugin.forwarder.check_updates.assert_awaited_once()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_send_pending_job_waits_for_cache_warm():
    plugin, tmp_dir = build_main()
    try:
        order = []
        warm_started = asyncio.Event()

        async def warm():
            warm_started.set()
            await asyncio.sleep(0.05)
            order.append("warm")

        plugin._cache_warm_task = asyncio.create_task(warm())
        await warm_started.wait()

        async def send_pending():
            order.append("send")

        plugin.forwarder.send_pending_messages = AsyncMock(side_effect=send_pending)

        await plugin._run_send_pending_job()

        assert order == ["warm", "send"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_cache_warm_gate_gives_up_after_timeout_without_cancelling_warm():
    plugin, tmp_dir = build_main()
    try:
        stopped = asyncio.Event()

        async def warm():
            try:
                await asyncio.sleep(10)
            finally:
                stopped.set()

        plugin._cache_warm_task = asyncio.create_task(warm())
        plugin.CACHE_WARM_TIMEOUT_SECONDS = 0.01

        await plugin._run_check_updates_job()

        # 超时不得取消预热任务，抓取也不得被永久挡住。
        plugin.forwarder.check_updates.assert_awaited_once()
        assert not plugin._cache_warm_task.done()
        assert not stopped.is_set()

        plugin._cache_warm_task.cancel()
        await asyncio.gather(plugin._cache_warm_task, return_exceptions=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_cache_warm_gate_ignores_failed_warm_task():
    plugin, tmp_dir = build_main()
    try:

        async def warm():
            raise RuntimeError("warm exploded")

        plugin._cache_warm_task = asyncio.create_task(warm())
        await asyncio.sleep(0)

        await plugin._run_check_updates_job()

        plugin.forwarder.check_updates.assert_awaited_once()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_cache_warm_gate_is_noop_without_warm_task():
    plugin, tmp_dir = build_main()
    try:
        plugin._cache_warm_task = None

        await plugin._run_send_pending_job()

        plugin.forwarder.send_pending_messages.assert_awaited_once()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
