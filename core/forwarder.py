"""
消息转发核心模块

负责从 Telegram 频道获取消息并转发到目标平台（Telegram 频道或 QQ 群）。
主要功能：
- 定期检查频道更新
- 消息过滤（关键词、正则表达式）
- 冷启动支持（从指定日期开始）
- 模块化设计：使用 Monitor -> Sender 模式
"""

import asyncio
import re
import os
from datetime import datetime, timezone
from typing import Optional, List
from telethon.tl.types import Message, PeerUser

from astrbot.api import logger, AstrBotConfig
from ..common.storage import Storage
from .client import TelegramClientWrapper
from .downloader import MediaDownloader
from .uploader import FileUploader
from .senders.telegram import TelegramSender
from .senders.qq import QQSender


class Forwarder:
    """
    消息转发器核心类 (Monitor + Dispatcher)

    负责：
    1. 监控源频道更新
    2. 过滤消息
    3. 分发给各平台 Sender
    """

    def __init__(
        self,
        config: AstrBotConfig,
        storage: Storage,
        client_wrapper: TelegramClientWrapper,
        plugin_data_dir: str,
    ):
        self.config = config
        self.storage = storage
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client
        self.plugin_data_dir = plugin_data_dir
        self.proxy_url = config.get("proxy")

        # 初始化组件
        self.downloader = MediaDownloader(self.client, plugin_data_dir)
        self.uploader = FileUploader(self.proxy_url)

        # 初始化发送器
        self.tg_sender = TelegramSender(self.client, config)
        self.qq_sender = QQSender(config, self.downloader, self.uploader)

        # 启动时清理孤儿文件
        self._cleanup_orphaned_files()

        # 任务锁，防止重入 (Key: ChannelName)
        self._channel_locks = {}
        # 上次检查时间 (Key: ChannelName)
        self._channel_last_check = {}

    def _get_channel_lock(self, channel_name):
        if channel_name not in self._channel_locks:
            self._channel_locks[channel_name] = asyncio.Lock()
        return self._channel_locks[channel_name]

    async def check_updates(self):
        """
        检查所有配置的频道更新
        """
        # 检查连接状态
        if not self.client_wrapper.is_connected():
            return

        # 获取源频道配置列表
        channels_config = self.config.get("source_channels", [])

        async def process_one(cfg):
            try:
                channel_name = ""
                start_date = None
                default_interval = 0 # Default interval if not specified
                interval = default_interval
                msg_limit = 20  # 默认一次处理20条

                # 解析频道配置
                if isinstance(cfg, dict):
                    # New config format
                    channel_name = cfg.get("channel_username", "")
                    if not channel_name:
                        logger.warning(f"Skipping channel config with missing 'channel_username': {cfg}")
                        return
                    
                    s_time = cfg.get("start_time", "")
                    if s_time:
                        try:
                            start_date = datetime.strptime(s_time, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except ValueError:
                            logger.error(f"Invalid date format for {channel_name}: {s_time}")

                    interval = cfg.get("check_interval", default_interval)
                    msg_limit = cfg.get("msg_limit", 20)

                    # Handle config_preset (overrides the above if set)
                    preset = cfg.get("config_preset", "")
                    if preset:
                        try:
                            # preset format: date|interval|limit
                            p_parts = [p.strip() for p in preset.split("|")]
                            if len(p_parts) >= 1 and p_parts[0]:
                                try:
                                    start_date = datetime.strptime(p_parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                                except ValueError:
                                    logger.warning(f"Invalid date in preset {preset} for {channel_name}")
                            
                            if len(p_parts) >= 2 and p_parts[1].isdigit():
                                interval = int(p_parts[1])
                            
                            if len(p_parts) >= 3 and p_parts[2].isdigit():
                                msg_limit = int(p_parts[2])
                                
                            logger.info(f"Applied preset {preset} for {channel_name}: date={start_date}, int={interval}, limit={msg_limit}")
                        except Exception as e:
                            logger.error(f"Error applying preset {preset} for {channel_name}: {e}")
                
                elif isinstance(cfg, str):
                    # Legacy string format: name|date|interval|limit
                    # 格式：channel_name | start_date | interval | limit
                    # 示例：xiaoshuwu | 2025-01-01 | 60 | 5
                    parts = [p.strip() for p in cfg.split("|")]
                    if not parts:
                        logger.warning(f"Skipping invalid channel string config: {cfg}")
                        return
                    
                    channel_name = parts[0]
                    ints_found = []

                    for part in parts[1:]:
                        if not part:
                            continue
                        # 尝试解析为日期
                        if "-" in part and not start_date:
                            try:
                                start_date = datetime.strptime(part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                                continue
                            except:
                                pass

                        # 数字可能是间隔或限制
                        if part.isdigit():
                            ints_found.append(int(part))

                    # 分配数字参数
                    if len(ints_found) >= 1:
                        interval = ints_found[0]
                    if len(ints_found) >= 2:
                        msg_limit = ints_found[1]
                else:
                    logger.warning(f"Skipping unknown channel config type: {type(cfg)} - {cfg}")
                    return  # Unknown type

                # Ensure channel_name is set
                if not channel_name:
                    logger.warning(f"Channel name could not be determined for config: {cfg}")
                    return

                # 检查间隔
                now = datetime.now().timestamp()
                last_check = self._channel_last_check.get(channel_name, 0)
                # 使用该频道配置的间隔判断
                if now - last_check < interval:
                    return # 还没到时间

                # 获取该频道的锁
                lock = self._get_channel_lock(channel_name)

                # 如果锁被占用，跳过本次检查（非阻塞）
                if lock.locked():
                    return

                async with lock:
                    self._channel_last_check[channel_name] = now
                    # 处理该频道
                    await self._process_channel(channel_name, start_date, msg_limit)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 记录错误但继续处理其他频道
                logger.error(f"Error checking {cfg}: {e}")

        # 并行执行所有频道的检查任务
        tasks = [process_one(cfg) for cfg in channels_config]
        if tasks:
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                pass  # 正常取消，忽略
            except Exception as e:
                logger.error(f"Error in check_updates gather: {e}")

    async def _process_channel(
        self, channel_name: str, start_date: Optional[datetime], msg_limit: int = 20
    ):
        """
        处理单个频道的消息更新
        """
        # ========== 初始化频道状态 ==========
        if not self.storage.get_channel_data(channel_name).get("last_post_id"):
            self.storage.update_last_id(channel_name, 0)

        last_id = self.storage.get_channel_data(channel_name)["last_post_id"]

        try:
            # ========== 冷启动处理 ==========
            if last_id == 0:
                if start_date:
                    logger.info(f"Cold start for {channel_name} with date {start_date}")
                    pass  # 逻辑在迭代参数中处理
                else:
                    msgs = await self.client.get_messages(channel_name, limit=1)
                    if msgs:
                        self.storage.update_last_id(channel_name, msgs[0].id)
                        logger.info(f"Initialized {channel_name} at ID {msgs[0].id}")
                    return

            # ========== 获取新消息 ==========
            new_messages = []
            params = {"entity": channel_name, "reverse": True, "limit": msg_limit}

            if last_id > 0:
                params["min_id"] = last_id
            elif start_date:
                params["offset_date"] = start_date
            else:
                params["limit"] = 5  # 默认初始化获取5条

            async for message in self.client.iter_messages(**params):
                if not message.id:
                    continue
                new_messages.append(message)

            if not new_messages:
                return

            # ========== 处理过滤和分发 ==========
            await self._process_messages_filter_and_dispatch(
                channel_name, new_messages, last_id
            )

        except Exception as e:
            logger.error(f"Access error for {channel_name}: {e}")

    async def _process_messages_filter_and_dispatch(
        self, channel_name: str, new_messages: list, last_id: int
    ):
        """
        过滤消息并进行分发处理 (支持相册批处理)
        """
        filter_keywords = self.config.get("filter_keywords", [])
        filter_regex = self.config.get("filter_regex", "")
        filter_hashtags = self.config.get("filter_hashtags", [])

        # 收集所有需要发送的批次
        all_batches = []

        async def process_batch(batch):
            if not batch:
                return
            batch_to_send = []
            for msg in batch:
                try:
                    # ----- 反垃圾 / 频道过滤 -----
                    is_user_msg = (
                        isinstance(msg.from_id, PeerUser) if msg.from_id else False
                    )
                    if not msg.post and is_user_msg:
                        continue

                    text_content = msg.text or ""
                    should_skip = False

                    # Hashtag 过滤
                    if filter_hashtags:
                        for tag in filter_hashtags:
                            if tag in text_content:
                                logger.info(f"Filtered {msg.id}: Hashtag {tag}")
                                should_skip = True
                                break

                    # Keyword 过滤
                    if not should_skip and filter_keywords:
                        for kw in filter_keywords:
                            if kw in text_content:
                                logger.info(f"Filtered {msg.id}: Keyword {kw}")
                                should_skip = True
                                break

                    # Regex 过滤
                    if not should_skip and filter_regex:
                        if re.search(
                            filter_regex, text_content, re.IGNORECASE | re.DOTALL
                        ):
                            logger.info(f"Filtered {msg.id}: Regex")
                            should_skip = True

                    if not should_skip:
                        batch_to_send.append(msg)
                except Exception as e:
                    logger.error(f"Error filtering msg {msg.id}: {e}")

            if batch_to_send:
                all_batches.append(batch_to_send)

            # 更新 last_id (无论是否发送/被过滤)
            if batch:
                max_id = max(m.id for m in batch)
                self.storage.update_last_id(channel_name, max_id)

        # 遍历消息进行批处理
        pending_batch = []
        for msg in new_messages:
            try:
                if msg.grouped_id:
                    if pending_batch and pending_batch[0].grouped_id != msg.grouped_id:
                        await process_batch(pending_batch)
                        pending_batch = []
                    pending_batch.append(msg)
                else:
                    if pending_batch:
                        await process_batch(pending_batch)
                        pending_batch = []
                    await process_batch([msg])
            except Exception as e:
                logger.error(f"Error in msg loop {msg.id}: {e}")

        if pending_batch:
            await process_batch(pending_batch)
            
        # 批量分发 (Ensure atomicity)
        if all_batches:
             await self._dispatch_to_senders(channel_name, all_batches)

    async def _dispatch_to_senders(self, src_channel: str, batches: List[List[Message]]):
        """
        将消息批次分发给所有 Sender
        """
        # 并行发送到各平台
        await asyncio.gather(
            self.tg_sender.send(batches, src_channel),
            self.qq_sender.send(batches, src_channel),
        )

    def _cleanup_orphaned_files(self):
        """
        启动时清理插件数据目录中的孤儿文件
        """
        if not os.path.exists(self.plugin_data_dir):
            return

        logger.info(f"Cleaning up orphaned files in {self.plugin_data_dir}...")
<<<<<<< HEAD

        allowlist = [
            "data.json",
            "user_session.session",
            "user_session.session-journal",
=======
        
        # 允许保留的文件列表
        # .session-journal, .session-shm, .session-wal 是 SQLite 的临时文件，必须保留以防止数据库损坏
        allowlist = [
            "data.json", 
            "user_session.session", 
            "user_session.session-journal",
            "user_session.session-shm",
            "user_session.session-wal"
>>>>>>> pr-1
        ]
        deleted_count = 0

        try:
            for filename in os.listdir(self.plugin_data_dir):
                if filename in allowlist:
                    continue

                file_path = os.path.join(self.plugin_data_dir, filename)

                # 只删除文件，不删除子目录
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                logger.info(
                    f"Cleanup finished. Removed {deleted_count} orphaned files."
                )

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
