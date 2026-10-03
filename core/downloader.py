import asyncio
import time
from io import BytesIO
from pathlib import Path

from telethon.tl.types import Message

from astrbot.api import logger


class MediaDownloader:
    """
    负责从 Telegram 消息中下载媒体文件
    """

    DOWNLOAD_CACHE_DIR = "telegram_download"
    DEFAULT_CACHE_RETENTION_SECONDS = 24 * 60 * 60
    CACHE_CLEANUP_INTERVAL_SECONDS = 60 * 60

    def __init__(
        self,
        client,
        plugin_data_dir: Path,
        max_file_size: int = 500 * 1024 * 1024,
        download_timeout_sec: float | None = None,
        retry_delay_sec: float = 2.0,
        cache_retention_seconds: float = DEFAULT_CACHE_RETENTION_SECONDS,
    ):
        self.client = client
        self.plugin_data_dir = plugin_data_dir
        self.download_cache_dir = plugin_data_dir / self.DOWNLOAD_CACHE_DIR
        self.max_file_size = max_file_size
        self.download_timeout_sec = download_timeout_sec
        self.retry_delay_sec = max(0.0, float(retry_delay_sec))
        self.cache_retention_seconds = max(0.0, float(cache_retention_seconds))
        self._last_cache_cleanup_at = 0.0

    def _cache_dir_is_safe(self) -> bool:
        """Ensure the dedicated cache root is a real directory, not a link."""
        try:
            if not self.download_cache_dir.is_dir():
                return False
            # Windows junctions are reparse points, not symbolic links.
            if self.download_cache_dir.is_symlink() or getattr(
                self.download_cache_dir.lstat(), "st_reparse_tag", 0
            ):
                logger.warning(
                    f"[Downloader] 拒绝使用链接下载缓存目录: {self.download_cache_dir}"
                )
                return False
            expected = self.plugin_data_dir.resolve() / self.DOWNLOAD_CACHE_DIR
            if self.download_cache_dir.resolve() != expected:
                logger.warning(
                    f"[Downloader] 下载缓存目录超出预期路径: {self.download_cache_dir}"
                )
                return False
            return True
        except (OSError, RuntimeError) as exc:
            logger.warning(
                f"[Downloader] 检查下载缓存目录失败 {self.download_cache_dir}: {exc}"
            )
            return False

    def cleanup_stale_files(self, *, now: float | None = None) -> int:
        """Remove interrupted-download leftovers older than the retention window."""
        current_time = time.time() if now is None else float(now)
        if not self._cache_dir_is_safe():
            self._last_cache_cleanup_at = current_time
            return 0

        cutoff = current_time - self.cache_retention_seconds
        deleted_count = 0
        try:
            for file_path in self.download_cache_dir.iterdir():
                try:
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    if file_path.stat().st_mtime >= cutoff:
                        continue
                    file_path.unlink()
                    deleted_count += 1
                except OSError as exc:
                    logger.debug(
                        f"[Downloader] 清理过期下载缓存失败 {file_path}: {exc}"
                    )
        except OSError as exc:
            logger.warning(
                f"[Downloader] 扫描下载缓存目录失败 {self.download_cache_dir}: {exc}"
            )
        self._last_cache_cleanup_at = current_time
        if deleted_count:
            logger.info(
                f"[Downloader] 清理过期下载缓存 {deleted_count} 个文件 "
                f"(保留 {self.cache_retention_seconds / 3600:g} 小时)"
            )
        return deleted_count

    def _cleanup_stale_files_if_due(self) -> None:
        if (
            time.time() - self._last_cache_cleanup_at
            >= self.CACHE_CLEANUP_INTERVAL_SECONDS
        ):
            self.cleanup_stale_files()

    def _download_timeout(self, msg: Message) -> float:
        if self.download_timeout_sec is not None:
            return max(0.01, float(self.download_timeout_sec))
        file_size = int(getattr(getattr(msg, "file", None), "size", 0) or 0)
        extra_steps = file_size // (10 * 1024 * 1024)
        return min(300.0, 30.0 + extra_steps * 30.0)

    async def contains_qr_code(self, msg: Message) -> bool:
        """Check whether a Telegram photo contains a QR code.

        Args:
            msg: Telegram message whose photo should be inspected.

        Returns:
            True when a QR code is detected. Non-photo messages, empty downloads,
            and scan failures return False.

        Raises:
            asyncio.CancelledError: If the surrounding download task is cancelled.
        """
        if not msg.photo:
            return False

        try:
            image_bytes = await self.client.download_media(msg, file=bytes)
            if not image_bytes:
                return False

            def scan_image() -> bool:
                """Decode the downloaded image in a worker thread.

                Returns:
                    True when ZXing decodes a QR code from the image.
                """
                import zxingcpp
                from PIL import Image

                with Image.open(BytesIO(image_bytes)) as image:
                    return (
                        zxingcpp.read_barcode(
                            image, formats=zxingcpp.BarcodeFormat.QRCode
                        )
                        is not None
                    )

            return await asyncio.to_thread(scan_image)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"[Filter] 消息 {msg.id} 二维码检测失败，按未命中继续处理: {e}"
            )
            return False

    async def download_media(self, msg: Message, max_size_mb: float = 0) -> list[str]:
        """
        下载媒体文件（带大小检查）
        """
        local_files = []

        if not msg.media:
            return local_files

        # 跳过动画贴纸 (.tgs) 和自定义动图表情 — QQ 无法显示
        _skip_attr_names = {
            "DocumentAttributeAnimated",
            "DocumentAttributeCustomEmoji",
        }
        if msg.sticker or (
            hasattr(msg.media, "document")
            and any(
                getattr(a, "type", None) == "animated"
                or type(a).__name__ in _skip_attr_names
                for a in getattr(getattr(msg.media, "document", None), "attributes", [])
            )
        ):
            return local_files

        # 检查大小限制 (图片除外)
        is_photo = bool(msg.photo)
        if not is_photo and max_size_mb > 0:
            file_size = 0
            if hasattr(msg.media, "document") and hasattr(msg.media.document, "size"):
                file_size = msg.media.document.size
            elif hasattr(msg.file, "size"):
                file_size = msg.file.size

            if file_size > max_size_mb * 1024 * 1024:
                logger.info(
                    f"[Downloader] 消息 {msg.id} 中的文件过大 ({file_size / 1024 / 1024:.2f} MB > {max_size_mb} MB)，跳过下载。"
                )
                return local_files

        is_video = bool(msg.video)
        is_audio = bool(msg.audio or msg.voice)
        is_file = bool(msg.file)

        should_download = is_photo or is_video or is_audio or is_file

        if should_download:
            if is_photo:
                media_type = "图片"
            elif is_video:
                media_type = "视频"
            elif is_audio:
                media_type = "音频"
            else:
                media_type = "文件"

            logger.debug(
                f"[Downloader] 检测到消息 {msg.id} 中的{media_type}，开始下载..."
            )
            if self.download_cache_dir.exists() or self.download_cache_dir.is_symlink():
                if not self._cache_dir_is_safe():
                    logger.error(
                        f"[Downloader] 下载缓存目录不安全，跳过消息 {msg.id}: "
                        f"{self.download_cache_dir}"
                    )
                    return local_files
            else:
                self.download_cache_dir.mkdir(parents=True, exist_ok=True)
            if not self._cache_dir_is_safe():
                logger.error(
                    f"[Downloader] 下载缓存目录校验失败，跳过消息 {msg.id}: "
                    f"{self.download_cache_dir}"
                )
                return local_files
            self._cleanup_stale_files_if_due()

            def progress_callback(current, total):
                if total > 0:
                    pct = (current / total) * 100
                    if int(pct) % 20 == 0 and int(pct) > 0:
                        logger.debug(f"[Downloader] 正在下载 {msg.id}: {pct:.1f}%")

            retry_count = 3
            for attempt in range(retry_count):
                timeout_sec = self._download_timeout(msg)
                try:
                    if not self.client.is_connected():
                        logger.warning(
                            f"[Downloader] 下载过程中客户端断开 (尝试 {attempt + 1})，正在尝试重连..."
                        )
                        try:
                            await self.client.connect()
                        except Exception as e:
                            logger.error(f"[Downloader] 重连失败: {e}")

                    path = await asyncio.wait_for(
                        self.client.download_media(
                            msg,
                            file=self.download_cache_dir,
                            progress_callback=progress_callback,
                        ),
                        timeout=timeout_sec,
                    )
                    if path:
                        local_files.append(path)
                        break
                except asyncio.CancelledError:
                    logger.warning(f"[Downloader] 消息 {msg.id} 的下载被取消")
                    raise
                except TimeoutError:
                    logger.warning(
                        f"[Downloader] 消息 {msg.id} 下载超时 (尝试 {attempt + 1}/{retry_count}, timeout={timeout_sec:.0f}s)"
                    )
                    if attempt < retry_count - 1:
                        await asyncio.sleep(self.retry_delay_sec)
                    else:
                        logger.error(f"[Downloader] 消息 {msg.id} 下载最终超时")
                except Exception as e:
                    logger.warning(
                        f"[Downloader] 消息 {msg.id} 下载失败 (尝试 {attempt + 1}/{retry_count}): {e}"
                    )
                    if attempt < retry_count - 1:
                        await asyncio.sleep(self.retry_delay_sec)
                    else:
                        logger.error(f"[Downloader] 消息 {msg.id} 下载最终失败")

        return local_files
