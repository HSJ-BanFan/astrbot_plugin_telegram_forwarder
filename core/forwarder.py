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
import os
from datetime import datetime, timezone
from typing import Optional, List
from telethon.tl.types import Message, PeerUser

from astrbot.api import logger, AstrBotConfig, star
from ..common.storage import Storage
from .client import TelegramClientWrapper
from .downloader import MediaDownloader
from .uploader import FileUploader
from .senders.telegram import TelegramSender
from .senders.qq import QQSender
from .filters.message_filter import MessageFilter
from .mergers import MessageMerger
from .config import ChannelConfigParser


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
        context: star.Context,
        config: AstrBotConfig,
        storage: Storage,
        client_wrapper: TelegramClientWrapper,
        plugin_data_dir: str,
    ):
        self.context = context
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
        self.qq_sender = QQSender(self.context, config, self.downloader, self.uploader)

        # 初始化过滤器和合并引擎
        self.message_filter = MessageFilter(config)
        self.message_merger = MessageMerger(config)

        # 启动时清理孤儿文件
        self._cleanup_orphaned_files()

        # 任务锁，防止重入 (Key: ChannelName)
        self._channel_locks = {}
        # 上次检查时间 (Key: ChannelName)
        self._channel_last_check = {}
        # 全局发送锁，确保所有频道的消息按顺序发送，避免交错
        self._global_send_lock = asyncio.Lock()

    def _get_channel_lock(self, channel_name):
        if channel_name not in self._channel_locks:
            self._channel_locks[channel_name] = asyncio.Lock()
        return self._channel_locks[channel_name]

    async def check_updates(self):
        """
        检查所有配置的频道更新 (FGSS 模式: Fetch-Gather-Sort-Send)

        架构改进：
        1. Fetch: 并发获取所有频道的新消息
        2. Gather: 收集所有消息到全局队列
        3. Sort: 按消息时间戳排序，确保跨频道时序正确
        4. Send: 线性发送，避免交错混乱

        优势：
        - 保证严格的时间顺序（频道B的10:01消息不会在频道A的10:05消息之后）
        - 避免Head-of-Line Blocking（通过超时控制慢速频道）

        Trade-off:
        - 稍微增加延迟（等待所有频道完成）
        - 但换来更好的用户体验（消息按时间线排列）
        """
        # 检查连接状态
        if not self.client_wrapper.is_connected():
            return

        # 获取源频道配置列表
        channels_config = self.config.get("source_channels", [])

        async def process_one(cfg):
            """
            处理单个频道的配置，返回获取到的消息列表

            Returns:
                List[Tuple[str, Message]]: (channel_name, message) 元组列表
            """
            try:
                # 解析频道配置
                config = ChannelConfigParser.parse(cfg)
                if not config:
                    return []

                # 检查间隔
                now = datetime.now().timestamp()
                last_check = self._channel_last_check.get(config.channel_name, 0)
                if now - last_check < config.interval:
                    return []  # 还没到时间

                # 获取该频道的锁
                lock = self._get_channel_lock(config.channel_name)

                # 如果锁被占用，跳过本次检查（非阻塞）
                if lock.locked():
                    return []

                async with lock:
                    self._channel_last_check[config.channel_name] = now
                    # Fetch阶段：只获取消息，不发送
                    messages = await self._fetch_channel_messages(
                        config.channel_name, config.start_date, config.msg_limit
                    )
                    return [(config.channel_name, msg) for msg in messages]

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 记录错误但继续处理其他频道
                logger.error(f"Error checking {cfg}: {e}")
                return []

        # ========== Phase 1 & 2: Fetch & Gather ==========
        # 并行获取所有频道的新消息，设置超时以避免 Head-of-Line Blocking
        FETCH_TIMEOUT = 15  # 15秒超时，避免慢速频道拖累整体

        try:
            # 并发执行所有频道的 Fetch 任务
            tasks = [process_one(cfg) for cfg in channels_config]
            if tasks:
                # 使用 gather 并设置超时
                all_channel_messages = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=FETCH_TIMEOUT,
                )

                # 合并所有频道的消息
                global_message_queue = []
                for channel_messages in all_channel_messages:
                    if isinstance(channel_messages, Exception):
                        logger.error(f"Channel fetch failed: {channel_messages}")
                        continue
                    if channel_messages:
                        global_message_queue.extend(channel_messages)

                # ========== Phase 3: Sort ==========
                # 按消息时间戳排序，确保跨频道时序正确
                if global_message_queue:
                    # 按 message.date 排序（从旧到新）
                    global_message_queue.sort(key=lambda x: x[1].date)

                    logger.info(
                        f"[FGSS] Sorted {len(global_message_queue)} messages from {len(channels_config)} channels by timestamp"
                    )

                    # ========== Phase 4: Send ==========
                    # 线性发送，严格按时间顺序
                    await self._send_sorted_messages(global_message_queue)

        except asyncio.TimeoutError:
            logger.warning(
                f"[FGSS] Fetch phase timeout after {FETCH_TIMEOUT}s, sending partial results"
            )
            # 发送已获取的消息
            if "global_message_queue" in locals() and global_message_queue:
                global_message_queue.sort(key=lambda x: x[1].date)
                await self._send_sorted_messages(global_message_queue)
        except asyncio.CancelledError:
            logger.info("[FGSS] Check updates cancelled")
            raise
        except Exception as e:
            logger.error(f"[FGSS] Error in check_updates: {e}")

    async def _fetch_channel_messages(
        self, channel_name: str, start_date: Optional[datetime], msg_limit: int = 20
    ) -> List[Message]:
        """
        Fetch阶段：从单个频道获取新消息（不发送）

        Returns:
            List[Message]: 获取到的新消息列表

        Note:
            此方法只负责获取消息，过滤和发送在后续阶段处理
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
                    return []

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

            # 更新 last_post_id（无论消息是否会被过滤）
            if new_messages:
                max_id = max(m.id for m in new_messages)
                self.storage.update_last_id(channel_name, max_id)
                logger.info(
                    f"[Fetch] {channel_name}: fetched {len(new_messages)} messages (max_id: {max_id})"
                )

            return new_messages

        except Exception as e:
            logger.error(f"[Fetch] Error accessing {channel_name}: {e}")
            return []

    async def _send_sorted_messages(self, sorted_messages: List[tuple]):
        """
        Send阶段：发送已排序的消息（应用过滤和相册处理）

        Args:
            sorted_messages: 已按时间排序的 (channel_name, message) 元组列表

        处理流程：
        1. 过滤不想要的消息（关键词、正则、hashtag）
        2. 智能合并关联消息（针对不规范频道）
        3. 识别相册（grouped_id）并分组
        4. 按顺序发送每个批次
        """
        # ========== Phase 1: 过滤消息 ==========
        filtered_messages = self.message_filter.filter_messages(
            sorted_messages, logger.info
        )

        if not filtered_messages:
            logger.info("[Send] All messages were filtered, nothing to send")
            return

        logger.info(
            f"[Send] {len(filtered_messages)} messages after filtering (from {len(sorted_messages)} fetched)"
        )

        # ========== Phase 1.5: 智能合并关联消息 (针对不规范频道) ==========
        filtered_messages = self.message_merger.merge_messages(filtered_messages)

        # ========== Phase 2: 相册分组 (修正版 + 合并标记支持) ==========
        # 使用字典按 (channel_name, grouped_id) 聚合，确保跨消息的相册也能正确合并
        # 同时支持 _merge_group_id 属性（用于智能合并的关联消息）
        album_groups = {}
        batches = []

        for channel_name, msg in filtered_messages:
            # 优先检查 _merge_group_id（智能合并的标记）
            group_id = getattr(msg, "_merge_group_id", None)

            if not group_id:
                # 使用 Telegram 原生 grouped_id
                group_id = msg.grouped_id

            if group_id:
                key = (channel_name, group_id)
                if key not in album_groups:
                    album_groups[key] = []
                album_groups[key].append((channel_name, msg))
            else:
                batches.append([(channel_name, msg)])

        # 将聚合好的相册添加到批次中
        for group in album_groups.values():
            batches.append(group)

        # 注意：不重新排序批次，保持原始消息顺序
        # 这样可以确保同一频道的关联消息（如 SomeACG 的预览图+原图）保持连续发送

        logger.info(
            f"[Send] Organized into {len(batches)} batches (albums={len(album_groups)}, singles={len(batches) - len(album_groups)})"
        )

        # ========== Phase 3: 线性发送 ==========
        # 使用全局锁确保原子性，按顺序发送每个批次
        async with self._global_send_lock:
            for batch_idx, batch in enumerate(batches):
                try:
                    # 提取消息对象和源频道
                    messages = [msg for _, msg in batch]
                    src_channel = batch[0][0]  # 获取源频道名称

                    # 判断批次类型（相册或单条消息）
                    if len(messages) > 1:
                        # 相册：所有消息的 grouped_id 相同
                        batch_type = "album"
                        grouped_id = messages[0].grouped_id
                        logger.info(
                            f"[Send] Batch {batch_idx + 1}/{len(batches)}: Album from {src_channel} (grouped_id={grouped_id}, {len(messages)} photos)"
                        )
                    else:
                        # 单条消息
                        batch_type = "single"
                        msg = messages[0]
                        msg_preview = (
                            (msg.text[:30] + "...")
                            if msg.text
                            else f"[Media: {type(msg.media).__name__}]"
                        )
                        logger.info(
                            f"[Send] Batch {batch_idx + 1}/{len(batches)}: Single msg {msg.id} from {src_channel} ({msg_preview})"
                        )

                    # 将批次转换为 Sender 期望的格式: List[List[Message]], src_channel
                    sender_batches = [messages]

                    # 并行发送到各平台
                    await asyncio.gather(
                        self.tg_sender.send(sender_batches, src_channel),
                        self.qq_sender.send(sender_batches, src_channel),
                    )

                except Exception as e:
                    logger.error(f"[Send] Error sending batch {batch_idx + 1}: {e}")
                    # 继续发送下一个批次

        logger.info(f"[Send] Completed sending {len(batches)} batches")

    def _cleanup_orphaned_files(self):
        """
        启动时清理插件数据目录中的孤儿文件
        """
        if not os.path.exists(self.plugin_data_dir):
            return

        logger.info(f"Cleaning up orphaned files in {self.plugin_data_dir}...")
        # 允许保留的文件列表
        # .session-journal, .session-shm, .session-wal 是 SQLite 的临时文件，必须保留以防止数据库损坏
        allowlist = [
            "data.json",
            "user_session.session",
            "user_session.session-journal",
            "user_session.session-shm",
            "user_session.session-wal",
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
