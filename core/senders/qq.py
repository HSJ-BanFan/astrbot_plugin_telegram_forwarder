
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
    def __init__(self, config: AstrBotConfig, downloader: MediaDownloader, uploader: FileUploader):
        self.config = config
        self.downloader = downloader
        self.uploader = uploader
        self._group_locks = {} # simple dict

    def _get_lock(self, group_id):
        if group_id not in self._group_locks:
            self._group_locks[group_id] = asyncio.Lock()
        return self._group_locks[group_id]

    async def send(self, msgs: List[Message], src_channel: str):
        """
        转发消息到 QQ 群

        Args:
            msgs: Telegram 消息对象列表
            src_channel: 源频道名称
        """
        qq_groups = self.config.get("target_qq_group")
        enable_qq = self.config.get("enable_forward_to_qq", True)
        conf_url = self.config.get("napcat_api_url", "localhost")

        # 如果填的是 localhost，则默认使用 3000 端口发送，并开启本地文件路径模式
        is_local_mode = conf_url.lower() == "localhost"
        napcat_url = "http://127.0.0.1:3000/send_group_msg" if is_local_mode else conf_url

        if not enable_qq:
            return

        if not (qq_groups and napcat_url) or not msgs:
            return
            
        if isinstance(qq_groups, int):
            qq_groups = [qq_groups]
        elif not isinstance(qq_groups, list):
            return

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
                return

            # ========== 3. 构建消息载荷 ==========
            message = []
            if final_text.strip():
                 message.append({"type": "text", "data": {"text": final_text}})

            # 处理所有收集到的文件
            for fpath in all_local_files:
                file_nodes = await self._process_one_file(fpath, is_local_mode)
                if file_nodes:
                    message.extend(file_nodes)
            
            if not message: return

            # 使用确定的 URL
            url = napcat_url
            
            async with httpx.AsyncClient() as http:
                 for gid in qq_groups:
                     if not gid: continue
                     # 获取该群的锁，确保消息发送（包含语音拆分）的原子性
                     lock = self._get_lock(gid)
                     async with lock:
                        try:
                            # 检查是否有 record 节点 (语音特殊处理)
                            has_record = any(node.get("type") == "record" for node in message)
                            
                            if has_record:
                                # 语音拆分发送逻辑
                                text_nodes = [node for node in message if node.get("type") == "text"]
                                if text_nodes:
                                    await http.post(url, json={"group_id": gid, "message": text_nodes}, timeout=60)
                                    await asyncio.sleep(1)

                                record_nodes = [node for node in message if node.get("type") == "record"]
                                for rec_node in record_nodes:
                                    await http.post(url, json={"group_id": gid, "message": [rec_node]}, timeout=60)
                                
                                logger.info(f"Forwarded album/msg to QQ group {gid} (Split)")
                            else:
                                # 普通/相册消息直接发送
                                await http.post(url, json={"group_id": gid, "message": message}, timeout=60)
                                logger.info(f"Forwarded album ({len(msgs)} msgs) to QQ group {gid}")

                        except Exception as e:
                            logger.error(f"Failed to send to QQ group {gid}: {type(e).__name__}: {e}")

        except Exception as e:
            logger.error(f"QQ Forward Error: {e}")
        finally:
            # 清理所有临时文件
            self._cleanup_files(all_local_files)

    async def _process_one_file(self, fpath: str, is_local_mode: bool) -> List[dict]:
        """
        将本地文件转换为 NapCat 消息节点列表
        """
        ext = os.path.splitext(fpath)[1].lower()
        hosting_url = self.config.get("file_hosting_url")
        abs_path = os.path.abspath(fpath)

        # ========== 1. 本地模式：直接使用文件路径 (推荐) ==========
        if is_local_mode:
            if ext in [".mp3", ".ogg", ".wav", ".m4a", ".flac", ".amr"]:
                return [{"type": "record", "data": {"file": f"file:///{abs_path}"}}]
            
            if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
                return [{"type": "image", "data": {"file": f"file:///{abs_path}"}}]

        # ========== 2. 远程/普通模式：尝试图床或 Base64 ==========
        # 图片 -> Base64 (仅在非本地模式下且文件较小时尝试)
        if not is_local_mode and ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
            if os.path.getsize(fpath) < 5 * 1024 * 1024:
                try:
                    import base64
                    with open(fpath, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    return [{"type": "image", "data": {"file": f"base64://{encoded_string}"}}]
                except:
                    pass

        # 上传到文件托管服务
        if hosting_url:
            try:
                link = await self.uploader.upload(fpath, hosting_url)
                if link:
                    if ext in [".mp3", ".ogg", ".wav", ".m4a", ".flac", ".amr"]:
                        return [
                            {"type": "text", "data": {"text": f"\n[Audio: {os.path.basename(fpath)}]\n🔗 Link: {link}\n"}},
                            {"type": "record", "data": {"file": link}}
                        ]
                    return [{"type": "text", "data": {"text": f"\n[File Link: {link}]"}}]
            except Exception as e:
                 logger.error(f"Upload Error: {e}")

        # ========== 3. 回退方案 ==========
        fname = os.path.basename(fpath)
        if is_local_mode:
            return [{"type": "text", "data": {"text": f"\n[Media File: {fname}] (Local path sending failed)"}}]
        
        # 远程模式下的原始提示语
        if hosting_url:
            return [{"type": "text", "data": {"text": f"\n[Media File: {fname}] (Upload Failed)"}}]
        return [{"type": "text", "data": {"text": f"\n[Media File: {fname}] (Too large/No hosting)"}}]


    def _cleanup_files(self, files: List[str]):
        """清理临时下载的文件"""
        for f in files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
