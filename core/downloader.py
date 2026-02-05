import os
import asyncio
from typing import List, Optional, Callable
from telethon.tl.types import Message
from astrbot.api import logger


class MediaDownloader:
    """
    负责从 Telegram 消息中下载媒体文件
    """

    def __init__(
        self, client, plugin_data_dir: str, max_file_size: int = 500 * 1024 * 1024
    ):
        self.client = client
        self.plugin_data_dir = plugin_data_dir
        self.max_file_size = max_file_size

    async def download_media(self, msg: Message, max_size_mb: float = 0) -> List[str]:
        """
        下载媒体文件（带大小检查）
        """
        local_files = []

        if not msg.media:
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
            
            logger.debug(f"[Downloader] 检测到消息 {msg.id} 中的{media_type}，开始下载...")
            
            def progress_callback(current, total):
                if total > 0:
                    pct = (current / total) * 100
                    if int(pct) % 20 == 0 and int(pct) > 0:
                        logger.debug(f"[Downloader] 正在下载 {msg.id}: {pct:.1f}%")

            retry_count = 3
            for attempt in range(retry_count):
                try:
                    if not self.client.is_connected():
                        logger.warning(
                            f"[Downloader] 下载过程中客户端断开 (尝试 {attempt+1})，正在尝试重连..."
                        )
                        try:
                            await self.client.connect()
                        except Exception as e:
                            logger.error(f"[Downloader] 重连失败: {e}")

                    path = await self.client.download_media(
                        msg,
                        file=self.plugin_data_dir,
                        progress_callback=progress_callback,
                    )
                    if path:
                        local_files.append(path)
                        break
                except asyncio.CancelledError:
                    logger.warning(f"[Downloader] 消息 {msg.id} 的下载被取消")
                    return local_files
                except Exception as e:
                    logger.warning(
                        f"[Downloader] 消息 {msg.id} 下载失败 (尝试 {attempt+1}/{retry_count}): {e}"
                    )
                    if attempt < retry_count - 1:
                        await asyncio.sleep(2)
                    else:
                        logger.error(f"[Downloader] 消息 {msg.id} 下载最终失败")

        return local_files
