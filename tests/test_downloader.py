"""MediaDownloader cancellation behavior tests."""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def load_downloader_module():
    path = Path(__file__).resolve().parents[1] / "core" / "downloader.py"
    spec = importlib.util.spec_from_file_location("test_downloader_module", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_download_media_propagates_cancellation(tmp_path):
    module = load_downloader_module()
    client = MagicMock()
    client.is_connected.return_value = True
    client.download_media = AsyncMock(side_effect=asyncio.CancelledError())
    downloader = module.MediaDownloader(client, tmp_path)

    msg = MagicMock()
    msg.id = 5300
    msg.media = object()
    msg.sticker = False
    msg.photo = object()
    msg.video = None
    msg.audio = None
    msg.voice = None
    msg.file = None

    with pytest.raises(asyncio.CancelledError):
        await downloader.download_media(msg)


@pytest.mark.asyncio
async def test_download_media_timeout_retries_and_returns(tmp_path):
    module = load_downloader_module()
    client = MagicMock()
    client.is_connected.return_value = True

    async def hangs_forever(*args, **kwargs):
        await asyncio.Event().wait()

    client.download_media = AsyncMock(side_effect=hangs_forever)
    downloader = module.MediaDownloader(
        client, tmp_path, download_timeout_sec=0.01, retry_delay_sec=0
    )

    msg = MagicMock()
    msg.id = 5400
    msg.media = object()
    msg.sticker = False
    msg.photo = None
    msg.video = object()
    msg.audio = None
    msg.voice = None
    msg.file = MagicMock()

    result = await asyncio.wait_for(downloader.download_media(msg), timeout=1)

    assert result == []
    assert client.download_media.await_count == 3
