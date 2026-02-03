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
        检查所有配置的频道更新并加入待发送队列
        """
        if not self.client_wrapper.is_connected():
            return

        logger.debug("[Capture] 正在检查 Telegram 频道更新...")

        channels_config = self.config.get("source_channels", [])

        async def fetch_one(cfg):
            try:
                channel_name = ""
                start_date = None
                default_interval = 0
                interval = default_interval
                msg_limit = 20

                if isinstance(cfg, dict):
                    channel_name = cfg.get("channel_username", "")
                    if not channel_name: return []
                    s_time = cfg.get("start_time", "")
                    if s_time:
                        try:
                            start_date = datetime.strptime(s_time, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except: pass
                    interval = cfg.get("check_interval", default_interval)
                    msg_limit = cfg.get("msg_limit", 20)
                elif isinstance(cfg, str):
                    parts = [p.strip() for p in cfg.split("|")]
                    if not parts: return []
                    channel_name = parts[0]
                else: return []

                if not channel_name: return []

                now = datetime.now().timestamp()
                last_check = self._channel_last_check.get(channel_name, 0)
                if now - last_check < interval:
                    return []

                lock = self._get_channel_lock(channel_name)
                if lock.locked(): return []

                async with lock:
                    self._channel_last_check[channel_name] = now
                    logger.debug(f"[Capture] 正在拉取频道 {channel_name} 的消息...")
                    messages = await self._fetch_channel_messages(channel_name, start_date, msg_limit)
                    
                    if messages:
                        logger.debug(f"[Capture] 从频道 {channel_name} 捕获到 {len(messages)} 条新消息，准备持久化。")
                        for m in messages:
                            self.storage.add_to_pending_queue(channel_name, m.id, m.date.timestamp(), m.grouped_id)
                    else:
                        logger.debug(f"[Capture] 频道 {channel_name} 没有新消息。")
                    return messages
            except Exception as e:
                logger.error(f"[Capture] 获取 {cfg} 失败: {e}")
                return []

        tasks = [fetch_one(cfg) for cfg in channels_config]
        if tasks:
            await asyncio.gather(*tasks)

        await self.send_pending_messages()

    async def send_pending_messages(self):
        """
        从待发送队列中提取消息并执行转发
        """
        logger.debug("[Send] 正在检测待发送队列...")
        batch_limit = self.config.get("batch_size_limit", 3)
        retention = self.config.get("retention_period", 86400)
        now_ts = datetime.now().timestamp()

        all_pending = self.storage.get_all_pending()
        if not all_pending:
            logger.debug("[Send] 待发送队列为空。")
            return

        valid_pending = []
        expired_count = 0
        for item in all_pending:
            if now_ts - item["time"] <= retention:
                valid_pending.append(item)
            else:
                expired_count += 1
        
        if expired_count > 0:
            logger.debug(f"[Send] 自动清理了 {expired_count} 条已过期消息。")
            self._update_storage_queues(valid_pending)

        if not valid_pending:
            return

        valid_pending.sort(key=lambda x: x["time"], reverse=True)

        to_send_meta = []
        processed_ids = set()
        logical_count = 0 
        
        for item in valid_pending:
            if logical_count >= batch_limit:
                break
            
            if item["id"] in processed_ids:
                continue
            
            if item.get("grouped_id"):
                gid = item["grouped_id"]
                channel = item["channel"]
                album_items = [i for i in valid_pending if i.get("grouped_id") == gid and i["channel"] == channel]
                for a_item in album_items:
                    if a_item["id"] not in processed_ids:
                        to_send_meta.append(a_item)
                        processed_ids.add(a_item["id"])
                logical_count += 1 
            else:
                to_send_meta.append(item)
                processed_ids.add(item["id"])
                logical_count += 1 

        if not to_send_meta:
            return

        channel_to_ids = {}
        for item in to_send_meta:
            c = item["channel"]
            if c not in channel_to_ids: channel_to_ids[c] = []
            channel_to_ids[c].append(item["id"])

        messages_to_process = []
        filter_keywords = self.config.get("filter_keywords", [])
        filter_regex = self.config.get("filter_regex", "")
        filter_hashtags = self.config.get("filter_hashtags", [])

        for channel, ids in channel_to_ids.items():
            try:
                msgs = await self.client.get_messages(channel, ids=ids)
                for m in msgs:
                    if not m: continue
                    
                    text_content = m.text or ""
                    should_skip = False
                    
                    if filter_hashtags:
                        for tag in filter_hashtags:
                            if tag in text_content:
                                logger.debug(f"[Filter] {m.id}: 命中 Hashtag '{tag}'")
                                should_skip = True; break
                    
                    if not should_skip and filter_keywords:
                        for kw in filter_keywords:
                            if kw in text_content:
                                logger.debug(f"[Filter] {m.id}: 命中关键词 '{kw}'")
                                should_skip = True; break
                    
                    if not should_skip and filter_regex:
                        if re.search(filter_regex, text_content, re.IGNORECASE | re.DOTALL):
                            logger.debug(f"[Filter] {m.id}: 命中正则匹配")
                            should_skip = True
                    
                    if not should_skip:
                        messages_to_process.append((channel, m))
            except Exception as e:
                logger.error(f"[Send] 拉取消息失败 {channel}: {e}")

        if not messages_to_process:
            sent_ids = [item["id"] for item in to_send_meta]
            remaining_pending = [item for item in valid_pending if item["id"] not in sent_ids]
            self._update_storage_queues(remaining_pending)
            return

        final_batches = []
        msg_map = {m.id: (c, m) for c, m in messages_to_process}
        processed_ids_in_send = set()
        
        for item in to_send_meta:
            mid = item["id"]
            if mid in msg_map and mid not in processed_ids_in_send:
                channel = item["channel"]
                if item.get("grouped_id"):
                    gid = item["grouped_id"]
                    album_items = [i for i in to_send_meta if i.get("grouped_id") == gid and i["channel"] == channel]
                    album_msgs = []
                    for ai in album_items:
                        if ai["id"] in msg_map:
                            album_msgs.append(msg_map[ai["id"]][1])
                            processed_ids_in_send.add(ai["id"])
                    
                    album_msgs.sort(key=lambda m: m.date)
                    final_batches.append((album_msgs, channel))
                else:
                    final_batches.append(([msg_map[mid][1]], channel))
                    processed_ids_in_send.add(mid)

        try:
            if final_batches:
                await self._send_sorted_messages_in_batches(final_batches)
        except Exception as e:
            logger.error(f"[Send] 转发过程出现错误: {e}")
        finally:
            sent_ids = [item["id"] for item in to_send_meta]
            remaining_pending = [item for item in valid_pending if item["id"] not in sent_ids]
            self._update_storage_queues(remaining_pending)
            
            if sent_ids:
                logger.debug(f"[Send] 批次处理完成。队列剩余: {len(remaining_pending)} 条消息。")

    async def _send_sorted_messages_in_batches(self, batches_with_channel: List[tuple]):
        """发送排好序的消息批次"""
        async with self._global_send_lock:
            for msgs, src_channel in batches_with_channel:
                # 1. 转发到 QQ
                await self.qq_sender.send([msgs], src_channel)
                
                # 2. 转发到 Telegram
                await self.tg_sender.send([msgs], src_channel)

    def _update_storage_queues(self, flat_pending_list: list):
        """
        将打平的待发送列表重新按频道分组并更新到 storage
        """
        # 1. 首先清空所有频道的队列（或者只处理涉及到的频道）
        # 这里为了安全，我们先按频道分组
        channel_queues = {}
        for item in flat_pending_list:
            c = item["channel"]
            if c not in channel_queues:
                channel_queues[c] = []
            
            # 构造原始存储格式
            channel_queues[c].append({
                "id": item["id"],
                "time": item["time"],
                "grouped_id": item.get("grouped_id")
            })
        
        # 2. 获取 storage 中当前已知的所有频道
        all_channels = self.config.get("source_channels", [])
        channel_names = []
        for cfg in all_channels:
            if isinstance(cfg, dict):
                channel_names.append(cfg.get("channel_username"))
            elif isinstance(cfg, str):
                channel_names.append(cfg.split("|")[0].strip())
        
        # 3. 更新 storage
        for cname in channel_names:
            if not cname: continue
            new_queue = channel_queues.get(cname, [])
            self.storage.update_pending_queue(cname, new_queue)

    async def _fetch_channel_messages(
        self, channel_name: str, start_date: Optional[datetime], msg_limit: int = 20
    ) -> List[Message]:
        """
        从单个频道获取新消息
        """
        if not self.storage.get_channel_data(channel_name).get("last_post_id"):
            self.storage.update_last_id(channel_name, 0)

        last_id = self.storage.get_channel_data(channel_name)["last_post_id"]

        try:
            if last_id == 0:
                if start_date:
                    logger.debug(f"[Fetch] {channel_name} 正在从 {start_date} 开始冷启动...")
                    pass
                else:
                    msgs = await self.client.get_messages(channel_name, limit=1)
                    if msgs:
                        self.storage.update_last_id(channel_name, msgs[0].id)
                        logger.debug(f"[Fetch] {channel_name} 初始化成功，起始 ID: {msgs[0].id}")
                    return []

            new_messages = []
            params = {"entity": channel_name, "reverse": True, "limit": msg_limit}

            if last_id > 0:
                params["min_id"] = last_id
            elif start_date:
                params["offset_date"] = start_date
            else:
                params["limit"] = 5

            async for message in self.client.iter_messages(**params):
                if not message.id:
                    continue
                new_messages.append(message)

            if new_messages:
                max_id = max(m.id for m in new_messages)
                self.storage.update_last_id(channel_name, max_id)
                logger.debug(
                    f"[Fetch] {channel_name}: 成功获取 {len(new_messages)} 条新消息 (max_id: {max_id})"
                )

            return new_messages

        except Exception as e:
            logger.error(f"[Fetch] 访问 {channel_name} 失败: {e}")
            return []

    def _cleanup_orphaned_files(self):
        """
        启动时清理插件数据目录中的孤儿文件
        """
        if not os.path.exists(self.plugin_data_dir):
            return

        logger.debug(f"[Cleanup] 正在清理临时文件: {self.plugin_data_dir}")
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

                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                logger.debug(f"[Cleanup] 清理完成，移除了 {deleted_count} 个孤儿文件。")

        except Exception as e:
            logger.error(f"[Cleanup] 清理文件失败: {e}")
