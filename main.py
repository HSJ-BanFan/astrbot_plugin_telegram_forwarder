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
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger, star, AstrBotConfig
from astrbot.api.star import StarTools

from .common.storage import Storage
from .core.client import TelegramClientWrapper
from .core.forwarder import Forwarder

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
            config: 插件配置对象，包含 api_id、api_hash、代理设置等

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
        # 1. 优先使用 StarTools 分配的标准数据目录
        self.plugin_data_dir = str(StarTools.get_data_dir())
        if not os.path.exists(self.plugin_data_dir):
            os.makedirs(self.plugin_data_dir)

        # 2. 兼容性处理：如果代码目录（旧位置）里有 session，强制覆盖到数据目录
        # 这可以解决数据目录里有损坏 session 导致无法更新的问题
        code_dir_session = os.path.join(os.path.dirname(__file__), "user_session.session")
        data_dir_session = os.path.join(self.plugin_data_dir, "user_session.session")
        
        logger.info(f"DEBUG: Checking session migration.\nSrc: {code_dir_session}\nDst: {data_dir_session}")

        if os.path.exists(code_dir_session):
            try:
                import shutil
                # 强制覆盖，确保 relogin.py 生成的新 session 生效
                shutil.copy2(code_dir_session, data_dir_session)
                logger.warning(f"Force migrated session from code dir to data dir.")
            except Exception as e:
                logger.error(f"Failed to migrate session: {e}")

        # ========== 初始化核心组件 ==========
        # Storage: 负责持久化存储频道消息ID
        self.storage = Storage(os.path.join(self.plugin_data_dir, "data.json"))

        # TelegramClientWrapper: 封装 Telegram 客户端连接逻辑
        self.client_wrapper = TelegramClientWrapper(self.config, self.plugin_data_dir)

        # Forwarder: 消息转发核心逻辑，处理消息过滤、媒体下载、多平台发送
        self.forwarder = Forwarder(self.config, self.storage, self.client_wrapper, self.plugin_data_dir)

        # ========== 初始化定时任务调度器 ==========
        # 使用 APScheduler 的异步调度器，定期检查 Telegram 频道更新
        self.scheduler = AsyncIOScheduler()

        # 保存异步任务引用，用于优雅关闭时等待任务完成
        
        self._start_task = None

        # ========== 启动 Telegram 客户端 ==========
        # 如果配置了 api_id 和 api_hash，创建异步任务启动客户端
        # 注意：在 __init__ 中创建任务需要确保事件循环已就绪
        if self.client_wrapper.client:
           self._start_task = asyncio.create_task(self._start())

        # ========== 配置检查警告 ==========
        # 如果缺少必要的配置，输出警告日志提醒用户
        if not self.config.get("api_id") or not self.config.get("api_hash"):
             logger.warning("Telegram Forwarder: api_id/api_hash missing. Please configure them.")

    async def _start(self):
        """
        启动客户端和定时调度器

        执行流程：
        1. 启动 Telegram 客户端连接
        2. 如果连接成功且插件已启用，启动定时任务
        3. 定时任务按照配置的间隔检查频道更新

        Note:
            此方法作为异步任务在 __init__ 中创建，确保在插件加载时自动启动
        """
        # 启动 Telegram 客户端（处理登录、会话恢复等）
        await self.client_wrapper.start()

        # 检查客户端是否成功连接并授权
        if self.client_wrapper.is_authorized():
            # ========== 启动定时调度器 ==========
            # 仅当插件配置为启用状态时才启动
            if self.config.get("enabled", True):
                # 从配置获取检查间隔，默认 60 秒
                interval = self.config.get("check_interval", 60)

                # 添加定时任务：每隔 interval 秒执行一次 check_updates
                self.scheduler.add_job(self.forwarder.check_updates, 'interval', seconds=interval)

                # 启动调度器
                self.scheduler.start()

                # 记录正在监控的频道列表
                logger.info(f"Monitoring channels: {self.config.get('source_channels')}")

    async def terminate(self):
        """
        插件终止时的清理工作

        清理流程：
        1. 停止定时调度器（不等待正在执行的任务完成）
        2. 等待启动任务完成（最多5秒），超时则取消
        3. 断开 Telegram 客户端连接

        Note:
            此方法由 AstrBot 框架在插件卸载时调用
            使用 shutdown(wait=False) 避免阻塞，快速释放资源
        """
        # ========== 停止定时调度器 ==========
        # wait=False: 不等待正在执行的任务完成，立即停止
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

        # ========== 等待启动任务完成 ==========
        # 如果 _start_task 还在运行，等待其完成（最多5秒）
        # 这确保在断开连接前，客户端已完全启动
        if self._start_task:
            try:
                await asyncio.wait_for(self._start_task, timeout=5.0)
            except asyncio.TimeoutError:
                # 超时则取消任务，强制继续清理流程
                self._start_task.cancel()

        # ========== 断开 Telegram 客户端 ==========
        # 优雅地关闭连接，释放会话文件
        if self.client_wrapper.client:
            await self.client_wrapper.client.disconnect()

        logger.info("Telethon Plugin Stopped")
