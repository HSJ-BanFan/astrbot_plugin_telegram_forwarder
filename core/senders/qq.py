import os
import asyncio
import httpx
from typing import List
from telethon.tl.types import Message
from astrbot.api import logger, AstrBotConfig

from ...common.text_tools import clean_telegram_text
from ..downloader import MediaDownloader
from ..uploader import FileUploader


class QQSender:
    """
    负责将消息转发到 QQ 群 (支持合并相册)
    """



    def __init__(
        self, config: AstrBotConfig, downloader: MediaDownloader, uploader: FileUploader
    ):
        self.config = config
        self.downloader = downloader
        self.uploader = uploader
        self._group_locks = {}  # simple dict

    def _get_lock(self, group_id):
        if group_id not in self._group_locks:
            self._group_locks[group_id] = asyncio.Lock()
        return self._group_locks[group_id]

    async def send(self, batches: List[List[Message]], src_channel: str):
        """
        转发消息到 QQ 群

        Args:
            batches: 消息批次列表 (List[List[Message]])
            src_channel: 源频道名称
        """
        qq_groups = self.config.get("target_qq_group")
        napcat_url = self.config.get("napcat_api_url")
        enable_qq = self.config.get("enable_forward_to_qq", True)

        if not enable_qq:
            return

        if not (qq_groups and napcat_url) or not batches:
            return

        if isinstance(qq_groups, int):
            qq_groups = [qq_groups]
        elif not isinstance(qq_groups, list):
            return

        # 使用配置的 URL 或默认值
        url = napcat_url if napcat_url else "http://127.0.0.1:3000/send_group_msg"

        async with httpx.AsyncClient() as http:
            for gid in qq_groups:
                if not gid:
                    continue
                
                # 获取该群的锁，确保本次更新周期内的所有批次按顺序发送，不被插队
                lock = self._get_lock(gid)
                async with lock:
                    for msgs in batches:
                        all_local_files = []
                        combined_text_parts = []
                        
                        try:
                            # ========== 1. 遍历消息收集内容 ==========
                            for msg in msgs:
                                # 收集文本
                                if msg.text:
                                    cleaned = clean_telegram_text(msg.text)
                                    if cleaned:
                                        combined_text_parts.append(cleaned)

                                # 下载媒体
                                files = await self.downloader.download_media(msg)
                                all_local_files.extend(files)

                            # ========== 2. 构建最终文本 ==========
                            header = f"From #{src_channel}:\n"
                            # 简单去重：如果所有 text 都一样，只保留一份
                            if len(set(combined_text_parts)) == 1:
                                final_body = combined_text_parts[0]
                            else:
                                final_body = "\n".join(combined_text_parts)

                            final_text = header + final_body

                            # 空内容检查 (既无文本也无文件)
                            if not final_body and not all_local_files:
                                continue

                            # ========== 3. 构建消息载荷 ==========
                            message = []
                            if final_text.strip():
                                message.append({"type": "text", "data": {"text": final_text}})

                            # 处理所有收集到的文件
                            for fpath in all_local_files:
                                file_nodes = await self._process_one_file(fpath)
                                if file_nodes:
                                    message.extend(file_nodes)

                            if not message:
                                continue

                            # ========== 4. 发送 ==========
                            try:
                                # 检查是否有 record 节点 (语音特殊处理)
                                has_record = any(
                                    node.get("type") == "record" for node in message
                                )

                                if has_record:
                                    # 语音拆分发送逻辑
                                    text_nodes = [
                                        node
                                        for node in message
                                        if node.get("type") == "text"
                                    ]
                                    if text_nodes:
                                        await http.post(
                                            url,
                                            json={"group_id": gid, "message": text_nodes},
                                            timeout=60,
                                        )
                                        await asyncio.sleep(1)

                                    record_nodes = [
                                        node
                                        for node in message
                                        if node.get("type") == "record"
                                    ]
                                    for rec_node in record_nodes:
                                        await http.post(
                                            url,
                                            json={"group_id": gid, "message": [rec_node]},
                                            timeout=60,
                                        )

                                    logger.info(
                                        f"Forwarded album/msg to QQ group {gid} (Split)"
                                    )
                                else:
                                    # 普通/相册消息直接发送
                                    await http.post(
                                        url,
                                        json={"group_id": gid, "message": message},
                                        timeout=60,
                                    )
                                    logger.info(
                                        f"Forwarded album ({len(msgs)} msgs) to QQ group {gid}"
                                    )
                                    
                                # 批次间稍微延迟
                                await asyncio.sleep(1)

                            except Exception as e:
                                logger.error(
                                    f"Failed to send to QQ group {gid}: {type(e).__name__}: {e}"
                                )
                        
                        except Exception as e:
                            logger.error(f"QQ Forward Error processing batch: {e}")
                        finally:
                            # 清理本次批次的临时文件
                            self._cleanup_files(all_local_files)

    async def _process_one_file(self, fpath: str) -> List[dict]:
        """
        将本地文件转换为 NapCat 消息节点列表
        """
        ext = os.path.splitext(fpath)[1].lower()
        hosting_url = self.config.get("file_hosting_url")

        # ========== 1. 图片 -> Base64（小文件安全） ==========
        if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
            # 50MB limit for Base64 (approx 66MB string)
            if os.path.getsize(fpath) < 50 * 1024 * 1024:
                try:
                    import base64

                    with open(fpath, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode(
                            "utf-8"
                        )
                    return [
                        {
                            "type": "image",
                            "data": {"file": f"base64://{encoded_string}"},
                        }
                    ]
                except Exception as e:
                    logger.warning(f"Base64 convert failed: {e}")
            else:
                logger.info("Image too large for base64, trying upload...")

        # ========== 2. 上传到文件托管服务 ==========
        if hosting_url:
            try:
                link = await self.uploader.upload(fpath, hosting_url)

                if link:
                    # 如果是音频，尝试发送语音预览 + 链接
                    if ext in [".mp3", ".ogg", ".wav", ".m4a", ".flac", ".amr"]:
                        logger.info(f"Audio Link Generated: {link}")
                        return [
                            {
                                "type": "text",
                                "data": {
                                    "text": f"\n[Audio: {os.path.basename(fpath)}]\n🔗 Link: {link}\n"
                                },
                            },
                            {"type": "record", "data": {"file": link}},
                        ]

                    # 普通文件/大图片
                    return [
                        {"type": "text", "data": {"text": f"\n[Media Link: {link}]"}}
                    ]
                else:
                    return [
                        {
                            "type": "text",
                            "data": {
                                "text": f"\n[Media File: {os.path.basename(fpath)}] (Upload Failed)"
                            },
                        }
                    ]
            except Exception as e:
                logger.error(f"Upload Error: {type(e).__name__}: {e}")
                return [
                    {
                        "type": "text",
                        "data": {
                            "text": f"\n[Media File: {os.path.basename(fpath)}] (Upload Failed)"
                        },
                    }
                ]

        # ========== 3. 回退方案 ==========
        fname = os.path.basename(fpath)
        return [
            {
                "type": "text",
                "data": {"text": f"\n[Media File: {fname}] (Too large/No hosting)"},
            }
        ]

    def _cleanup_files(self, files: List[str]):
        """清理临时下载的文件"""
        for f in files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
