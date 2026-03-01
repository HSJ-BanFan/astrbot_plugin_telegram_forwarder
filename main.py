import asyncio
import os
import shutil
import filecmp
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger, star, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import StarTools

from .common.storage import Storage
from .core.client import TelegramClientWrapper
from .core.forwarder import Forwarder
from .core.commands import PluginCommands


class Main(star.Star):
    """Telegram 转发插件主类"""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        """
        插件初始化
        """
        super().__init__(context)
        self.context = context
        self.config = config
        self.bot = None


        # ========== 设置数据目录 ==========
        self.plugin_data_dir = str(StarTools.get_data_dir())
        if not os.path.exists(self.plugin_data_dir):
            os.makedirs(self.plugin_data_dir)

        # ========== 初始化核心组件 ==========
        self.storage = Storage(os.path.join(self.plugin_data_dir, "data.json"))

        # ========== 处理上传的 Session 文件 ==========
        session_files = self.config.get("telegram_session", [])
        if session_files and isinstance(session_files, list) and len(session_files) > 0:
            uploaded_session_path = session_files[0]
            full_uploaded_path = os.path.join(self.plugin_data_dir, uploaded_session_path)

            if os.path.exists(full_uploaded_path):
                target_session_path = os.path.join(self.plugin_data_dir, "user_session.session")
                
                should_copy = True
                if os.path.exists(target_session_path):
                    try:
                        if filecmp.cmp(full_uploaded_path, target_session_path, shallow=False):
                            should_copy = False
                            logger.debug("[Main] 会话文件未变化，跳过同步。")
                    except Exception as e:
                        logger.warning(f"[Main] 比较会话文件失败: {e}")

                if should_copy:
                    try:
                        shutil.copy2(full_uploaded_path, target_session_path)
                        logger.debug(f"[Main] 已从上传配置同步会话文件: {target_session_path}")
                        # 客户端缓存键使用 user_session（无 .session 后缀）
                        session_key_path = os.path.join(self.plugin_data_dir, "user_session")
                        TelegramClientWrapper.clear_cache(session_key_path)
                        logger.debug("[Main] 会话文件已更新，已清理客户端缓存。")
                            
                    except Exception as e:
                        logger.error(f"[Main] 同步会话文件失败 (可能被占用): {e}")
            else:
                logger.warning(f"[Main] 配置中的会话文件路径不存在: {full_uploaded_path}")

        # TelegramClientWrapper: 封装 Telegram 客户端连接逻辑
        self.client_wrapper = TelegramClientWrapper(self.config, self.plugin_data_dir)

        # Forwarder: 消息转发核心逻辑
        self.forwarder = Forwarder(
            self.context, self.config, self.storage, self.client_wrapper, self.plugin_data_dir
        )

        # ========== 初始化定时任务调度器 ==========
        self.scheduler = AsyncIOScheduler()

        # 初始化命令处理器
        self.command_handler = PluginCommands(context, config, self.forwarder, self.scheduler)

        # ========== 配置检查警告 ==========
        if not self.config.get("api_id") or not self.config.get("api_hash"):
            logger.warning(
                "Telegram Forwarder: 缺少 api_id 或 api_hash，请在配置中填写。"
            )

    async def initialize(self):
        """
        插件启动逻辑
        """
        # 启动 Telegram 客户端（处理登录、会话恢复等）
        if self.client_wrapper.client:
            logger.info("正在尝试连接 Telegram 客户端...")
            await self.client_wrapper.start()
        
        # 检查客户端是否成功连接并授权
        is_authorized = self.client_wrapper.is_authorized()
        logger.info(f"Telegram 客户端授权状态: {'已授权' if is_authorized else '未授权'}")

        if is_authorized:
            # ========== 启动定时调度器 ==========
            # 从全局 forward_config 对象中获取间隔
            forward_config = self.config.get("forward_config", {})
            check_interval = forward_config.get("check_interval", 60)
            send_interval = forward_config.get("send_interval", 60)

            # 任务 1: 检查更新 (Capture)
            check_start_time = datetime.now() + timedelta(seconds=5)
            self.scheduler.add_job(
                self.forwarder.check_updates,
                "interval",
                seconds=check_interval,
                max_instances=1,
                coalesce=True,
                next_run_time=check_start_time
            )

            # 任务 2: 执行发送 (Send)
            send_start_time = datetime.now() + timedelta(seconds=30)
            self.scheduler.add_job(
                self.forwarder.send_pending_messages,
                "interval",
                seconds=send_interval,
                max_instances=1,
                coalesce=True,
                next_run_time=send_start_time
            )

            # 启动调度器
            self.scheduler.start()

            logger.info(f"Telegram Forwarder 已成功启动并激活调度器。")
            logger.info(f" - 抓取任务: 每 {check_interval}s 执行一次 (首次执行: {check_start_time.strftime('%H:%M:%S')})")
            logger.info(f" - 发送任务: 每 {send_interval}s 执行一次 (首次执行: {send_start_time.strftime('%H:%M:%S')})")
            source_channels = self.config.get('source_channels', [])
            channel_names = [c.get('channel_username') for c in source_channels if c.get('channel_username')]
            logger.info(f"正在监控频道: {', '.join(channel_names) if channel_names else '无'}")
        else:
            logger.error("Telegram 客户端未授权，定时任务未启动。请检查 session 文件或 api_id/api_hash。")


    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP | filter.PlatformAdapterType.QQOFFICIAL)
    async def on_qq_message(self, event: AstrMessageEvent):
        """通过接收到的消息动态捕获并确认 QQ 平台 ID"""
        umo = event.unified_msg_origin
        if ":" in umo:
            platform_id = umo.split(":")[0]
            if not self.bot or getattr(self.forwarder.qq_sender, "platform_id", None) != platform_id:
                self.bot = event.bot
                if hasattr(self, "forwarder") and hasattr(self.forwarder, "qq_sender"):
                    self.forwarder.qq_sender.platform_id = platform_id
                    self.forwarder.qq_sender.bot = event.bot
                logger.debug(f"通过消息事件成功捕获/更新 QQ platform_id: {platform_id}")
                
                if hasattr(self.forwarder.qq_sender, "_ensure_node_name"):
                    asyncio.create_task(self.forwarder.qq_sender._ensure_node_name(event.bot))

    async def terminate(self):
        """插件终止时的清理工作"""
        logger.debug("[Main] 正在停止插件...")

        # 0. 停止转发器逻辑
        if hasattr(self, "forwarder"):
            self.forwarder.stop()

        # 1. Stop Scheduler
        if self.scheduler.running:
            logger.debug("[Main] 正在关闭调度器...")
            self.scheduler.shutdown(wait=False)
            logger.debug("[Main] 调度器已关闭。")

        # 2. Client Disconnect Strategy
        if self.client_wrapper and self.client_wrapper.client:
            session_path = os.path.join(self.plugin_data_dir, "user_session")
            logger.debug(f"[Main] 正在安全断开客户端连接: {session_path}")
            try:
                # 稍微增加等待时间至 5 秒，确保 SQLite 事务安全提交
                await asyncio.wait_for(
                    self.client_wrapper.client.disconnect(), timeout=5.0
                )
                logger.debug("[Main] 客户端已安全断开连接。")
            except asyncio.TimeoutError:
                logger.warning("[Main] 安全断开客户端连接超时，强制清理缓存。")
            except Exception as e:
                logger.debug(f"[Main] 断开连接时遇到异常 (通常不影响下次启动): {e}")
            finally:
                # 无论是否成功断开，都清理缓存，确保下次加载时重新初始化
                from .core.client import TelegramClientWrapper
                TelegramClientWrapper.clear_cache(session_path)

        logger.info("Telegram Forwarder 已停止")

# ================= COMMANDS =================

@filter.command_group("tg")
@filter.permission_type(filter.PermissionType.ADMIN)
def tg(self):
    """Telegram Forwarder 插件管理"""
    pass

@tg.command("add")
async def add_channel(self, event: AstrMessageEvent, channel: str):
    async for result in self.command_handler.add_channel(event, channel):
        yield result

@tg.command("rm")
async def remove_channel(self, event: AstrMessageEvent, channel: str):
    async for result in self.command_handler.remove_channel(event, channel):
        yield result

@tg.command("ls")
async def list_channels(self, event: AstrMessageEvent):
    async for result in self.command_handler.list_channels(event):
        yield result

@tg.command("check")
async def force_check(self, event: AstrMessageEvent):
    async for result in self.command_handler.force_check(event):
        yield result

@tg.command("status")
async def status(self, event: AstrMessageEvent):
    async for result in self.command_handler.show_status(event):
        yield result

@tg.command("pause")
async def pause(self, event: AstrMessageEvent):
    async for result in self.command_handler.pause(event):
        yield result

@tg.command("resume")
async def resume(self, event: AstrMessageEvent):
    async for result in self.command_handler.resume(event):
        yield result

@tg.command("queue")
async def queue(self, event: AstrMessageEvent):
    async for result in self.command_handler.show_queue(event):
        yield result

@tg.command("clearqueue")
async def clearqueue(self, event: AstrMessageEvent, target: str = None):
    async for result in self.command_handler.clear_queue(event, target):
        yield result

@tg.command("get")
async def get(self, event: AstrMessageEvent, target: str = ""):
    """查看频道配置"""
    async for result in self.command_handler.get_config(event, target):
        yield result

@tg.command("set")
async def set_config(self, event: AstrMessageEvent, args: str = ""):
    """修改频道配置"""
    full_text = event.message_str.strip()
    prefix_variants = ["/tg set", "tg set"]
    cmd_text = full_text
    for p in prefix_variants:
        if full_text.lower().startswith(p):
            cmd_text = full_text[len(p):].strip()
            break

    parts = cmd_text
    async for result in self.command_handler.set_config(event, parts):
        yield result

@tg.command("help")
async def show_help(self, event: AstrMessageEvent):
    async for result in self.command_handler.show_help(event):
        yield result
