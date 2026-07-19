"""MediaDownloader cancellation and unsupported-media skip tests."""

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
async def test_download_media_skips_sticker_and_marks_reason(tmp_path):
    module = load_downloader_module()
    client = MagicMock()
    client.download_media = AsyncMock(return_value="/should/not/download.tgs")
    downloader = module.MediaDownloader(client, tmp_path)

    msg = MagicMock()
    msg.id = 5301
    msg.media = MagicMock()
    msg.media.document = MagicMock()
    msg.media.document.attributes = []
    msg.sticker = object()
    msg.photo = None
    msg.video = None
    msg.audio = None
    msg.voice = None
    msg.file = None

    files = await downloader.download_media(msg)
    assert files == []
    assert getattr(msg, "_tgf_skipped_media", None) == "sticker"
    client.download_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_media_skips_custom_emoji_attribute(tmp_path):
    module = load_downloader_module()
    client = MagicMock()
    client.download_media = AsyncMock(return_value="/should/not/download.webp")
    downloader = module.MediaDownloader(client, tmp_path)

    attr = type("DocumentAttributeCustomEmoji", (), {})()
    msg = MagicMock()
    msg.id = 5302
    msg.media = MagicMock()
    msg.media.document = MagicMock()
    msg.media.document.attributes = [attr]
    msg.sticker = False
    msg.photo = None
    msg.video = None
    msg.audio = None
    msg.voice = None
    msg.file = None

    files = await downloader.download_media(msg)
    assert files == []
    assert getattr(msg, "_tgf_skipped_media", None) == "animated_or_custom_emoji"
    client.download_media.assert_not_awaited()
