import asyncio
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon.tl.types import Message  # type: ignore

from astrbot.api import AstrBotConfig, logger, star

from ..common.storage import Storage
from ..common.text_tools import (
    is_numeric_channel_id,
    normalize_telegram_channel_name,
    to_telethon_entity,
)
from .client import TelegramClientWrapper
from .downloader import MediaDownloader
from .filters.message_filter import MessageFilter
from .mergers import MessageMerger
from .senders.qq import QQSender, QQSendSummary
from .senders.telegram import TelegramSender


class Forwarder:
    """
    消息转发器核心类 (Monitor + Dispatcher)
    """

    def __init__(
        self,
        context: star.Context,
        config: AstrBotConfig,
        storage: Storage,
        client_wrapper: TelegramClientWrapper,
        plugin_data_dir: Path,
    ):
        self.context = context
        self.config = config
        self.storage = storage
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client
        self.plugin_data_dir = plugin_data_dir
        self._stopping = False  # 新增停止标志
        self.stats = {
            "forward_success": 0,  # 成功转发的**消息条数**（不是批次）
            "forward_failed": 0,  # 失败的消息条数
            "forward_attempts": 0,  # 尝试转发的总消息条数（成功+失败）
            "acked_messages": 0,
            "failed_messages": 0,
            "deferred_messages": 0,
            "last_reset": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 初始化组件
        self.downloader = MediaDownloader(self.client, plugin_data_dir)

        # 初始化发送器
        self.tg_sender = TelegramSender(self.client, config)
        self.qq_sender = QQSender(self.context, config, self.downloader)

        # 初始化过滤器和合并引擎
        self.message_filter = MessageFilter(config)
        self.message_merger = MessageMerger(config)

        # 启动时清理孤儿文件
        self._cleanup_orphaned_files()

        # 启动时重置不在配置中的频道的 last_post_id
        source_channels = config.get("source_channels", [])
        active_channels = [
            c.get("channel_username")
            for c in source_channels
            if c.get("channel_username")
        ]
        logger.debug(f"[Capture] 当前活跃监控频道列表: {active_channels}")
        self.storage.reset_inactive_channels(active_channels)

        # 任务锁，防止重入 (Key: ChannelName)
        self._channel_locks = {}
        # 上次检查时间 (Key: ChannelName)
        self._channel_last_check = {}
        # 全局发送锁，确保所有频道的消息按顺序发送，避免交错
        self._global_send_lock = asyncio.Lock()
        # 发送任务锁，避免定时发送与立即发送并发执行导致重复发送
        self._send_dispatch_lock = asyncio.Lock()
        self._active_tasks: set[asyncio.Task] = set()
        self._shutdown_complete = asyncio.Event()
        self._shutdown_complete.set()

        # 缓存频道标题 (Key: ChannelUsername, Value: Title)
        self._channel_titles_cache = {}

    def reload_runtime_config(self) -> None:
        """刷新依赖配置快照的运行时组件。"""
        self.message_filter = MessageFilter(self.config)
        self.message_merger = MessageMerger(self.config)
        source_channels = self.config.get("source_channels", [])
        active_channels = [
            c.get("channel_username")
            for c in source_channels
            if c.get("channel_username")
        ]
        self.storage.reset_inactive_channels(active_channels)
        logger.info("[Forwarder] 运行时配置组件已刷新。")

    def _get_channel_lock(self, channel_name: str) -> asyncio.Lock:
        if channel_name not in self._channel_locks:
            self._channel_locks[channel_name] = asyncio.Lock()
        return self._channel_locks[channel_name]

    def _sync_client_refs(self):
        """保持 Forwarder 内部 client 引用与 wrapper 最新 client 一致。"""
        latest = self.client_wrapper.client
        if not latest:
            return
        if self.client is latest:
            return
        self.client = latest
        self.downloader.client = latest
        self.tg_sender.client = latest

    def _track_current_task(self) -> None:
        task = asyncio.current_task()
        if task is None or task in self._active_tasks:
            return

        self._active_tasks.add(task)
        self._shutdown_complete.clear()

        def _cleanup(done_task: asyncio.Task) -> None:
            self._active_tasks.discard(done_task)
            if not self._active_tasks:
                self._shutdown_complete.set()

        task.add_done_callback(_cleanup)

    async def _ensure_client_ready(self) -> bool:
        """确保 client 可用且已连接。"""
        self._sync_client_refs()
        if not self.client_wrapper or not self.client_wrapper.client:
            return False
        try:
            ok = await self.client_wrapper.ensure_connected()
            if ok:
                self._sync_client_refs()
            return ok
        except Exception as e:
            logger.error(f"[Forwarder] reconnect failed: {e}")
            return False

    async def _get_display_name(self, channel_name: str) -> str:
        """获取频道显示名称"""
        forward_cfg = self.config.get("forward_config", {})
        use_title = forward_cfg.get("use_channel_title", True)

        if not use_title:
            return (
                channel_name
                if is_numeric_channel_id(channel_name)
                else f"@{channel_name}"
            )

        # 尝试从缓存获取
        if channel_name in self._channel_titles_cache:
            return self._channel_titles_cache[channel_name]

        # 尝试从 Telegram 获取
        try:
            entity = await self.client.get_entity(to_telethon_entity(channel_name))
            title = getattr(entity, "title", channel_name)
            self._channel_titles_cache[channel_name] = title
            return title
        except Exception as e:
            logger.warning(f"[Capture] 无法获取频道 {channel_name} 的标题: {e}")
            return (
                channel_name
                if is_numeric_channel_id(channel_name)
                else f"@{channel_name}"
            )

    def _get_channel_raw_cfg(self, channel_name: str) -> dict:
        channels_config = self.config.get("source_channels", [])
        channel_name_norm = normalize_telegram_channel_name(channel_name)
        for cfg in channels_config:
            if (
                normalize_telegram_channel_name(cfg.get("channel_username", ""))
                == channel_name_norm
            ):
                return cfg
        return {}

    @staticmethod
    def _is_keyword_matched(pattern_str: str, text: str) -> bool:
        if not pattern_str or not text:
            return False
        pattern_str = pattern_str.lower().strip()
        if not pattern_str:
            return False
        if pattern_str.isascii():
            regex_pattern = rf"(?<![a-zA-Z0-9]){re.escape(pattern_str)}(?![a-zA-Z0-9])"
            return bool(re.search(regex_pattern, text, re.IGNORECASE))
        return pattern_str in text

    @staticmethod
    def _build_message_search_text(msg: Message) -> str:
        text_content = msg.text or ""
        button_text = ""
        if msg.reply_markup and hasattr(msg.reply_markup, "rows"):
            button_parts = []
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    if hasattr(btn, "text") and btn.text:
                        button_parts.append(btn.text)
            button_text = " ".join(button_parts)
        return f"{text_content} {button_text}".strip()

    @staticmethod
    def _is_spoiler_message(msg: Message) -> bool:
        entities = getattr(msg, "entities", None) or []
        has_text_spoiler = any(
            getattr(entity, "__class__", type(entity)).__name__
            == "MessageEntitySpoiler"
            for entity in entities
        )

        media = getattr(msg, "media", None)
        has_media_spoiler = bool(getattr(media, "spoiler", False))
        if not has_media_spoiler:
            has_media_spoiler = bool(
                getattr(getattr(msg, "photo", None), "spoiler", False)
            )
        if not has_media_spoiler:
            has_media_spoiler = bool(
                getattr(getattr(msg, "document", None), "spoiler", False)
            )

        return has_text_spoiler or has_media_spoiler

    @staticmethod
    def _positive_int(value, fallback: int, minimum: int = 1) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(minimum, parsed)

    @staticmethod
    def _message_age_seconds(msg: Message) -> float:
        msg_date = getattr(msg, "date", None)
        if not isinstance(msg_date, datetime):
            return float("inf")
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc).timestamp() - msg_date.timestamp()

    async def _prepare_album_boundaries(
        self, channel_name: str, messages: list[Message], msg_limit: int
    ) -> list[Message]:
        """避免把刚到达或被上限截断的 Telegram 相册半组入队。"""
        if not messages:
            return messages

        grouped_tail = getattr(messages[-1], "grouped_id", None)
        if not grouped_tail:
            return messages

        forward_cfg = self.config.get("forward_config", {})
        settle_seconds = self._positive_int(
            forward_cfg.get("album_settle_seconds", 8), 8, minimum=0
        )
        lookahead_limit = self._positive_int(
            forward_cfg.get("album_lookahead_limit", 20), 20
        )
        fetch_limit = self._positive_int(msg_limit, 20)

        if self._message_age_seconds(messages[-1]) < settle_seconds:
            tail_start_index = len(messages) - 1
            while (
                tail_start_index > 0
                and getattr(messages[tail_start_index - 1], "grouped_id", None)
                == grouped_tail
            ):
                tail_start_index -= 1
            stable_messages = messages[:tail_start_index]
            logger.info(
                f"[Merge] 频道 {channel_name} 最新相册 {grouped_tail} 仍在到达窗口内，暂缓 {len(messages) - len(stable_messages)} 条。"
            )
            return stable_messages

        if len(messages) < fetch_limit:
            return messages

        known_ids = {msg.id for msg in messages}
        max_id = max(known_ids)
        extra_messages = []
        try:
            async for message in self.client.iter_messages(
                entity=to_telethon_entity(channel_name),
                reverse=True,
                min_id=max_id,
                limit=lookahead_limit,
            ):
                if not isinstance(message, Message) or not message.id:
                    continue
                if getattr(message, "grouped_id", None) != grouped_tail:
                    break
                if message.id in known_ids:
                    continue
                extra_messages.append(message)
                known_ids.add(message.id)
        except Exception as e:
            logger.debug(f"[Merge] 频道 {channel_name} 相册边界补拉失败: {e}")

        if extra_messages:
            logger.info(
                f"[Merge] 频道 {channel_name} 相册 {grouped_tail} 因批次上限被截断，已补拉 {len(extra_messages)} 条。"
            )
            messages = [*messages, *extra_messages]
            if len(extra_messages) >= lookahead_limit:
                tail_start_index = len(messages) - 1
                while (
                    tail_start_index > 0
                    and getattr(messages[tail_start_index - 1], "grouped_id", None)
                    == grouped_tail
                ):
                    tail_start_index -= 1
                stable_messages = messages[:tail_start_index]
                logger.info(
                    f"[Merge] 频道 {channel_name} 相册 {grouped_tail} 补拉达到上限 {lookahead_limit}，暂缓 {len(messages) - len(stable_messages)} 条。"
                )
                return stable_messages

        return messages

    def _is_monitor_matched(self, msg: Message, effective_cfg: dict) -> bool:
        monitor_keywords = effective_cfg.get("monitor_keywords", [])
        monitor_patterns = effective_cfg.get("monitor_regex_patterns", [])
        if not monitor_keywords and not monitor_patterns:
            return False

        full_check_text = self._build_message_search_text(msg)
        check_text_lower = full_check_text.lower()

        for kw in monitor_keywords:
            if self._is_keyword_matched(kw, check_text_lower):
                return True

        for pattern in monitor_patterns:
            if not pattern:
                continue
            try:
                if re.search(pattern, full_check_text, re.IGNORECASE | re.DOTALL):
                    return True
            except re.error as e:
                logger.error(f"[Monitor] 非法正则表达式 '{pattern}': {e}")
        return False

    def _is_text_filter_matched(self, msg: Message, effective_cfg: dict) -> bool:
        full_check_text = self._build_message_search_text(msg)
        should_skip = False
        check_text_lower = full_check_text.lower()

        filter_keywords = effective_cfg["filter_keywords"]
        if filter_keywords:
            for kw in filter_keywords:
                if self._is_keyword_matched(kw, check_text_lower):
                    logger.info(f"[Filter] 消息 {msg.id} 命中关键词 '{kw}'")
                    return True

        patterns = effective_cfg.get("filter_regex_patterns", [])

        for pattern in patterns:
            if pattern:
                try:
                    if re.search(
                        pattern,
                        full_check_text,
                        re.IGNORECASE | re.DOTALL,
                    ):
                        logger.info(
                            f"[Filter] 消息 {msg.id} 命中正则匹配: {pattern[:30]}..."
                        )
                        should_skip = True
                        break
                except re.error as e:
                    logger.error(f"[Filter] 非法正则表达式 '{pattern}': {e}")

        return should_skip

    def _get_effective_config(self, channel_name: str):
        """
        获取有效配置 (分过滤原则: 全局与频道配置均需符合)
        """
        # 1. 获取全局配置
        global_cfg = self.config.get("forward_config", {})

        # 2. 获取该频道的特定配置
        channel_cfg = self._get_channel_raw_cfg(channel_name)

        # 3. 核心过滤项交集逻辑 ( Strictest Policy )

        # 3.1 转发类型 (交集)
        g_types = set(
            global_cfg.get("forward_types", ["文字", "图片", "视频", "音频", "文件"])
        )
        c_types = set(
            channel_cfg.get("forward_types", ["文字", "图片", "视频", "音频", "文件"])
        )
        forward_types = list(g_types.intersection(c_types))

        # 3.2 文件大小限制 (取非零最小值)
        g_max = global_cfg.get("max_file_size", 0)
        c_max = channel_cfg.get("max_file_size", 0)
        if g_max > 0 and c_max > 0:
            max_file_size = min(g_max, c_max)
        else:
            max_file_size = g_max or c_max

        # 3.3 关键词与正则
        ignore_global_text_filters = channel_cfg.get("ignore_global_filters", False)

        g_filter_keywords = global_cfg.get("filter_keywords", [])
        c_filter_keywords = channel_cfg.get("filter_keywords", [])

        if ignore_global_text_filters:
            filter_keywords = c_filter_keywords
        else:
            filter_keywords = list(set(g_filter_keywords + c_filter_keywords))

        filter_patterns = []
        g_regex = global_cfg.get("filter_regex", "").strip()
        c_regex = channel_cfg.get("filter_regex", "").strip()

        if not ignore_global_text_filters and g_regex:
            filter_patterns.append(g_regex)
        if c_regex:
            filter_patterns.append(c_regex)

        # 3.4 监听关键词与监听正则 (并集监听：命中任何一个都触发立即转发)
        monitor_keywords = list(
            set(
                global_cfg.get("monitor_keywords", [])
                + channel_cfg.get("monitor_keywords", [])
            )
        )
        monitor_patterns = []
        if global_cfg.get("monitor_regex"):
            monitor_patterns.append(global_cfg["monitor_regex"])
        if channel_cfg.get("monitor_regex"):
            monitor_patterns.append(channel_cfg["monitor_regex"])

        # 3.5 发送间隔与检测间隔
        check_interval = channel_cfg.get("check_interval") or global_cfg.get(
            "check_interval", 60
        )
        send_interval = global_cfg.get("send_interval", 60)

        # 3.6 查重开关（目前只支持全局控制）
        enable_deduplication = global_cfg.get("enable_deduplication", True)

        # 3.7 优先级校验 (小于 1 视作 0)
        priority = channel_cfg.get("priority", 0)
        if priority < 1:
            priority = 0

        # 3.8 媒体文本排除 & 剧透过滤
        exclude_text_on_media = channel_cfg.get(
            "exclude_text_on_media", "继承全局"
        ) == "开启" or (
            channel_cfg.get("exclude_text_on_media", "继承全局") == "继承全局"
            and global_cfg.get("exclude_text_on_media", False)
        )

        filter_spoiler_messages = channel_cfg.get(
            "filter_spoiler_messages", "继承全局"
        ) == "开启" or (
            channel_cfg.get("filter_spoiler_messages", "继承全局") == "继承全局"
            and global_cfg.get("filter_spoiler_messages", False)
        )

        # 3.9 Markdown 链接剥离规则
        strip_global = global_cfg.get("strip_markdown_links", False)
        strip_channel_raw = channel_cfg.get("strip_markdown_links", "继承全局")

        if strip_channel_raw == "开启":
            strip_markdown_links = True
        elif strip_channel_raw == "关闭":
            strip_markdown_links = False
        else:
            strip_markdown_links = strip_global

        channel_specific_targets = channel_cfg.get("target_qq_sessions", [])
        if channel_specific_targets:  # 非空列表 → 使用频道专属配置
            effective_qq_targets = channel_specific_targets
            has_exclusive_qq_targets = True
            # 是否为专属目标配置（用于决定是否参与混合频道大合并）
        else:
            effective_qq_targets = self.config.get("target_qq_session", [])
            has_exclusive_qq_targets = False

        return {
            "forward_types": forward_types,
            "max_file_size": max_file_size,
            "filter_keywords": filter_keywords,
            "filter_regex_patterns": filter_patterns,
            "monitor_keywords": monitor_keywords,
            "monitor_regex_patterns": monitor_patterns,
            "check_interval": check_interval,
            "send_interval": send_interval,
            "enable_deduplication": enable_deduplication,
            "priority": priority,
            "exclude_text_on_media": exclude_text_on_media,
            "filter_spoiler_messages": filter_spoiler_messages,
            "strip_markdown_links": strip_markdown_links,
            "start_time": channel_cfg.get("start_time", ""),
            "msg_limit": channel_cfg.get("msg_limit", 20),
            "effective_target_qq_sessions": effective_qq_targets,
            "has_exclusive_qq_sessions": has_exclusive_qq_targets,
        }

    def _is_curfew(self) -> bool:
        """检查当前是否处于宵禁时间"""
        forward_cfg = self.config.get("forward_config", {})
        curfew_time = forward_cfg.get("curfew_time", "").strip()
        if not curfew_time:
            return False

        try:
            # 格式校验 11:11-14:12
            if "-" not in curfew_time:
                return False

            start_str, end_str = curfew_time.split("-")
            start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
            end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
            now_time = datetime.now().time()

            if start_time <= end_time:
                # 非跨天情况 (例: 11:11-14:12)
                return start_time <= now_time <= end_time
            else:
                # 跨天情况 (例: 23:00-07:00)
                return now_time >= start_time or now_time <= end_time
        except Exception as e:
            logger.error(f"[Forwarder] 宵禁时间格式解析错误: {curfew_time}. 错误: {e}")
            return False

    async def check_updates(self):
        """
        检查所有配置的频道更新并加入待发送队列
        """
        self._track_current_task()
        if self._stopping:
            return

        if not await self._ensure_client_ready():
            return

        if self._is_curfew():
            logger.debug("[Capture] 当前处于宵禁时间，跳过拉取任务。")
            return

        channels_config = self.config.get("source_channels", [])
        logger.debug(
            f"[Capture] 开始检查 Telegram 频道更新 (共 {len(channels_config)} 个频道)..."
        )

        async def fetch_one(cfg):
            try:
                channel_name = normalize_telegram_channel_name(
                    cfg.get("channel_username", "")
                )
                if not channel_name:
                    return []

                effective_cfg = self._get_effective_config(channel_name)

                interval = effective_cfg["check_interval"]
                msg_limit = effective_cfg["msg_limit"]

                # 1. 优先检查抓取间隔，没到时间直接退出，避免无效开销
                now = datetime.now().timestamp()
                last_check = self._channel_last_check.get(channel_name, 0)
                if now - last_check < interval:
                    return []

                # 2. 到时间了，再获取该频道上次拉取的最后一条消息 ID
                channel_data = self.storage.get_channel_data(channel_name)
                last_id = channel_data.get("last_post_id", 0)

                start_date = None
                s_time = effective_cfg.get("start_time", "")
                # 只有在 last_id 为 0 (说明从未成功拉取过，需要冷启动) 时，才执行日期转换逻辑
                if last_id == 0 and s_time:
                    try:
                        dt_naive = datetime.strptime(s_time, "%Y-%m-%d")
                        # 设为北京时间 00:00:00 (UTC+8)
                        tz_beijing = timezone(timedelta(hours=8))
                        dt_beijing = dt_naive.replace(tzinfo=tz_beijing)
                        # 转换为 UTC
                        start_date = dt_beijing.astimezone(timezone.utc)
                        logger.debug(
                            f"[Capture] 频道 {channel_name} 冷启动日期转换: 输入 {s_time} (北京时间) -> 转换为 UTC: {start_date}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[Capture] 频道 {channel_name} 冷启动日期格式错误 '{s_time}': {e}"
                        )
                        pass

                lock = self._get_channel_lock(channel_name)
                if lock.locked():
                    logger.debug(
                        f"[Capture] 频道 {channel_name} 正在抓取中，跳过本次。"
                    )
                    return []

                async with lock:
                    if self._stopping:
                        return []
                    self._channel_last_check[channel_name] = now
                    logger.debug(f"[Capture] 正在拉取: {channel_name}")
                    messages = await self._fetch_channel_messages(
                        channel_name, start_date, msg_limit
                    )
                    messages = await self._prepare_album_boundaries(
                        channel_name, messages, msg_limit
                    )
                    monitor_hit_count = 0
                    monitor_hit_targets = []

                    if messages:
                        message_pairs = [(channel_name, m) for m in messages]
                        defer_from_index = self.message_merger.find_defer_from_index(
                            channel_name, message_pairs
                        )
                        if defer_from_index is not None:
                            deferred_count = len(messages) - defer_from_index
                            if defer_from_index == 0:
                                logger.info(
                                    f"[Merge] 频道 {channel_name} 触发合并规则但后续消息未凑齐，暂缓 {deferred_count} 条消息。"
                                )
                                return []
                            logger.info(
                                f"[Merge] 频道 {channel_name} 触发合并规则但后续消息未凑齐，本轮先入队 {defer_from_index} 条，暂缓 {deferred_count} 条。"
                            )
                            messages = messages[:defer_from_index]
                            message_pairs = message_pairs[:defer_from_index]

                        merged_pairs = self.message_merger.merge_messages(
                            message_pairs
                        )
                        messages = [m for _, m in merged_pairs]

                        # 先加入队列，再更新 last_id
                        pending_items = []
                        for m in messages:
                            if self._stopping:
                                return []
                            is_monitored = self._is_monitor_matched(m, effective_cfg)
                            if is_monitored:
                                monitor_hit_count += 1
                                monitor_hit_targets.append((channel_name, m.id))
                            pending_items.append(
                                {
                                    "id": m.id,
                                    "time": m.date.timestamp(),
                                    "grouped_id": getattr(
                                        m, "_merge_group_id", None
                                    )
                                    or m.grouped_id,
                                    "is_cold_start": (
                                        last_id == 0 and start_date is not None
                                    ),
                                    "is_monitored": is_monitored,
                                    "merge_rule_class": getattr(
                                        m, "_merge_rule_class", ""
                                    ),
                                }
                            )

                        self.storage.add_batch_to_pending_queue(
                            channel_name, pending_items
                        )

                        max_id = max(m.id for m in messages)
                        self.storage.update_last_id(channel_name, max_id)

                        logger.info(
                            f"[Capture] 频道 {channel_name} 成功拉取 {len(messages)} 条消息 (ID: {max_id})"
                            + (
                                f" | 监听命中 {monitor_hit_count} 条"
                                if monitor_hit_count
                                else ""
                            )
                        )
                    else:
                        logger.debug(f"[Capture] 频道 {channel_name} 无新消息。")
                    return monitor_hit_targets
            except Exception as e:
                error_msg = str(e)
                if "database disk image is malformed" in error_msg:
                    logger.error(
                        "[Capture] Telethon 数据库文件损坏 (malformed)。可尝试重载插件以恢复..."
                    )
                    session_path = str(self.plugin_data_dir / "user_session")
                    from .client import TelegramClientWrapper

                    TelegramClientWrapper.clear_cache(session_path)
                logger.error(f"[Capture] 检查频道 {cfg} 时出现未捕获异常: {e}")
                import traceback

                logger.error(traceback.format_exc())
                return []
            finally:
                if "channel_name" in locals() and channel_name:
                    logger.debug(f"[Capture] 频道 {channel_name} 检查任务结束。")

        tasks = [fetch_one(cfg) for cfg in channels_config]
        if tasks:
            monitor_hits = await asyncio.gather(*tasks)
            if self._stopping:
                return
            monitor_targets = set()
            for hits in monitor_hits:
                for item in hits:
                    monitor_targets.add(item)
            if monitor_targets:
                logger.info(
                    f"[Monitor] 本轮抓取命中监听规则 {len(monitor_targets)} 条，立即仅转发命中消息。"
                )
                await self.send_pending_messages(
                    force_immediate=True,
                    monitored_only=True,
                    monitor_targets=monitor_targets,
                )

    @staticmethod
    def _merge_send_summaries(
        current: QQSendSummary | None, incoming: QQSendSummary | None
    ) -> QQSendSummary | None:
        if incoming is None:
            return current
        if current is None:
            return incoming

        def merge_target_map(
            first: dict[int, tuple[str, ...]], second: dict[int, tuple[str, ...]]
        ) -> dict[int, tuple[str, ...]]:
            merged: dict[int, tuple[str, ...]] = dict(first)
            for index, targets in second.items():
                merged[index] = tuple(
                    dict.fromkeys((*merged.get(index, ()), *targets))
                )
            return merged

        return QQSendSummary(
            acked_batch_indexes=tuple(
                dict.fromkeys(
                    (*current.acked_batch_indexes, *incoming.acked_batch_indexes)
                )
            ),
            failed_batch_indexes=tuple(
                dict.fromkeys(
                    (*current.failed_batch_indexes, *incoming.failed_batch_indexes)
                )
            ),
            deferred_batch_indexes=tuple(
                dict.fromkeys(
                    (*current.deferred_batch_indexes, *incoming.deferred_batch_indexes)
                )
            ),
            error_types={**current.error_types, **incoming.error_types},
            target_sessions=tuple(
                dict.fromkeys(
                    (
                        *getattr(current, "target_sessions", ()),
                        *getattr(incoming, "target_sessions", ()),
                    )
                )
            ),
            target_sessions_by_batch=merge_target_map(
                getattr(current, "target_sessions_by_batch", {}),
                getattr(incoming, "target_sessions_by_batch", {}),
            ),
            completed_target_sessions=merge_target_map(
                getattr(current, "completed_target_sessions", {}),
                getattr(incoming, "completed_target_sessions", {}),
            ),
        )

    @staticmethod
    def _qq_batch_message_counts(batches: list) -> list[int]:
        counts: list[int] = []
        for batch in batches:
            if batch and all(isinstance(item, list) for item in batch):
                counts.extend(len(sub_batch) for sub_batch in batch)
            else:
                counts.append(len(batch))
        return counts

    def _remove_dispatched_batches(
        self, batch_meta: list[dict], send_summary: QQSendSummary
    ) -> None:
        strict_ack = self.config.get("forward_config", {}).get(
            "send_result_strict_ack", False
        ) or self._has_multi_qq_targets(batch_meta, send_summary)
        removable_by_channel: dict[str, list[int]] = {}

        if strict_ack:
            indexes = set(send_summary.acked_batch_indexes)
        else:
            deferred_indexes = {
                index
                for index in send_summary.deferred_batch_indexes
                if 0 <= index < len(batch_meta)
            }
            indexes = set(range(len(batch_meta))) - deferred_indexes

        for index in indexes:
            if index < 0 or index >= len(batch_meta):
                continue
            meta = batch_meta[index]
            removable_by_channel.setdefault(meta["channel"], []).extend(meta["ids"])

        for channel, ids in removable_by_channel.items():
            self.storage.remove_ids_from_pending(channel, ids)

    @staticmethod
    def _normalize_target_list(targets: object) -> list[str]:
        if not isinstance(targets, list):
            return []
        return [str(target) for target in targets if str(target)]

    @classmethod
    def _completed_qq_targets_for_items(cls, items: list[dict]) -> list[str]:
        if not items:
            return []
        completed_sets = []
        for item in items:
            completed_sets.append(
                set(
                    cls._normalize_target_list(
                        item.get("completed_qq_targets", [])
                    )
                )
            )
        if not completed_sets:
            return []
        return sorted(set.intersection(*completed_sets))

    def _mark_completed_qq_targets(
        self, batch_meta: list[dict], send_summary: QQSendSummary
    ) -> None:
        if not hasattr(self.storage, "mark_pending_qq_targets_completed"):
            return
        completed_by_batch = getattr(send_summary, "completed_target_sessions", {})
        for index, completed_targets in completed_by_batch.items():
            if index < 0 or index >= len(batch_meta) or not completed_targets:
                continue
            meta = batch_meta[index]
            self.storage.mark_pending_qq_targets_completed(
                meta["channel"], meta["ids"], list(completed_targets)
            )

    @staticmethod
    def _has_multi_qq_targets(
        batch_meta: list[dict], send_summary: QQSendSummary
    ) -> bool:
        target_sessions = getattr(send_summary, "target_sessions", ())
        if len(target_sessions) > 1:
            return True
        target_sessions_by_batch = getattr(send_summary, "target_sessions_by_batch", {})
        if any(len(targets) > 1 for targets in target_sessions_by_batch.values()):
            return True
        return any(len(meta.get("target_sessions", [])) > 1 for meta in batch_meta)

    def _completed_qq_targets_for_batch(self, src_channel: str, msgs: list) -> list[str]:
        msg_ids = {msg.id for msg in msgs}
        pending_items = [
            item
            for item in self.storage.get_all_pending()
            if item["channel"] == src_channel and item["id"] in msg_ids
        ]
        return self._completed_qq_targets_for_items(pending_items)

    async def send_pending_messages(
        self,
        force_immediate: bool = False,
        monitored_only: bool = False,
        monitor_targets: set | None = None,
    ):
        """
        从待发送队列中提取消息并执行转发
        """
        if self._stopping:
            return

        if self._is_curfew():
            logger.debug("[Send] 当前处于宵禁时间，跳过转发任务。")
            return

        self._track_current_task()
        async with self._send_dispatch_lock:
            if self._stopping:
                return
            if force_immediate:
                logger.debug("[Send] 监听命中触发立即转发，跳过发送周期等待。")

            self.stats.setdefault("acked_messages", 0)
            self.stats.setdefault("failed_messages", 0)
            self.stats.setdefault("deferred_messages", 0)

            all_pending = self.storage.get_all_pending()
            queue_size = len(all_pending) if all_pending else 0

            if not all_pending:
                logger.debug("[Send] 正在检测待发送队列... 队列为空，无需处理。")
                return

            # 获取全局配置用于提取公共参数
            global_cfg = self.config.get("forward_config", {})

            batch_limit = global_cfg.get("batch_size_limit", 3)
            retention = global_cfg.get("retention_period", 86400)
            now_ts = datetime.now().timestamp()

            # 统计各频道积压情况
            stats = {}
            monitor_pending_count = 0
            for item in all_pending:
                c = item["channel"]
                stats[c] = stats.get(c, 0) + 1
                if item.get("is_monitored", False):
                    monitor_pending_count += 1

            stats_str = ", ".join([f"{c}({n}条)" for c, n in stats.items()])
            logger.debug(
                f"[Send] 队列状态: 总计 {queue_size} 条"
                f"{f' | 监听命中待发 {monitor_pending_count} 条' if monitor_pending_count else ''}"
                f" | 详情: {stats_str}"
            )

            valid_pending = []
            expired_count = 0
            for item in all_pending:
                # 冷启动消息不检测过期时间
                if item.get("is_cold_start", False) or (
                    now_ts - item["time"] <= retention
                ):
                    valid_pending.append(item)
                else:
                    expired_count += 1

            if expired_count > 0:
                self.storage.cleanup_expired_pending(retention)
                all_pending = self.storage.get_all_pending()
                valid_pending = all_pending

            if monitored_only:
                if monitor_targets:
                    monitored_group_keys = {
                        (item["channel"], item["grouped_id"])
                        for item in valid_pending
                        if (item["channel"], item["id"]) in monitor_targets
                        and item.get("grouped_id") is not None
                    }
                    valid_pending = [
                        item
                        for item in valid_pending
                        if (item["channel"], item["id"]) in monitor_targets
                        or (
                            item.get("grouped_id") is not None
                            and (item["channel"], item["grouped_id"])
                            in monitored_group_keys
                        )
                    ]
                else:
                    valid_pending = [
                        item
                        for item in valid_pending
                        if item.get("is_monitored", False)
                    ]
                if not valid_pending:
                    logger.debug("[Send] 监听即时转发未匹配到可发送消息。")
                    return
                # 监听即时转发只处理本次命中范围，不按常规批次掺杂其他消息
                batch_limit = len(valid_pending)
                logger.debug(
                    f"[Send] 监听即时转发本次仅处理 {batch_limit} 条命中消息。"
                )

            retryable_pending = []
            for item in valid_pending:
                next_retry_at = item.get("next_retry_at", 0)
                if next_retry_at and next_retry_at > now_ts:
                    continue
                retryable_pending.append(item)
            valid_pending = retryable_pending

            if not valid_pending:
                return

            # 优先级排序逻辑
            source_channels = self.config.get("source_channels", [])
            channel_priorities = {
                cfg.get("channel_username"): self._get_effective_config(
                    cfg.get("channel_username")
                )["priority"]
                for cfg in source_channels
                if cfg.get("channel_username")
            }

            # 排序规则：
            # 1. 监听命中消息优先
            # 2. 优先级 (priority) 从大到小
            # 3. 实时消息优先于补旧消息
            # 4. 实时消息按时间倒序，补旧消息按时间正序
            def sorting_key(x):
                prio = channel_priorities.get(x["channel"], 0)
                is_cold = x.get("is_cold_start", False)
                is_monitored = x.get("is_monitored", False)
                return (
                    0 if is_monitored else 1,
                    -prio,
                    is_cold,
                    -x["time"] if not is_cold else x["time"],
                )

            valid_pending.sort(key=sorting_key)

            logger.debug(f"[Send] 开始处理待发送队列 (批次上限: {batch_limit})")

            final_batches = []
            all_processed_meta = []
            all_fetched_keys = set()
            logical_sent_count = 0
            processed_keys: set[tuple[str, int]] = set()
            pending_idx = 0

            while logical_sent_count < batch_limit and pending_idx < len(valid_pending):
                # 1. 提取下一组元数据进行尝试
                current_try_meta = []
                current_try_logical_units = 0
                needed_units = batch_limit - logical_sent_count

                # 记录本轮尝试提取的逻辑单元对应的 ID，用于后续分组
                current_try_logical_map = {}  # {逻辑 ID: [meta_items]}

                while current_try_logical_units < needed_units and pending_idx < len(
                    valid_pending
                ):
                    item = valid_pending[pending_idx]
                    pending_idx += 1
                    item_key = (item["channel"], item["id"])

                    if item_key in processed_keys:
                        continue

                    logical_id = (
                        item.get("grouped_id")
                        or f"single_{item['channel']}_{item['id']}"
                    )
                    if item.get("grouped_id"):
                        gid = item["grouped_id"]
                        channel = item["channel"]
                        album_items = [
                            i
                            for i in valid_pending
                            if i.get("grouped_id") == gid and i["channel"] == channel
                        ]

                        unit_items = []
                        for a_item in album_items:
                            a_item_key = (a_item["channel"], a_item["id"])
                            if a_item_key not in processed_keys:
                                unit_items.append(a_item)
                                processed_keys.add(a_item_key)

                        if unit_items:
                            current_try_meta.extend(unit_items)
                            current_try_logical_map[logical_id] = unit_items
                            current_try_logical_units += 1
                    else:
                        current_try_meta.append(item)
                        current_try_logical_map[logical_id] = [item]
                        processed_keys.add(item_key)
                        current_try_logical_units += 1

                if not current_try_meta:
                    break

                all_processed_meta.extend(current_try_meta)

                # 2. 抓取与初步过滤
                channel_to_ids = {}
                id_to_meta = {
                    (item["channel"], item["id"]): item for item in current_try_meta
                }
                for item in current_try_meta:
                    c = item["channel"]
                    mid = item["id"]
                    if c not in channel_to_ids:
                        channel_to_ids[c] = []
                    channel_to_ids[c].append(mid)

                raw_fetched_messages = []
                skipped_grouped_ids = set()  # (频道, grouped_id)
                individually_skipped_keys = set()

                # 发送周期 re-fetch 前确保 Telegram 客户端可用
                if not await self._ensure_client_ready():
                    logger.warning(
                        "[Send] Telegram 客户端不可用，跳过本轮 re-fetch，保留队列。"
                    )
                    break

                for channel, ids in channel_to_ids.items():
                    try:
                        effective_cfg = self._get_effective_config(channel)
                        msgs = await self.client.get_messages(
                            to_telethon_entity(channel), ids=ids
                        )
                        msg_iterable = []
                        if msgs is not None:
                            msg_iterable = msgs if isinstance(msgs, list) else [msgs]
                        for m in msg_iterable:
                            if not m or not isinstance(m, Message):
                                continue
                            raw_fetched_messages.append((channel, m))
                            all_fetched_keys.add((channel, m.id))
                            meta = id_to_meta.get((channel, m.id))

                            if (
                                meta
                                and meta.get("grouped_id") is not None
                                and self._is_text_filter_matched(m, effective_cfg)
                            ):
                                individually_skipped_keys.add((channel, m.id))
                                skipped_grouped_ids.add(
                                    (channel, meta["grouped_id"])
                                )
                                continue

                            # 类型过滤
                            forward_types = effective_cfg["forward_types"]
                            max_file_size = effective_cfg["max_file_size"]
                            msg_type = "文字"
                            if m.photo:
                                msg_type = "图片"
                            elif m.video:
                                msg_type = "视频"
                            elif m.voice or m.audio:
                                msg_type = "音频"
                            elif m.document:
                                msg_type = "文件"

                            if msg_type not in forward_types:
                                logger.info(
                                    f"[Filter] 消息 {m.id} 类型 '{msg_type}' 不在允许列表中，跳过。"
                                )
                                individually_skipped_keys.add((channel, m.id))
                                continue

                            # 检查文件大小
                            m._max_file_size = max_file_size
                            if not m.photo and max_file_size > 0:
                                file_size = 0
                                if hasattr(m, "media") and m.media:
                                    if hasattr(m.media, "document") and hasattr(
                                        m.media.document, "size"
                                    ):
                                        file_size = m.media.document.size
                                    elif hasattr(m.file, "size"):
                                        file_size = m.file.size
                                if file_size > max_file_size * 1024 * 1024:
                                    logger.info(
                                        f"[Filter] 消息 {m.id} 文件大小 ({file_size / 1024 / 1024:.2f} MB) 超过限制 ({max_file_size} MB)，跳过。"
                                    )
                                    individually_skipped_keys.add((channel, m.id))
                                    continue

                            if effective_cfg.get(
                                "filter_spoiler_messages", False
                            ) and self._is_spoiler_message(m):
                                logger.info(
                                    f"[Filter] 消息 {m.id} 为遮罩/剧透消息，已跳过。"
                                )
                                individually_skipped_keys.add((channel, m.id))
                                if meta and meta.get("grouped_id"):
                                    skipped_grouped_ids.add(
                                        (channel, meta["grouped_id"])
                                    )
                                continue

                            # 关键词/正则过滤
                            should_skip = self._is_text_filter_matched(
                                m, effective_cfg
                            )

                            if should_skip:
                                individually_skipped_keys.add((channel, m.id))
                                if meta and meta.get("grouped_id"):
                                    skipped_grouped_ids.add(
                                        (channel, meta["grouped_id"])
                                    )
                    except Exception as e:
                        error_msg = str(e)
                        if "database disk image is malformed" in error_msg:
                            logger.error(
                                "[Send] Telethon 数据库文件损坏 (malformed)。可尝试重载插件以恢复..."
                            )
                            session_path = str(
                                self.plugin_data_dir / "user_session"
                            )
                            from .client import TelegramClientWrapper

                            TelegramClientWrapper.clear_cache(session_path)
                        logger.error(f"[Send] 拉取消息失败 {channel}: {e}")

                # 3. 应用过滤并构建本轮有效的 batches
                msg_map = {(c, m.id): m for c, m in raw_fetched_messages}

                for logical_id, unit_items in current_try_logical_map.items():
                    channel = unit_items[0]["channel"]
                    is_album = unit_items[0].get("grouped_id") is not None

                    if is_album:
                        gid = unit_items[0]["grouped_id"]
                        if (channel, gid) in skipped_grouped_ids:
                            continue  # 整个相册被跳过

                        album_msgs = []
                        for ui in unit_items:
                            mid = ui["id"]
                            msg_key = (channel, mid)
                            if (
                                msg_key in msg_map
                                and msg_key not in individually_skipped_keys
                            ):
                                album_msgs.append(msg_map[msg_key])

                        if album_msgs:
                            album_msgs.sort(key=lambda m: m.date)
                            final_batches.append((album_msgs, channel))
                            logical_sent_count += 1
                    else:
                        mid = unit_items[0]["id"]
                        msg_key = (channel, mid)
                        if (
                            msg_key in msg_map
                            and msg_key not in individually_skipped_keys
                        ):
                            final_batches.append(([msg_map[msg_key]], channel))
                            logical_sent_count += 1

            refetch_miss_retained_by_channel = {}
            refetch_miss_cleaned_by_channel = {}
            refetch_miss_attempted_at = datetime.now().timestamp()

            def reset_refetch_miss_retry_state(channel: str, ids: list[int]) -> None:
                if not ids:
                    return
                id_set = set(ids)
                if hasattr(self.storage, "get_channel_data"):
                    channel_data = self.storage.get_channel_data(channel)
                    for pending_item in channel_data.get("pending_queue", []):
                        if (
                            pending_item.get("id") in id_set
                            and pending_item.get("last_error_type") != "refetch_miss"
                        ):
                            pending_item["retry_count"] = 0
                    return
                if hasattr(self.storage, "pending"):
                    for pending_item in self.storage.pending:
                        if (
                            pending_item.get("channel") == channel
                            and pending_item.get("id") in id_set
                            and pending_item.get("last_error_type") != "refetch_miss"
                        ):
                            pending_item["retry_count"] = 0

            try:
                refetch_miss_max_retries = int(
                    global_cfg.get("pending_refetch_miss_max_retries", 3)
                )
            except (TypeError, ValueError):
                refetch_miss_max_retries = 3
            if refetch_miss_max_retries < 1:
                refetch_miss_max_retries = 1
            for item in all_processed_meta:
                item_key = (item["channel"], item["id"])
                if item_key in all_fetched_keys:
                    continue
                channel = item["channel"]
                item_id = item["id"]
                previous_refetch_miss_count = (
                    int(item.get("retry_count", 0))
                    if item.get("last_error_type") == "refetch_miss"
                    else 0
                )
                next_refetch_miss_count = previous_refetch_miss_count + 1
                if next_refetch_miss_count >= refetch_miss_max_retries:
                    refetch_miss_cleaned_by_channel.setdefault(channel, []).append(
                        item_id
                    )
                else:
                    refetch_miss_retained_by_channel.setdefault(channel, []).append(
                        item_id
                    )

            if not final_batches:
                if all_processed_meta:
                    # 只移除成功拉取到的消息（可能被过滤）；未拉取到的消息按 re-fetch miss 计次，超限后清理。
                    removable_ids = [
                        item["id"]
                        for item in all_processed_meta
                        if (item["channel"], item["id"]) in all_fetched_keys
                    ]
                    if removable_ids:
                        chan_to_ids_processed = {}
                        for item in all_processed_meta:
                            if (item["channel"], item["id"]) in all_fetched_keys:
                                c = item["channel"]
                                if c not in chan_to_ids_processed:
                                    chan_to_ids_processed[c] = []
                                chan_to_ids_processed[c].append(item["id"])
                        for channel, ids in chan_to_ids_processed.items():
                            self.storage.remove_ids_from_pending(channel, ids)
                    for channel, ids in refetch_miss_retained_by_channel.items():
                        reset_refetch_miss_retry_state(channel, ids)
                        self.storage.mark_pending_retry(
                            channel,
                            ids,
                            error_type="refetch_miss",
                            target_session="",
                            base_delay=global_cfg.get(
                                "pending_retry_base_delay_sec", 60
                            ),
                            max_delay=global_cfg.get(
                                "pending_retry_max_delay_sec", 1800
                            ),
                            attempted_at=refetch_miss_attempted_at,
                        )
                    for channel, ids in refetch_miss_cleaned_by_channel.items():
                        self.storage.remove_ids_from_pending(channel, ids)
                    retained_ids = [
                        item_id
                        for ids in refetch_miss_retained_by_channel.values()
                        for item_id in ids
                    ]
                    cleaned_ids = [
                        item_id
                        for ids in refetch_miss_cleaned_by_channel.values()
                        for item_id in ids
                    ]
                    logger.info(
                        f"[Send] 本批次 {len(all_processed_meta)} 条消息均未进入发送队列: "
                        f"过滤移除 {len(removable_ids)} 条"
                        f"{f'，保留重试 {len(retained_ids)} 条' if retained_ids else ''}"
                        f"{f'，超限清理 {len(cleaned_ids)} 条' if cleaned_ids else ''}"
                    )
                return

            attempted_send_count = sum(len(msgs) for msgs, _ in final_batches)
            send_summary = None
            batch_meta = []
            for msgs, channel in final_batches:
                effective_cfg = self._get_effective_config(channel)
                target_sessions = effective_cfg.get("effective_target_qq_sessions", [])
                batch_meta.append(
                    {
                        "channel": channel,
                        "ids": [msg.id for msg in msgs],
                        "qq_attempted": bool(target_sessions),
                        "target_session": target_sessions[0] if target_sessions else "",
                        "target_sessions": list(target_sessions),
                    }
                )
            queued_keys = {
                (meta["channel"], msg_id)
                for meta in batch_meta
                for msg_id in meta["ids"]
            }
            filtered_fetched_by_channel = {}
            for item in all_processed_meta:
                item_id = item["id"]
                channel = item["channel"]
                if (channel, item_id) not in all_fetched_keys or (
                    channel,
                    item_id,
                ) in queued_keys:
                    continue
                filtered_fetched_by_channel.setdefault(channel, []).append(item_id)
            try:
                send_summary = await self._send_sorted_messages_in_batches(
                    final_batches
                )
            except Exception as e:
                logger.error(f"[Send] 转发过程出现错误: {e}")
                should_preserve_failed = global_cfg.get(
                    "send_result_strict_ack", False
                ) or any(len(meta.get("target_sessions", [])) > 1 for meta in batch_meta)
                if should_preserve_failed:
                    send_summary = QQSendSummary(
                        acked_batch_indexes=(),
                        failed_batch_indexes=tuple(
                            index
                            for index, meta in enumerate(batch_meta)
                            if meta.get("qq_attempted", False)
                        ),
                        deferred_batch_indexes=(),
                        error_types={
                            index: "send_failed"
                            for index, meta in enumerate(batch_meta)
                            if meta.get("qq_attempted", False)
                        },
                    )
                else:
                    send_summary = QQSendSummary(
                        acked_batch_indexes=tuple(range(len(batch_meta))),
                        failed_batch_indexes=(),
                        deferred_batch_indexes=(),
                        error_types={},
                    )
            finally:
                effective_strict_ack = False
                if send_summary is not None:
                    attempted_at = datetime.now().timestamp()
                    strict_ack = global_cfg.get(
                        "send_result_strict_ack", False
                    ) or self._has_multi_qq_targets(batch_meta, send_summary)
                    effective_strict_ack = strict_ack
                    if strict_ack:
                        acked_indexes = {
                            index
                            for index in send_summary.acked_batch_indexes
                            if 0 <= index < len(batch_meta)
                        }
                        attempted_indexes = {
                            index
                            for index, meta in enumerate(batch_meta)
                            if meta.get("qq_attempted", False)
                        }
                        removable_indexes = acked_indexes | (
                            set(range(len(batch_meta))) - attempted_indexes
                        )
                        removable_summary = QQSendSummary(
                            acked_batch_indexes=tuple(sorted(removable_indexes)),
                            failed_batch_indexes=tuple(
                                index
                                for index in send_summary.failed_batch_indexes
                                if 0 <= index < len(batch_meta)
                                and batch_meta[index].get("qq_attempted", False)
                            ),
                            deferred_batch_indexes=tuple(
                                index
                                for index in send_summary.deferred_batch_indexes
                                if 0 <= index < len(batch_meta)
                                and batch_meta[index].get("qq_attempted", False)
                            ),
                            error_types={
                                index: error_type
                                for index, error_type in send_summary.error_types.items()
                                if 0 <= index < len(batch_meta)
                                and batch_meta[index].get("qq_attempted", False)
                            },
                        )
                        stats_summary = QQSendSummary(
                            acked_batch_indexes=tuple(sorted(acked_indexes)),
                            failed_batch_indexes=removable_summary.failed_batch_indexes,
                            deferred_batch_indexes=removable_summary.deferred_batch_indexes,
                            error_types=removable_summary.error_types,
                        )
                    else:
                        removable_summary = send_summary
                        stats_summary = send_summary

                    for index in removable_summary.failed_batch_indexes:
                        meta = batch_meta[index]
                        self.storage.mark_pending_retry(
                            meta["channel"],
                            meta["ids"],
                            error_type=removable_summary.error_types.get(
                                index, "send_failed"
                            ),
                            target_session=meta.get("target_session", ""),
                            base_delay=global_cfg.get(
                                "pending_retry_base_delay_sec", 60
                            ),
                            max_delay=global_cfg.get(
                                "pending_retry_max_delay_sec", 1800
                            ),
                            attempted_at=attempted_at,
                        )

                    for index in removable_summary.deferred_batch_indexes:
                        meta = batch_meta[index]
                        self.storage.mark_pending_retry(
                            meta["channel"],
                            meta["ids"],
                            error_type=removable_summary.error_types.get(
                                index, "deferred"
                            ),
                            target_session=meta.get("target_session", ""),
                            base_delay=global_cfg.get(
                                "pending_retry_base_delay_sec", 60
                            ),
                            max_delay=global_cfg.get(
                                "pending_retry_max_delay_sec", 1800
                            ),
                            attempted_at=attempted_at,
                        )

                    for channel, ids in refetch_miss_retained_by_channel.items():
                        reset_refetch_miss_retry_state(channel, ids)
                        self.storage.mark_pending_retry(
                            channel,
                            ids,
                            error_type="refetch_miss",
                            target_session="",
                            base_delay=global_cfg.get(
                                "pending_retry_base_delay_sec", 60
                            ),
                            max_delay=global_cfg.get(
                                "pending_retry_max_delay_sec", 1800
                            ),
                            attempted_at=refetch_miss_attempted_at,
                        )

                    for channel, ids in refetch_miss_cleaned_by_channel.items():
                        self.storage.remove_ids_from_pending(channel, ids)

                    for index in removable_summary.acked_batch_indexes:
                        meta = batch_meta[index]
                        self.storage.clear_pending_retry(meta["channel"], meta["ids"])

                    self._mark_completed_qq_targets(batch_meta, send_summary)

                    acked_count = sum(
                        len(batch_meta[index]["ids"])
                        for index in stats_summary.acked_batch_indexes
                        if 0 <= index < len(batch_meta)
                    )
                    failed_count = sum(
                        len(batch_meta[index]["ids"])
                        for index in stats_summary.failed_batch_indexes
                        if 0 <= index < len(batch_meta)
                    )
                    deferred_count = sum(
                        len(batch_meta[index]["ids"])
                        for index in stats_summary.deferred_batch_indexes
                        if 0 <= index < len(batch_meta)
                    )
                    self.stats["acked_messages"] += acked_count
                    self.stats["failed_messages"] += failed_count
                    self.stats["deferred_messages"] += deferred_count

                    self._remove_dispatched_batches(batch_meta, removable_summary)
                else:
                    acked_count = 0
                    failed_count = 0
                    deferred_count = 0
                for channel, ids in filtered_fetched_by_channel.items():
                    self.storage.remove_ids_from_pending(channel, ids)

                if all_processed_meta:
                    processed_count = len(all_processed_meta)
                    skipped_count = processed_count - attempted_send_count
                    if effective_strict_ack:
                        msg = f"[Send] 处理完成: 尝试 {attempted_send_count}"
                    else:
                        msg = f"[Send] 处理完成: 成功 {attempted_send_count}"
                    if skipped_count > 0:
                        msg += f" | 跳过 {skipped_count}"
                    new_all_pending = self.storage.get_all_pending()
                    msg += f" | 剩余队列: {len(new_all_pending)}"
                    logger.info(msg)
                    logger.info(
                        f"[Send] 本轮完成: ACK {acked_count} | 失败保留 {failed_count} | 延后重试 {deferred_count} | 剩余队列 {len(new_all_pending)}"
                    )

    async def _send_sorted_messages_in_batches(self, batches_with_channel: list[tuple]):
        """
        按频道分组后，依次执行 QQ 和 Telegram 转发，并进行成功/失败统计

        Args:
            batches_with_channel: List[(List[Message], str)]  每个元素是 (一批消息, 源频道名)
        """
        async with self._global_send_lock:
            forward_cfg = self.config.get("forward_config", {})
            strict_ack = forward_cfg.get("send_result_strict_ack", False)
            default_qq_summary = QQSendSummary(
                acked_batch_indexes=()
                if strict_ack
                else tuple(range(len(batches_with_channel))),
                failed_batch_indexes=tuple(range(len(batches_with_channel)))
                if strict_ack
                else (),
                deferred_batch_indexes=(),
                error_types={},
            )
            qq_summary = None
            merge_threshold = forward_cfg.get("qq_merge_threshold", 0)
            merge_mode = forward_cfg.get("qq_big_merge_mode", "独立频道")

            if merge_threshold <= 1:
                merge_mode = "关闭"

            # ─── 本次调用尝试转发的总消息条数 ───
            attempted_count = sum(len(msgs) for msgs, _ in batches_with_channel)
            self.stats["forward_attempts"] += attempted_count
            logger.debug(f"[Stats] 本次发送调度尝试转发 {attempted_count} 条消息")

            from collections import defaultdict

            qq_send_budget_raw = forward_cfg.get("qq_send_logical_unit_budget", 0)
            try:
                qq_send_budget = int(qq_send_budget_raw)
            except (TypeError, ValueError):
                qq_send_budget = 0
            if qq_send_budget < 0:
                qq_send_budget = 0

            qq_send_groups = defaultdict(list)
            deferred_qq_indexes: list[int] = []
            completed_qq_indexes: list[int] = []
            qq_allowed_count = 0
            completed_qq_targets_by_batch: dict[int, list[str]] = {}
            completed_target_sessions_by_batch: dict[int, tuple[str, ...]] = {}

            for batch_index, (msgs, src_channel) in enumerate(batches_with_channel):
                effective_cfg = self._get_effective_config(src_channel)
                target_sessions = self._normalize_target_list(
                    effective_cfg["effective_target_qq_sessions"]
                )
                if not target_sessions:
                    continue
                completed_targets = self._completed_qq_targets_for_batch(
                    src_channel, msgs
                )
                if completed_targets:
                    completed_qq_targets_by_batch[batch_index] = completed_targets
                    if set(target_sessions).issubset(set(completed_targets)):
                        completed_qq_indexes.append(batch_index)
                        completed_target_sessions_by_batch[batch_index] = tuple(
                            completed_targets
                        )
                        continue
                if qq_send_budget > 0 and qq_allowed_count >= qq_send_budget:
                    deferred_qq_indexes.append(batch_index)
                    continue
                qq_send_groups[src_channel].append((batch_index, msgs))
                qq_allowed_count += 1

            budget_summary = QQSendSummary(
                acked_batch_indexes=tuple(completed_qq_indexes),
                failed_batch_indexes=(),
                deferred_batch_indexes=tuple(deferred_qq_indexes),
                error_types={},
                completed_target_sessions=completed_target_sessions_by_batch,
            )

            # ─── QQ 转发部分 ───
            if merge_mode == "混合所有频道":
                # 分离：有专属群的频道强制独立，无专属群的才参与混合
                mixable_batches = []
                mixable_channels = set()
                exclusive_tasks = []

                for src_channel, msg_groups in qq_send_groups.items():
                    effective_cfg = self._get_effective_config(src_channel)
                    target_sessions = effective_cfg["effective_target_qq_sessions"]
                    if not target_sessions:
                        continue

                    if effective_cfg["has_exclusive_qq_sessions"]:
                        # 专属群 → 独立发送，不参与混合
                        display_name = await self._get_display_name(src_channel)
                        exclusive_tasks.append(
                            (
                                [index for index, _ in msg_groups],
                                self._send_to_qq_and_count(
                                    src_channel=src_channel,
                                    batches=[msgs for _, msgs in msg_groups],
                                    batch_indexes=[index for index, _ in msg_groups],
                                    display_name=display_name,
                                    effective_cfg=effective_cfg,
                                    is_mixed=False,
                                    completed_qq_targets_by_batch=completed_qq_targets_by_batch,
                                ),
                            )
                        )
                        logger.debug(f"[QQ] {src_channel} 使用专属群，走独立发送")
                    else:
                        # 无专属群 → 可参与混合
                        mixable_batches.extend(msg_groups)
                        mixable_channels.add(src_channel)

                # 先并发执行所有专属群发送
                if exclusive_tasks:
                    exclusive_results = await asyncio.gather(
                        *(task for _, task in exclusive_tasks), return_exceptions=True
                    )
                    for (batch_indexes, _), result in zip(
                        exclusive_tasks, exclusive_results
                    ):
                        if isinstance(result, BaseException):
                            if not isinstance(result, Exception):
                                raise result
                            qq_summary = self._merge_send_summaries(
                                qq_summary,
                                QQSendSummary(
                                    failed_batch_indexes=tuple(batch_indexes),
                                    error_types={
                                        batch_index: type(result).__name__.lower()
                                        for batch_index in batch_indexes
                                    },
                                ),
                            )
                            continue
                        qq_summary = self._merge_send_summaries(qq_summary, result)

                # 处理混合大合并
                if (
                    mixable_batches
                    and len(mixable_channels) > 1
                    and len(mixable_batches) >= merge_threshold
                ):
                    involved_list = sorted(mixable_channels)
                    logger.info(
                        f"[QQ 大合并] 混合模式触发 | "
                        f"频道数 {len(involved_list)} | 逻辑批次 {len(mixable_batches)} ≥ {merge_threshold}"
                    )

                    first_channel = involved_list[0]
                    display_name = await self._get_display_name(first_channel)
                    effective_cfg = self._get_effective_config(first_channel)

                    mixed_summary = await self._send_to_qq_and_count(
                        src_channel=first_channel,
                        batches=[
                            [msgs for _, msgs in mixable_batches]
                        ],  # 注意包一层，表示整组合并
                        batch_indexes=[index for index, _ in mixable_batches],
                        display_name=display_name,
                        effective_cfg=effective_cfg,
                        is_mixed=True,
                        involved_channels=involved_list,
                        completed_qq_targets_by_batch=completed_qq_targets_by_batch,
                    )
                    qq_summary = self._merge_send_summaries(qq_summary, mixed_summary)
                else:
                    # 未达混合阈值 → 按频道普通发送
                    for src_channel in sorted(mixable_channels):
                        groups = qq_send_groups.get(src_channel, [])
                        if not groups:
                            continue

                        display_name = await self._get_display_name(src_channel)
                        effective_cfg = self._get_effective_config(src_channel)
                        channel_summary = await self._send_to_qq_and_count(
                            src_channel=src_channel,
                            batches=[msgs for _, msgs in groups],
                            batch_indexes=[index for index, _ in groups],
                            display_name=display_name,
                            effective_cfg=effective_cfg,
                            is_mixed=False,
                            completed_qq_targets_by_batch=completed_qq_targets_by_batch,
                        )
                        qq_summary = self._merge_send_summaries(
                            qq_summary, channel_summary
                        )

            else:
                # 非混合模式 → 逐频道发送
                for src_channel, msg_groups in qq_send_groups.items():
                    effective_cfg = self._get_effective_config(src_channel)
                    target_sessions = effective_cfg["effective_target_qq_sessions"]
                    if not target_sessions:
                        continue

                    display_name = await self._get_display_name(src_channel)

                    if merge_mode == "关闭":
                        channel_summary = await self._send_to_qq_and_count(
                            src_channel=src_channel,
                            batches=[msgs for _, msgs in msg_groups],
                            batch_indexes=[index for index, _ in msg_groups],
                            display_name=display_name,
                            effective_cfg=effective_cfg,
                            is_mixed=False,
                            completed_qq_targets_by_batch=completed_qq_targets_by_batch,
                        )
                        qq_summary = self._merge_send_summaries(
                            qq_summary, channel_summary
                        )
                    elif merge_mode == "独立频道":
                        if len(msg_groups) >= merge_threshold:
                            channel_summary = await self._send_to_qq_and_count(
                                src_channel=src_channel,
                                batches=[
                                    [msgs for _, msgs in msg_groups]
                                ],  # 包一层表示合并
                                batch_indexes=[index for index, _ in msg_groups],
                                display_name=display_name,
                                effective_cfg=effective_cfg,
                                is_mixed=False,
                                completed_qq_targets_by_batch=completed_qq_targets_by_batch,
                            )
                            qq_summary = self._merge_send_summaries(
                                qq_summary, channel_summary
                            )
                        else:
                            channel_summary = await self._send_to_qq_and_count(
                                src_channel=src_channel,
                                batches=[msgs for _, msgs in msg_groups],
                                batch_indexes=[index for index, _ in msg_groups],
                                display_name=display_name,
                                effective_cfg=effective_cfg,
                                is_mixed=False,
                                completed_qq_targets_by_batch=completed_qq_targets_by_batch,
                            )
                            qq_summary = self._merge_send_summaries(
                                qq_summary, channel_summary
                            )

            if completed_qq_indexes or deferred_qq_indexes:
                qq_summary = self._merge_send_summaries(qq_summary, budget_summary)

            # ─── Telegram 转发部分 ───
            tg_target = self.config.get("target_channel", "").strip()
            if tg_target:
                pending_by_key = {
                    (item["channel"], item["id"]): item
                    for item in self.storage.get_all_pending()
                }
                for msgs, src_channel in batches_with_channel:
                    batch_ids = [msg.id for msg in msgs]
                    if batch_ids and all(
                        pending_by_key.get((src_channel, msg_id), {}).get(
                            "last_tg_target", ""
                        )
                        == tg_target
                        for msg_id in batch_ids
                    ):
                        continue
                    try:
                        effective_cfg = self._get_effective_config(src_channel)
                        await self.tg_sender.send(
                            batches=[msgs],
                            src_channel=src_channel,
                            effective_cfg=effective_cfg,
                        )
                        self.stats["forward_success"] += len(msgs)
                        self.storage.mark_pending_tg_forwarded(
                            src_channel, batch_ids, tg_target
                        )
                        logger.debug(
                            f"[Stats] TG 转发成功 {len(msgs)} 条  {src_channel} → {tg_target}"
                        )
                    except Exception as e:
                        self.stats["forward_failed"] += len(msgs)
                        logger.error(
                            f"[Stats] TG 转发失败 {len(msgs)} 条  {src_channel} → {tg_target} : {e}"
                        )

            return qq_summary or default_qq_summary

    async def _send_to_qq_and_count(
        self,
        src_channel: str,
        batches: list,
        batch_indexes: list[int],
        display_name: str,
        effective_cfg: dict,
        is_mixed: bool = False,
        involved_channels: list[str] | None = None,
        completed_qq_targets_by_batch: dict[int, list[str]] | None = None,
    ):
        batch_message_counts = self._qq_batch_message_counts(batches)
        try:
            local_completed_targets_by_batch: dict[int, tuple[str, ...]] = {}
            if completed_qq_targets_by_batch:
                for local_index, global_index in enumerate(batch_indexes):
                    completed_targets = self._normalize_target_list(
                        completed_qq_targets_by_batch.get(global_index, [])
                    )
                    if not completed_targets:
                        continue
                    local_completed_targets_by_batch[local_index] = tuple(
                        completed_targets
                    )
            qq_summary = await self.qq_sender.send(
                batches=batches,
                src_channel=src_channel,
                display_name=display_name,
                effective_cfg=effective_cfg,
                involved_channels=involved_channels if is_mixed else None,
                completed_target_sessions_by_batch=local_completed_targets_by_batch
                or None,
            )
            if qq_summary is None:
                return QQSendSummary()
            success_count = sum(
                batch_message_counts[index]
                for index in qq_summary.acked_batch_indexes
                if 0 <= index < len(batch_message_counts)
            )
            self.stats["forward_success"] += success_count
            logger.debug(f"[Stats] QQ 转发成功 {success_count} 条  @{src_channel}")
            return QQSendSummary(
                acked_batch_indexes=tuple(
                    batch_indexes[index]
                    for index in qq_summary.acked_batch_indexes
                    if 0 <= index < len(batch_indexes)
                ),
                failed_batch_indexes=tuple(
                    batch_indexes[index]
                    for index in qq_summary.failed_batch_indexes
                    if 0 <= index < len(batch_indexes)
                ),
                deferred_batch_indexes=tuple(
                    batch_indexes[index]
                    for index in qq_summary.deferred_batch_indexes
                    if 0 <= index < len(batch_indexes)
                ),
                error_types={
                    batch_indexes[index]: error_type
                    for index, error_type in qq_summary.error_types.items()
                    if 0 <= index < len(batch_indexes)
                },
                target_sessions=getattr(qq_summary, "target_sessions", ()),
                target_sessions_by_batch={
                    batch_indexes[index]: targets
                    for index, targets in getattr(
                        qq_summary, "target_sessions_by_batch", {}
                    ).items()
                    if 0 <= index < len(batch_indexes)
                },
                completed_target_sessions={
                    batch_indexes[index]: targets
                    for index, targets in getattr(
                        qq_summary, "completed_target_sessions", {}
                    ).items()
                    if 0 <= index < len(batch_indexes)
                },
            )
        except Exception as e:
            failed_count = sum(batch_message_counts)
            self.stats["forward_failed"] += failed_count
            logger.error(f"[Stats] QQ 转发失败 {failed_count} 条  @{src_channel} : {e}")
            return QQSendSummary(
                acked_batch_indexes=(),
                failed_batch_indexes=tuple(batch_indexes),
                deferred_batch_indexes=(),
                error_types={
                    batch_index: type(e).__name__.lower()
                    for batch_index in batch_indexes
                },
            )

    def stop(self):
        """停止转发器工作"""
        self._stopping = True

    async def shutdown(self, timeout: float = 10.0) -> None:
        """等待运行中的任务结束；超时后取消剩余任务。"""
        self._stopping = True

        pending_tasks = [task for task in self._active_tasks if not task.done()]
        if not pending_tasks:
            return

        try:
            await asyncio.wait_for(self._shutdown_complete.wait(), timeout=timeout)
            return
        except asyncio.TimeoutError:
            logger.warning(
                f"[Forwarder] 等待在途任务结束超时，准备取消剩余 {len(pending_tasks)} 个任务。"
            )

        for task in pending_tasks:
            task.cancel()

        with suppress(Exception):
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    async def _fetch_channel_messages(
        self,
        channel_name: str,
        start_date: datetime | None,
        msg_limit: int = 20,
        _retried: bool = False,
    ) -> list[Message]:
        """
        从单个频道获取新消息
        """
        if not self.storage.get_channel_data(channel_name).get("last_post_id"):
            self.storage.update_last_id(channel_name, 0)

        last_id = self.storage.get_channel_data(channel_name)["last_post_id"]
        logger.debug(
            f"[Fetch] 频道: {channel_name} | 记录的最新 ID (last_id): {last_id}"
        )

        try:
            if self._stopping:
                return []
            if not await self._ensure_client_ready():
                logger.error(f"[Fetch] {channel_name}: client is not connected")
                return []

            effective_cfg = self._get_effective_config(channel_name)
            enable_dedup = effective_cfg.get("enable_deduplication", True)

            # 0. 获取频道实体并记录 ID (用于查重)
            entity = await self.client.get_input_entity(
                to_telethon_entity(channel_name)
            )
            if hasattr(entity, "channel_id"):
                self.storage.update_channel_id(channel_name, entity.channel_id)

            new_messages = []

            # 1. 如果没有上次拉取的 ID
            if last_id == 0:
                if start_date:
                    # 执行冷启动：从指定日期开始向后抓取
                    params = {
                        "entity": to_telethon_entity(channel_name),
                        "reverse": True,
                        "offset_date": start_date,
                        "limit": 1000,  # 冷启动设置安全上限
                    }
                    from datetime import timezone as _tz

                    _beijing_tz = _tz(timedelta(hours=8))
                    _display_date = start_date.astimezone(_beijing_tz)
                    logger.info(
                        f"[Fetch] {channel_name}: 首次运行，执行冷启动，从 {_display_date.strftime('%Y-%m-%d')} 开始拉取历史消息"
                    )
                else:
                    # 无冷启动设置：初始化 last_id 为最新消息 ID，不搬运旧消息
                    msgs = await self.client.get_messages(
                        to_telethon_entity(channel_name), limit=1
                    )
                    if msgs:
                        self.storage.update_last_id(channel_name, msgs[0].id)
                        logger.info(
                            f"[Fetch] {channel_name}: 首次运行且无冷启动设置，初始化 ID -> {msgs[0].id}"
                        )
                    return []
            else:
                # 2. 正常增量抓取
                params = {
                    "entity": to_telethon_entity(channel_name),
                    "reverse": True,
                    "min_id": last_id,
                    "limit": msg_limit,
                }
                logger.debug(f"[Fetch] {channel_name}: 增量拉取，ID > {last_id}")

            async for message in self.client.iter_messages(**params):
                if self._stopping:
                    break
                if not isinstance(message, Message):
                    continue
                if not message.id:
                    continue

                # --- 转发查重逻辑 ---
                if enable_dedup and message.fwd_from and message.fwd_from.from_id:
                    from telethon.tl.types import PeerChannel  # type: ignore

                    if isinstance(message.fwd_from.from_id, PeerChannel):
                        src_channel_id = message.fwd_from.from_id.channel_id
                        orig_msg_id = message.fwd_from.channel_post

                        # 查找该 ID 是否对应我们正在监控的某个频道
                        src_channel_name = self.storage.get_channel_name_by_id(
                            src_channel_id
                        )
                        if src_channel_name:
                            # 检查原消息是否已经处理过 (通过比较原频道的 last_post_id)
                            src_data = self.storage.get_channel_data(src_channel_name)
                            src_last_id = src_data.get("last_post_id", 0)

                            if orig_msg_id <= src_last_id:
                                logger.debug(
                                    f"[Fetch] 频道 {channel_name} 的消息 {message.id} 是转发自监控频道 {src_channel_name} 的旧消息 (原 ID: {orig_msg_id} <= 已处理 ID: {src_last_id})，自动跳过。"
                                )
                                continue
                # --- 转发查重逻辑结束 ---

                new_messages.append(message)

            return new_messages

        except Exception as e:
            error_msg = str(e)
            if (
                not _retried
            ) and "Cannot send requests while disconnected" in error_msg:
                logger.warning(
                    f"[Fetch] {channel_name}: disconnected, reconnect and retry once"
                )
                if await self._ensure_client_ready():
                    return await self._fetch_channel_messages(
                        channel_name, start_date, msg_limit, _retried=True
                    )
            if "database disk image is malformed" in error_msg:
                logger.error(
                    "[Fetch] Telethon 数据库文件损坏 (malformed)。建议重载插件。"
                )
            logger.error(f"[Fetch] {channel_name}: 访问失败 - {e}")
            return []

    def _cleanup_orphaned_files(self):
        """
        启动时清理插件数据目录中的孤儿文件
        """
        plugin_data_dir = self.plugin_data_dir
        if not plugin_data_dir.exists():
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
            for file_path in plugin_data_dir.iterdir():
                if file_path.name in allowlist:
                    continue

                if file_path.is_file():
                    try:
                        file_path.unlink()
                        deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                logger.debug(f"[Cleanup] 清理完成，移除了 {deleted_count} 个孤儿文件。")

        except Exception as e:
            logger.error(f"[Cleanup] 清理文件失败: {e}")
