"""Pausing forwarding must leave authorization and cache maintenance running."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from test_commands_debug import load_commands_module, make_event
from test_downloader import load_downloader_module
from test_forwarder_send_pending import (
    FakeStorage,
    load_forwarder_module,
    make_forwarder,
)
from test_web_admin import load_web_admin_module


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_point", ["web", "command"])
async def test_pause_keeps_maintenance_running_and_resume_restores_forwarding(
    tmp_path, entry_point
):
    scheduler = AsyncIOScheduler()
    storage = FakeStorage([])
    forwarder = make_forwarder(load_forwarder_module(), storage, strict_ack=True)
    commands = load_commands_module().PluginCommands(
        MagicMock(), {}, forwarder, scheduler
    )
    wrapper = SimpleNamespace(client=None, is_authorized=lambda: False)
    plugin = SimpleNamespace(
        config={},
        forwarder=forwarder,
        scheduler=scheduler,
        command_handler=commands,
        client_wrapper=wrapper,
    )
    server = load_web_admin_module().WebAdminServer(plugin, asyncio.get_running_loop())
    server._telegram_me_cache = {"id": 123}
    downloader = load_downloader_module().MediaDownloader(MagicMock(), tmp_path)
    downloader.download_cache_dir.mkdir()
    stale = downloader.download_cache_dir / "stale.bin"
    stale.write_bytes(b"old")
    os.utime(stale, (0, 0))
    refreshed = asyncio.Event()
    cleaned = asyncio.Event()

    async def refresh_status():
        await server._refresh_telegram_me()
        refreshed.set()

    async def cleanup_cache():
        await asyncio.to_thread(downloader.cleanup_stale_files)
        cleaned.set()

    scheduler.start()
    try:
        if entry_point == "web":
            await server.runtime_pause()
        else:
            _ = [result async for result in commands.pause(make_event())]

        assert commands._paused is True
        assert forwarder._stopping is True
        await forwarder.check_updates()
        await forwarder.send_pending_messages()
        assert storage.get_all_pending_calls == 0

        # Date jobs make execution deterministic without arbitrary sleeps.
        scheduler.add_job(refresh_status, "date")
        scheduler.add_job(cleanup_cache, "date")
        await asyncio.wait_for(
            asyncio.gather(refreshed.wait(), cleaned.wait()), timeout=2
        )
        assert server._telegram_me_cache is None
        assert not stale.exists()

        if entry_point == "web":
            await server.runtime_resume()
        else:
            _ = [result async for result in commands.resume(make_event())]
        assert commands._paused is False
        assert forwarder._stopping is False
        await forwarder.send_pending_messages()
        assert storage.get_all_pending_calls == 1
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
