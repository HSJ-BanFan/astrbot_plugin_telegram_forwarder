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


class QQSender:
    """
    负责将消息转发到 QQ 群
    """

    def __init__(
        self, context: star.Context, config: AstrBotConfig, downloader: MediaDownloader
    ):
        self.context = context
        self.config = config
        self.downloader = downloader
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

    async def send(
        self,
        batches: List[List[Message]],
        src_channel: str,
        display_name: str = None,
        effective_cfg: dict = None,
        involved_channels: List[str] = None,           # 新增：混合模式时传入实际涉及的频道列表
    ):
        """
        转发消息到 QQ 群
        """
        if effective_cfg is None:
            effective_cfg = {}
        
        if involved_channels and len(involved_channels) > 1:
            global_cfg = self.config.get("forward_config", {})
            strip_links = global_cfg.get("strip_markdown_links", False)
            exclude_text_on_media = global_cfg.get("exclude_text_on_media", False)
        else:
            exclude_text_on_media = effective_cfg.get("exclude_text_on_media", False)
            strip_links = effective_cfg.get("strip_markdown_links", False)

        channel_specific_groups = effective_cfg.get("effective_target_qq_groups", [])
        if channel_specific_groups:  # 非空列表 → 使用频道专属配置
            effective_qq_groups = channel_specific_groups
        else:
            effective_qq_groups = self.config.get("target_qq_group", [])  # 回退到全局

        qq_groups = effective_qq_groups
    
        napcat_url = self.config.get("napcat_api_url")
    
        if not qq_groups or not napcat_url or not batches:
            return
    
        if isinstance(qq_groups, int):
            qq_groups = [qq_groups]
        elif not isinstance(qq_groups, list):
            return
    
        url = napcat_url if napcat_url else "http://127.0.0.1:3000/send_group_msg"
        is_localhost = url.lower() in ["localhost", "127.0.0.1", "http://localhost", "http://127.0.0.1:3000"]
    
        forward_cfg = self.config.get("forward_config", {})
        qq_merge_threshold = forward_cfg.get("qq_merge_threshold", 0)
    
        # 兼容大合并调用时多包一层的情况
        real_batches = []
        for item in batches:
            if isinstance(item, list) and item and all(isinstance(sub, list) for sub in item):
                real_batches.extend(item)
            else:
                real_batches.append(item)
    
        if not real_batches:
            logger.debug("[QQSender] 展平后无有效批次，跳过发送")
            return
    
        logger.debug(f"[QQSender] 接收到 {len(batches)} 批次，展平后 {len(real_batches)} 个逻辑批次")
    
        if is_localhost:
            qq_platform_id = self.platform_id
            if not qq_platform_id:
                logger.warning("[QQSender] Localhost 模式下尚未捕获到有效的 QQ 平台 ID（请尝试发送一条消息给bot），跳过本次转发。")
                return
    
            bot = self.bot
            if not bot:
                try:
                    platform = self.context.get_platform(qq_platform_id)
                    if platform:
                        bot = platform.bot
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
            node_name = await self._ensure_node_name(bot) if bot else "AstrBot"
            if bot:
                try:
                    info = await bot.get_login_info()
                    self_id = info.get("user_id", 0)
                except Exception as e:
                    logger.error(f"[QQSender] 获取 bot 详细信息失败: {e}")
    
            # ─── 判断是否为混合频道大合并模式 ───
            is_mixed_big_merge = bool(involved_channels and len(involved_channels) > 1)
    
            if is_mixed_big_merge:
                # 构造清晰的多频道 From
                formatted = [f"@{ch.lstrip('@')}" for ch in sorted(involved_channels)]
                if len(formatted) <= 4:
                    channels_str = " ".join(formatted)
                else:
                    channels_str = " ".join(formatted[:4]) + f" 等{len(formatted)-4}个频道"
                header = f"From {channels_str}:"
                logger.debug(f"[QQSender] 混合大合并 From: {header}")
            else:
                # 普通情况（单频道或独立频道模式）
                header_name = display_name or src_channel
                header_name = header_name if header_name.startswith("@") else f"@{header_name}"
                header = f"From {header_name}:"
    
            # 预处理所有批次
            processed_batches = []
            header_added = False  # 用于混合模式：只在全局第一个节点加 header
    
            for msgs in real_batches:
                all_local_files = []
                all_nodes_data = []
                try:
                    for i, msg in enumerate(msgs):
                        current_node_components = []
                        text_parts = []
                        if msg.text:
                            cleaned = clean_telegram_text(msg.text, strip_links=strip_links)
                            if cleaned:
                                text_parts.append(cleaned)
    
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
    
                        # ─── 决定是否添加 From 头部 ───
                        add_header_this_time = False
                        if is_mixed_big_merge:
                            # 混合大合并：**只在整个合并的第一个消息**加 From
                            if not header_added and i == 0:
                                add_header_this_time = True
                                header_added = True
                        else:
                            # 普通/独立模式：每个小相册/单条的第一个消息加 From
                            if i == 0:
                                add_header_this_time = True
    
                        if add_header_this_time:
                            if text_parts:
                                text_parts[0] = f"{header}\n\u200b{text_parts[0]}"
                            else:
                                current_node_components.append(Plain(f"{header}\n\u200b"))
    
                        if not should_exclude_text:
                            for t in text_parts:
                                current_node_components.append(Plain(t + "\n"))
    
                        current_node_components.extend(media_components)
    
                        if current_node_components:
                            # 避免生成只有 header 的空节点
                            is_only_header = (
                                len(current_node_components) == 1 and
                                isinstance(current_node_components[0], Plain) and
                                current_node_components[0].text.strip("\u200b\n") in [header, ""]
                            )
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
    
            use_big_merge = (qq_merge_threshold > 1) and (len(processed_batches) >= qq_merge_threshold)
            if use_big_merge and not is_mixed_big_merge:
                logger.info(f"[QQSender] 本次 {len(processed_batches)} 个逻辑单元 >= 阈值 {qq_merge_threshold}，转为整组合并转发")
    
            # 发送到各个目标群组
            for gid in qq_groups:
                if not gid:
                    continue
                lock = self._get_lock(gid)
                async with lock:
                    unified_msg_origin = f"{qq_platform_id}:GroupMessage:{gid}"
    
                    if use_big_merge or is_mixed_big_merge:
                        # ─── 大合并（包括混合模式） ───
                        all_sub_nodes_data = []
                        for batch_data in processed_batches:
                            all_sub_nodes_data.extend(batch_data["nodes_data"])
    
                        if all_sub_nodes_data:
                            try:
                                nodes_list = [Node(uin=self_id, name=node_name, content=nc) for nc in all_sub_nodes_data]
                                message_chain = MessageChain()
                                message_chain.chain.append(Nodes(nodes_list))
                                await self.context.send_message(unified_msg_origin, message_chain)
                                logger.info(
                                    f"[QQSender] {node_name} -> 群 {gid}: "
                                    f"{'混合' if is_mixed_big_merge else ''}大合并转发 "
                                    f"({len(all_sub_nodes_data)} 子节点 / {len(processed_batches)} 逻辑单元)"
                                )
                            except Exception as e:
                                logger.error(f"[QQSender] 大合并转发到群 {gid} 失败: {e}")
                    else:
                        # 普通发送（逐个小相册 / 单条）
                        for batch_data in processed_batches:
                            all_nodes_data = batch_data["nodes_data"]
                            try:
                                if len(all_nodes_data) > 1:
                                    # 小范围相册合并
                                    message_chain = MessageChain()
                                    nodes_list = [Node(uin=self_id, name=node_name, content=nc) for nc in all_nodes_data]
                                    message_chain.chain.append(Nodes(nodes_list))
                                    await self.context.send_message(unified_msg_origin, message_chain)
                                    logger.info(f"[QQSender] {node_name} -> 群 {gid}: 相册合并 ({len(all_nodes_data)} 节点)")
                                else:
                                    # 单条消息（可能含特殊媒体需拆分）
                                    components = all_nodes_data[0]
                                    special_types = (Record, File, Video)
                                    has_special = any(isinstance(c, special_types) for c in components)
                                    if has_special:
                                        for c in components:
                                            if isinstance(c, special_types):
                                                chain = MessageChain([c])
                                                await self.context.send_message(unified_msg_origin, chain)
                                        common_components = [c for c in components if not isinstance(c, special_types)]
                                        if common_components:
                                            chain = MessageChain()
                                            chain.chain.extend(common_components)
                                            await self.context.send_message(unified_msg_origin, chain)
                                        logger.info(f"[QQSender] {node_name} -> 群 {gid}: 单条消息 (已拆分特殊媒体)")
                                    else:
                                        message_chain = MessageChain()
                                        message_chain.chain.extend(components)
                                        await self.context.send_message(unified_msg_origin, message_chain)
                                        logger.info(f"[QQSender] {node_name} -> 群 {gid}: 单条普通消息")
                                await asyncio.sleep(1)
                            except Exception as e:
                                logger.error(f"[QQSender] 转发到群 {gid} 异常: {e}")
    
            # 清理文件
            for batch_data in processed_batches:
                self._cleanup_files(batch_data["local_files"])
    
        else:
            # HTTP (NapCat) 模式 ── 暂未实现混合 From 和大合并，保持原有逻辑
            # 如有需要可后续补充类似逻辑
            async with httpx.AsyncClient() as http:
                header_name = display_name or src_channel
                header_name = header_name if header_name.startswith("@") else f"@{header_name}"
                header = f"From {header_name}:\n"
    
                for gid in qq_groups:
                    if not gid:
                        continue
                    lock = self._get_lock(gid)
                    async with lock:
                        for msgs in real_batches:
                            all_local_files = []
                            combined_text_parts = []
                            has_any_attachment = False
                            try:
                                for msg in msgs:
                                    if msg.text:
                                        cleaned = clean_telegram_text(msg.text, strip_links=strip_links)
                                        if cleaned:
                                            combined_text_parts.append(cleaned)
                                    msg_max_size = getattr(msg, "_max_file_size", 0)
                                    files = await self.downloader.download_media(msg, max_size_mb=msg_max_size)
                                    for fpath in files:
                                        all_local_files.append(fpath)
                                        has_any_attachment = True
    
                                final_body = "\n".join(combined_text_parts) if len(set(combined_text_parts)) > 1 else (
                                    combined_text_parts[0] if combined_text_parts else ""
                                )
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
                                            for spec_node in [n for n in message if n.get("type") in special_types]:
                                                await http.post(url, json={"group_id": gid, "message": [spec_node]}, timeout=60)
                                            common_nodes = [n for n in message if n.get("type") not in special_types]
                                            if common_nodes:
                                                await http.post(url, json={"group_id": gid, "message": common_nodes}, timeout=60)
                                            logger.info(f"[QQSender] HTTP 转发包含特殊媒体到群 {gid} (已拆分)")
                                        else:
                                            await http.post(url, json={"group_id": gid, "message": message}, timeout=60)
                                            logger.info(f"[QQSender] HTTP 转发消息到群 {gid}")
                                        await asyncio.sleep(1)
                                    except Exception as e:
                                        logger.error(f"[QQSender] HTTP 发送到群 {gid} 失败: {e}")
                            finally:
                                self._cleanup_files(all_local_files)

    def _cleanup_files(self, files: List[str]):
        """清理临时下载的文件"""
        for f in files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
