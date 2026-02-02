"""
Telegram 消息转发插件 - 主入口

本插件用于从 Telegram 频道自动转发消息到其他 Telegram 频道或 QQ 群。
支持消息过滤、媒体文件处理、冷启动等功能。

主要组件：
- Storage: 持久化存储每个频道的最后一条消息ID
- TelegramClientWrapper: Telegram 客户端封装
- Forwarder: 消息转发核心逻辑
- AsyncIOScheduler: 定时任务调度器
"""

import asyncio
import os
import shutil
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger, star, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import StarTools

from .common.storage import Storage
from .core.client import TelegramClientWrapper
from .core.forwarder import Forwarder
from .core.commands import PluginCommands


class Main(star.Star):
    """
    Telegram 转发插件主类

    继承自 AstrBot 的 star.Star 基类，实现插件的生命周期管理。
    """

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        """
        插件初始化

        Args:
            context: AstrBot 上下文对象，提供框架级别的API访问
            config: 插件配置对象

        初始化流程：
        1. 设置数据持久化目录
        2. 初始化存储、客户端、转发器组件
        3. 创建定时任务调度器
        4. 启动 Telegram 客户端（如果配置完整）
        """
        super().__init__(context)
        self.context = context
        self.config = config

        # ========== 设置数据目录 ==========
        self.plugin_data_dir = str(StarTools.get_data_dir())
        if not os.path.exists(self.plugin_data_dir):
            os.makedirs(self.plugin_data_dir)

        # ========== 数据迁移 (Legacy -> New) ==========
        # 旧位置: 插件源码目录 (e.g. data/plugins/astrbot_plugin_telegram_forwarder)
        legacy_dir = os.path.dirname(__file__)
        files_to_migrate = [
            "data.json",
            "user_session.session",
            "user_session.session-journal",
        ]

        for filename in files_to_migrate:
            src = os.path.join(legacy_dir, filename)
            dst = os.path.join(self.plugin_data_dir, filename)

            if os.path.exists(src):
                # 如果目标不存在，或者目标也是空的，才迁移
                should_migrate = False
                if not os.path.exists(dst):
                    should_migrate = True
                elif (
                    filename == "data.json" and os.path.getsize(dst) < 100
                ):  # 可能是空的默认文件
                    should_migrate = True

                if should_migrate:
                    try:
                        shutil.copy2(src, dst)
                        logger.warning(
                            f"[Migration] Moved {filename} from plugin dir to data dir."
                        )

                        # 可选：迁移后重命名源文件作为备份，防止下次误判 (或者保留作为备份)
                        # os.rename(src, src + ".bak")
                    except Exception as e:
                        logger.error(f"[Migration] Failed to move {filename}: {e}")

        # ========== 初始化核心组件 ==========
        # Storage: 负责持久化存储频道消息ID
        self.storage = Storage(os.path.join(self.plugin_data_dir, "data.json"))

        # ========== 处理上传的 Session 文件 ==========
        session_files = self.config.get("telegram_session", [])
        if session_files and isinstance(session_files, list) and len(session_files) > 0:
            uploaded_session_path = session_files[0]
            full_uploaded_path = os.path.join(self.plugin_data_dir, uploaded_session_path)

            if os.path.exists(full_uploaded_path):
                target_session_path = os.path.join(self.plugin_data_dir, "user_session.session")
                
                try:
                    shutil.copy2(full_uploaded_path, target_session_path)
                    logger.info(f"Telegram Forwarder: 已从上传配置同步会话文件: {target_session_path} (源: {full_uploaded_path})")
                except Exception as e:
                    logger.error(f"Telegram Forwarder: 同步会话文件失败: {e}")
            else:
                logger.warning(f"Telegram Forwarder: 配置中的会话文件路径不存在: {full_uploaded_path}")

        # TelegramClientWrapper: 封装 Telegram 客户端连接逻辑
        self.client_wrapper = TelegramClientWrapper(self.config, self.plugin_data_dir)

        # Forwarder: 消息转发核心逻辑，处理消息过滤、媒体下载、多平台发送
        self.forwarder = Forwarder(
            self.config, self.storage, self.client_wrapper, self.plugin_data_dir
        )

        # ========== 初始化定时任务调度器 ==========
        # 使用 APScheduler 的异步调度器，定期检查 Telegram 频道更新
        self.scheduler = AsyncIOScheduler()

        # 初始化命令处理器
        self.command_handler = PluginCommands(context, config, self.forwarder)

        # ========== 配置检查警告 ==========
        # 如果缺少必要的配置，输出警告日志提醒用户
        if not self.config.get("api_id") or not self.config.get("api_hash"):
            logger.warning(
                "Telegram Forwarder: api_id/api_hash missing. Please configure them."
            )

    async def initialize(self):
        """
        插件启动逻辑
        
        执行流程：
        1. 启动 Telegram 客户端连接
        2. 如果连接成功且插件已启用，启动定时任务
        3. 定时任务按照配置的间隔检查频道更新
        """
        # 启动 Telegram 客户端（处理登录、会话恢复等）
        # 如果配置了 api_id 和 api_hash，尝试启动
        if self.client_wrapper.client:
            await self.client_wrapper.start()

        # 检查客户端是否成功连接并授权
        if self.client_wrapper.is_authorized():
            # ========== 启动定时调度器 ==========
            # 仅当插件配置为启用状态时才启动
            if self.config.get("enabled", True):
                # 从配置获取检查间隔，默认 60 秒
                interval = self.config.get("check_interval", 60)

                # 添加定时任务：每隔 interval 秒执行一次 check_updates
                # max_instances=10: 允许最多10个并发任务，确保"快"的频道能绕过"慢"的频道
                self.scheduler.add_job(
                    self.forwarder.check_updates,
                    "interval",
                    seconds=interval,
                    max_instances=10,
                    coalesce=False,
                )

                # 启动调度器
                self.scheduler.start()

                # 记录正在监控的频道列表
                logger.info(
                    f"Monitoring channels: {self.config.get('source_channels')}"
                )

    async def terminate(self):
        """
        插件终止时的清理工作
        """
        logger.info("[Telegram Forwarder] Terminating plugin...")

        # 1. Stop Scheduler
        if self.scheduler.running:
            logger.info("[Telegram Forwarder] Shutting down scheduler...")
            self.scheduler.shutdown(wait=False)
            logger.info("[Telegram Forwarder] Scheduler shutdown complete.")

        # 2. Disconnect Client
        if self.client_wrapper.client:
            logger.info("[Telegram Forwarder] Disconnecting client...")
            try:
                # Force disconnect with short timeout
                await asyncio.wait_for(
                    self.client_wrapper.client.disconnect(), timeout=3.0
                )
                logger.info("[Telegram Forwarder] Client disconnected.")
            except asyncio.TimeoutError:
                logger.warning("[Telegram Forwarder] Client disconnect timed out!")
            except Exception as e:
                logger.error(f"[Telegram Forwarder] Error disconnecting client: {e}")

        logger.info("[Telegram Forwarder] Plugin Stopped")

    # ================= COMMANDS =================

    @filter.command_group("tg")
    def tg(self):
        """Telegram Forwarder 插件管理"""
        pass

    @tg.command("add")
    async def add_channel(self, event: AstrMessageEvent, channel: str):
        """添加监控频道: /tg add <channel>"""
        async for result in self.command_handler.add_channel(event, channel):
            yield result

    @tg.command("rm")
    async def remove_channel(self, event: AstrMessageEvent, channel: str):
        """移除监控频道: /tg rm <channel>"""
        async for result in self.command_handler.remove_channel(event, channel):
            yield result

    @tg.command("ls")
    async def list_channels(self, event: AstrMessageEvent):
        """列出所有监控频道: /tg ls"""
        async for result in self.command_handler.list_channels(event):
            yield result

    @tg.command("check")
    async def force_check(self, event: AstrMessageEvent):
        """立即检查更新: /tg check"""
        async for result in self.command_handler.force_check(event):
            yield result

    @tg.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        async for result in self.command_handler.show_help(event):
            yield result
