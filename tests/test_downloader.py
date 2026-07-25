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
    image_bytes = _image_bytes(Image.fromarray(barcode.to_image(scale=4)))
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
