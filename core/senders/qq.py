import os
import asyncio
import httpx
from typing import List
from telethon.tl.types import Message
from astrbot.api import logger, AstrBotConfig, star
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image, Record, Video, Node, Nodes, File

from ...common.text_tools import clean_telegram_text
from ..downloader import MediaDownloader
from ..uploader import FileUploader


class QQSender:
    """
    负责将消息转发到 QQ 群 (支持合并相册)
    """

    def __init__(
        self, context: star.Context, config: AstrBotConfig, downloader: MediaDownloader, uploader: FileUploader
    ):
        self.context = context
        self.config = config
        self.downloader = downloader
        self.uploader = uploader
        self._group_locks = {}  # 群锁，防止并发发送
        self.platform_id = None # 动态捕获的平台 ID
        self.bot = None         # 动态捕获的 bot 实例
        self.node_name = None   # 合并转发消息时显示的 bot 昵称

    async def _ensure_node_name(self, bot):
        """获取 bot 昵称"""
        if self.node_name:
            return self.node_name
        
        try:
            # 优先从登录信息获取
            info = await bot.get_login_info()
            if info and (nickname := info.get("nickname")):
                self.node_name = str(nickname)
                logger.debug(f"[QQSender] 获取到 bot 昵称: {self.node_name}")
            else:
                logger.debug(f"[QQSender] 未能从登录信息获取到昵称")
        except Exception as e:
            logger.debug(f"[QQSender] 获取 bot 昵称异常: {e}")
            
        if not self.node_name:
            self.node_name = "AstrBot"
        return self.node_name

    def _get_lock(self, group_id):
        if group_id not in self._group_locks:
            self._group_locks[group_id] = asyncio.Lock()
        return self._group_locks[group_id]

    async def send(self, batches: List[List[Message]], src_channel: str, display_name: str = None, exclude_text_on_media: bool = False):
        """
        转发消息到 QQ 群
        """
        qq_groups = self.config.get("target_qq_group")
        napcat_url = self.config.get("napcat_api_url")
        
        if not qq_groups or not napcat_url or not batches:
            return

        if isinstance(qq_groups, int):
            qq_groups = [qq_groups]
        elif not isinstance(qq_groups, list):
            return

        url = napcat_url if napcat_url else "http://127.0.0.1:3000/send_group_msg"
        is_localhost = url.lower() == "localhost"

        if is_localhost:
            qq_platform_id = self.platform_id
            if not qq_platform_id:
                logger.warning("[QQSender] Localhost 模式下尚未捕获到有效的 QQ 平台 ID，跳过本次转发。")
                return

            bot = self.bot
            if not bot:
                try:
                    platform = self.context.get_platform(qq_platform_id)
                    if platform: bot = platform.bot
                    if not bot:
                        all_platforms = self.context.get_all_platforms()
                        if all_platforms:
                            for p in all_platforms:
                                if hasattr(p, "platform_config") and p.platform_config.get("id") == qq_platform_id:
                                    bot = p.bot
                                    break
                except Exception as e:
                    logger.error(f"[QQSender] 获取 bot 实例失败: {e}")
            
            self_id = 0
            node_name = "AstrBot"
            if bot:
                try:
                    node_name = await self._ensure_node_name(bot)
                    info = await bot.get_login_info()
                    self_id = info.get("user_id", 0)
                except Exception as e:
                    logger.error(f"[QQSender] 获取 bot 详细信息失败: {e}")

            # 统一显示名称格式: 如果包含 @ 则保持原样，否则添加 @ 符号
            # 如果 display_name 已经是带有 @ 的(因为获取失败回退到了 @username)，则不重复添加
            header_name = display_name or src_channel
            header_name = header_name if header_name.startswith("@") else f"@{header_name}"
            header = f"From {header_name}:"

            # 预处理所有批次的消息，避免多群转发时重复下载
            processed_batches = []
            for msgs in batches:
                all_local_files = []
                all_nodes_data = [] 
                try:
                    for i, msg in enumerate(msgs):
                        current_node_components = []
                        text_parts = []
                        if msg.text:
                            cleaned = clean_telegram_text(msg.text)
                            if cleaned: text_parts.append(cleaned)
                        
                        media_components = []
                        has_any_attachment = False
                        msg_max_size = getattr(msg, "_max_file_size", 0)
                        files = await self.downloader.download_media(msg, max_size_mb=msg_max_size)
                        for fpath in files:
                            all_local_files.append(fpath)
                            has_any_attachment = True
                            ext = os.path.splitext(fpath)[1].lower()
                            if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
                                media_components.append(Image.fromFileSystem(fpath))
                            elif ext == ".wav":
                                media_components.append(Record.fromFileSystem(fpath))
                            elif ext == ".mp4":
                                media_components.append(Video.fromFileSystem(fpath))
                            else:
                                media_components.append(File(file=fpath, name=os.path.basename(fpath)))

                        should_exclude_text = exclude_text_on_media and has_any_attachment
                        if i == 0 and not should_exclude_text:
                            if text_parts:
                                text_parts[0] = f"{header}\n\u200b{text_parts[0]}"
                            else:
                                current_node_components.append(Plain(f"{header}\n\u200b"))

                        if not should_exclude_text:
                            for t in text_parts:
                                current_node_components.append(Plain(t + "\n"))
                        
                        current_node_components.extend(media_components)
                        if current_node_components:
                            is_only_header = (i == 0 and len(current_node_components) == 1 and 
                                             isinstance(current_node_components[0], Plain) and 
                                             current_node_components[0].text in [header, header + "\n", f"{header}\n\u200b"])
                            if not is_only_header:
                                all_nodes_data.append(current_node_components)

                    if all_nodes_data:
                        processed_batches.append({
                            "nodes_data": all_nodes_data,
                            "local_files": all_local_files
                        })
                except Exception as e:
                    logger.error(f"[QQSender] 预处理消息批次异常: {e}")
                    self._cleanup_files(all_local_files)

            # 发送到各个目标群组
            for gid in qq_groups:
                if not gid: continue
                lock = self._get_lock(gid)
                async with lock:
                    unified_msg_origin = f"{qq_platform_id}:GroupMessage:{gid}"
                    for batch_data in processed_batches:
                        all_nodes_data = batch_data["nodes_data"]
                        try:
                            if len(all_nodes_data) > 1:
                                # 合并转发模式 (相册)
                                message_chain = MessageChain()
                                nodes_list = [Node(uin=self_id, name=node_name, content=nc) for nc in all_nodes_data]
                                message_chain.chain.append(Nodes(nodes_list))
                                await self.context.send_message(unified_msg_origin, message_chain)
                                logger.info(f"[QQSender] {node_name} -> 群 {gid}: 转发相册 ({len(all_nodes_data)} 节点)")
                            else:
                                # 单条消息转发模式
                                components = all_nodes_data[0]
                                special_types = (Record, File, Video)
                                has_special = any(isinstance(c, special_types) for c in components)
                                if has_special:
                                    for c in components:
                                        if isinstance(c, special_types):
                                            chain = MessageChain()
                                            chain.chain.append(c)
                                            await self.context.send_message(unified_msg_origin, chain)
                                    common_components = [c for c in components if not isinstance(c, special_types)]
                                    if common_components:
                                        chain = MessageChain()
                                        chain.chain.extend(common_components)
                                        await self.context.send_message(unified_msg_origin, chain)
                                    logger.info(f"[QQSender] {node_name} -> 群 {gid}: 转发单条消息 (已拆分媒体)")
                                else:
                                    message_chain = MessageChain()
                                    message_chain.chain.extend(components)
                                    await self.context.send_message(unified_msg_origin, message_chain)
                                    logger.info(f"[QQSender] {node_name} -> 群 {gid}: 转发单条消息")
                            await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"[QQSender] 转发到群 {gid} 异常: {e}")

            # 最后清理所有下载的文件
            for batch_data in processed_batches:
                self._cleanup_files(batch_data["local_files"])
        else:
            # HTTP 模式逻辑
            async with httpx.AsyncClient() as http:
                header_name = display_name or src_channel
                header_name = header_name if header_name.startswith("@") else f"@{header_name}"
                header = f"From {header_name}:\n"
                
                for gid in qq_groups:
                    if not gid: continue
                    lock = self._get_lock(gid)
                    async with lock:
                        for msgs in batches:
                            all_local_files = []
                            combined_text_parts = []
                            has_any_attachment = False
                            try:
                                for msg in msgs:
                                    if msg.text:
                                        cleaned = clean_telegram_text(msg.text)
                                        if cleaned: combined_text_parts.append(cleaned)
                                    msg_max_size = getattr(msg, "_max_file_size", 0)
                                    files = await self.downloader.download_media(msg, max_size_mb=msg_max_size)
                                    for fpath in files:
                                        all_local_files.append(fpath)
                                        has_any_attachment = True

                                final_body = "\n".join(combined_text_parts) if len(set(combined_text_parts)) > 1 else (combined_text_parts[0] if combined_text_parts else "")
                                final_text = header + final_body
                                
                                message = []
                                if not (exclude_text_on_media and has_any_attachment) and final_text.strip():
                                    message.append({"type": "text", "data": {"text": final_text}})

                                for fpath in all_local_files:
                                    ext = os.path.splitext(fpath)[1].lower()
                                    if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
                                        message.append({"type": "image", "data": {"file": f"file:///{os.path.abspath(fpath)}"}})
                                    elif ext == ".wav":
                                        message.append({"type": "record", "data": {"file": f"file:///{os.path.abspath(fpath)}"}})
                                    elif ext == ".mp4":
                                        message.append({"type": "video", "data": {"file": f"file:///{os.path.abspath(fpath)}"}})
                                    else:
                                        message.append({"type": "file", "data": {"file": f"file:///{os.path.abspath(fpath)}", "name": os.path.basename(fpath)}})

                                if message:
                                    try:
                                        special_types = ["record", "file", "video"]
                                        has_special = any(node.get("type") in special_types for node in message)
                                        if has_special:
                                            for spec_node in message:
                                                if spec_node.get("type") in special_types:
                                                    await http.post(url, json={"group_id": gid, "message": [spec_node]}, timeout=60)
                                            common_nodes = [node for node in message if node.get("type") not in special_types]
                                            if common_nodes:
                                                await http.post(url, json={"group_id": gid, "message": common_nodes}, timeout=60)
                                            logger.info(f"[QQSender] 转发包含视频、语音或文件的消息到群 {gid} (已拆分发送，媒体优先)")
                                        else:
                                            await http.post(url, json={"group_id": gid, "message": message}, timeout=60)
                                            logger.info(f"[QQSender] 转发相册/消息 ({len(msgs)} 条) 到群 {gid}")
                                        await asyncio.sleep(1)
                                    except Exception as e:
                                        logger.error(f"[QQSender] HTTP 发送到群 {gid} 失败: {e}")
                            finally:
                                self._cleanup_files(all_local_files)

    async def _process_one_file(self, fpath: str) -> List[dict]:
        """
        将本地文件转换为 NapCat 消息节点列表
        """
        ext = os.path.splitext(fpath)[1].lower()
        hosting_url = self.config.get("file_hosting_url")

        # 1. 处理图片：50MB 以下尝试 Base64 发送
        if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
            if os.path.getsize(fpath) < 50 * 1024 * 1024:
                try:
                    import base64
                    with open(fpath, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
                    return [
                        {
                            "type": "image",
                            "data": {"file": f"base64://{encoded_string}"},
                        }
                    ]
                except Exception as e:
                    logger.debug(f"[QQSender] 图片转 Base64 失败: {e}")
            else:
                logger.debug(f"[QQSender] 图片过大，尝试其他方式发送")

        # 2. 上传到文件托管服务
        if hosting_url:
            try:
                link = await self.uploader.upload(fpath, hosting_url)

                if link:
                    # 音频文件发送语音节点
                    if ext in [".mp3", ".ogg", ".wav", ".m4a", ".flac", ".amr"]:
                        return [
                            {
                                "type": "text",
                                "data": {
                                    "text": f"\n[音频: {os.path.basename(fpath)}]\n🔗 链接: {link}\n"
                                },
                            },
                            {"type": "record", "data": {"file": link}},
                        ]

                    # 其他媒体文件返回链接
                    return [
                        {"type": "text", "data": {"text": f"\n[媒体链接: {link}]"}}
                    ]
                else:
                    # 如果没有 link 且不是富媒体，尝试直接发送本地文件
                    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".flv"]:
                        return [
                            {
                                "type": "file",
                                "data": {
                                    "file": f"file:///{os.path.abspath(fpath)}",
                                    "name": os.path.basename(fpath)
                                }
                            }
                        ]
                    return [
                        {
                            "type": "text",
                            "data": {
                                "text": f"\n[媒体文件: {os.path.basename(fpath)}] (上传失败)"
                            },
                        }
                    ]
            except Exception as e:
                logger.error(f"[QQSender] 上传失败: {e}")
                # 上传失败回退到直接发送本地文件
                if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".flv"]:
                    return [
                        {
                            "type": "file",
                            "data": {
                                "file": f"file:///{os.path.abspath(fpath)}",
                                "name": os.path.basename(fpath)
                            }
                        }
                    ]
                return [
                    {
                        "type": "text",
                        "data": {
                            "text": f"\n[媒体文件: {os.path.basename(fpath)}] (上传异常)"
                        },
                    }
                ]

        # 3. 回退方案：如果没有配置托管，对于普通文件尝试直接发送
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".flv"]:
            return [
                {
                    "type": "file",
                    "data": {
                        "file": f"file:///{os.path.abspath(fpath)}",
                        "name": os.path.basename(fpath)
                    }
                }
            ]
        
        fname = os.path.basename(fpath)
        return [
            {
                "type": "text",
                "data": {"text": f"\n[媒体文件: {fname}] (文件过大或未配置托管)"},
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
