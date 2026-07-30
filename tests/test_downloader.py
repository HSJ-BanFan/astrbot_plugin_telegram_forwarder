"""MediaDownloader cancellation behavior tests."""

import asyncio
import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
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


def test_download_timeout_scales_with_file_size(tmp_path):
    module = load_downloader_module()
    downloader = module.MediaDownloader(MagicMock(), tmp_path)

    assert (
        downloader._download_timeout(MagicMock(file=MagicMock(size=2 * 1024**2))) == 30
    )
    assert (
        downloader._download_timeout(MagicMock(file=MagicMock(size=25 * 1024**2))) == 90
    )
    assert (
        downloader._download_timeout(MagicMock(file=MagicMock(size=500 * 1024**2)))
        == 300
    )


def _image_bytes(image) -> bytes:
    """Serialize a Pillow image as PNG bytes.

    Args:
        image: Pillow image to serialize.

    Returns:
        PNG-encoded image bytes.
    """
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_contains_qr_code_detects_qr_image(tmp_path):
    import zxingcpp
    from PIL import Image

    module = load_downloader_module()
    barcode = zxingcpp.create_barcode(
        "https://example.com", zxingcpp.BarcodeFormat.QRCode
    )
    # zxing-cpp API differs by version:
    # - older/CI wheels: to_image(scale=...)
    # - newer: to_image(size_hint=...)
    try:
        qr_image = barcode.to_image(scale=4)
    except TypeError:
        qr_image = barcode.to_image(size_hint=200)
    # zxing Image exposes __array_interface__; no numpy required.
    image_bytes = _image_bytes(Image.fromarray(qr_image))
    client = MagicMock()
    client.download_media = AsyncMock(return_value=image_bytes)
    downloader = module.MediaDownloader(client, tmp_path)
    msg = SimpleNamespace(id=5401, photo=object())

    assert await downloader.contains_qr_code(msg) is True
    client.download_media.assert_awaited_once_with(msg, file=bytes)


@pytest.mark.asyncio
async def test_contains_qr_code_returns_false_for_plain_image(tmp_path):
    from PIL import Image

    module = load_downloader_module()
    client = MagicMock()
    client.download_media = AsyncMock(
        return_value=_image_bytes(Image.new("RGB", (128, 128), "white"))
    )
    downloader = module.MediaDownloader(client, tmp_path)
    msg = SimpleNamespace(id=5402, photo=object())

    assert await downloader.contains_qr_code(msg) is False


@pytest.mark.asyncio
async def test_contains_qr_code_ignores_non_photo_without_download(tmp_path):
    module = load_downloader_module()
    client = MagicMock()
    client.download_media = AsyncMock()
    downloader = module.MediaDownloader(client, tmp_path)

    assert (
        await downloader.contains_qr_code(SimpleNamespace(id=5403, photo=None)) is False
    )
    client.download_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_contains_qr_code_fails_open_on_scan_error(tmp_path):
    module = load_downloader_module()
    client = MagicMock()
    client.download_media = AsyncMock(return_value=b"not-an-image")
    downloader = module.MediaDownloader(client, tmp_path)

    assert (
        await downloader.contains_qr_code(SimpleNamespace(id=5404, photo=object()))
        is False
    )
