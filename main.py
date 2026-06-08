import asyncio
import filecmp
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter

try:
    from astrbot.core.utils import path_utils as astrbot_path_utils
except ImportError:
    from astrbot.core.utils import astrbot_path as astrbot_path_utils

from .common.storage import Storage
from .core.client import TelegramClientWrapper
from .core.commands import PluginCommands
from .core.forwarder import Forwarder

PLUGIN_NAME = "astrbot_plugin_telegram_forwarder"


def _get_plugin_data_dir() -> Path:
    plugin_data_root = Path(astrbot_path_utils.get_astrbot_plugin_data_path()).resolve()
    plugin_data_dir = (
        plugin_data_root
        if plugin_data_root.name == PLUGIN_NAME
        else plugin_data_root / PLUGIN_NAME
    )
    plugin_data_dir.mkdir(parents=True, exist_ok=True)
    return plugin_data_dir.resolve()


class Main(star.Star):
    """Telegram 转发插件主类。"""

    STARTUP_GRACE_SECONDS = 30

    def _resolve_uploaded_session_path(self, uploaded_session_path: str) -> str | None:
        plugin_dir = self.plugin_data_dir.resolve()
        uploaded_path = Path(uploaded_session_path)
        if uploaded_path.is_absolute():
            return None
        candidate = (plugin_dir / uploaded_path).resolve()
        try:
            candidate.relative_to(plugin_dir)
        except ValueError:
            return None
        if candidate.suffix != ".session" or not candidate.is_file():
            return None
        return str(candidate)

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        """初始化插件并装配运行时组件。"""
        super().__init__(context)
        self.context = context
        self.config = config
        self.bot = None
        self._runtime_bootstrap_task = None

        # 设置插件数据目录。
        self.plugin_data_dir = _get_plugin_data_dir()

        # 初始化核心组件。
        self.storage = Storage(self.plugin_data_dir / "data.json")

        # 按配置同步上传的 Telegram 会话文件。
        session_files = self.config.get("telegram_session", [])
        if session_files and isinstance(session_files, list) and len(session_files) > 0:
            uploaded_session_path = session_files[0]
            full_uploaded_path = self._resolve_uploaded_session_path(
                uploaded_session_path
            )

            if full_uploaded_path:
                target_session_path = self.plugin_data_dir / "user_session.session"

                should_copy = True
                if target_session_path.exists():
                    try:
                        if filecmp.cmp(
                            full_uploaded_path, target_session_path, shallow=False
                        ):
                            should_copy = False
                            logger.debug("[Main] 会话文件未变化，跳过同步。")
                    except Exception as e:
                        logger.warning(f"[Main] 比较会话文件失败: {e}")

                if should_copy:
                    try:
                        shutil.copy2(full_uploaded_path, target_session_path)
                        logger.debug(
                            f"[Main] 已从上传配置同步会话文件: {target_session_path}"
                        )
                        # 客户端缓存键使用不带 .session 后缀的 user_session。
                        session_key_path = self.plugin_data_dir / "user_session"
                        TelegramClientWrapper.clear_cache(str(session_key_path))
                        logger.debug("[Main] 会话文件已更新，已清理客户端缓存。")

                    except Exception as e:
                        logger.error(f"[Main] 同步会话文件失败 (可能被占用): {e}")
            else:
                logger.warning(
                    f"[Main] 配置中的会话文件路径不存在: {full_uploaded_path}"
                )

        # TelegramClientWrapper 负责管理 Telegram 客户端连接状态。
        self.client_wrapper = TelegramClientWrapper(self.config, self.plugin_data_dir)

        # Forwarder 负责消息转发编排。
        self.forwarder = Forwarder(
            self.context,
            self.config,
            self.storage,
            self.client_wrapper,
            self.plugin_data_dir,
        )

        # 初始化调度器。
        self.scheduler = AsyncIOScheduler()

        # 初始化命令处理器。
        self.command_handler = PluginCommands(
            context, config, self.forwarder, self.scheduler
        )

        # 必要配置缺失时输出警告。
        if not self.config.get("api_id") or not self.config.get("api_hash"):
            logger.warning(
                "Telegram Forwarder: 缺少 api_id 或 api_hash，请在配置中填写。"
            )

    async def initialize(self):
        """启动插件运行时。"""
        # 启动 Telegram 客户端并恢复会话状态。
        if self.client_wrapper.client:
            logger.debug("正在尝试连接 Telegram 客户端...")
            await self.client_wrapper.start()

        # 检查客户端是否已连接并完成授权。
        is_authorized = self.client_wrapper.is_authorized()
        logger.info(
            f"Telegram 客户端授权状态: {'已授权' if is_authorized else '未授权'}"
        )

        if is_authorized:
            startup_grace = self.STARTUP_GRACE_SECONDS

            if hasattr(self.forwarder, "qq_sender"):

                async def _bootstrap_runtime_later():
                    await asyncio.sleep(startup_grace)
                    try:
                        await self.forwarder.qq_sender.initialize_runtime()
                    except Exception as e:
                        logger.debug(
                            f"[Main] QQ sender runtime delayed bootstrap failed: {e}"
                        )

                self._runtime_bootstrap_task = asyncio.create_task(
                    _bootstrap_runtime_later()
                )

            # 启动定时任务。
            # 从全局 forward_config 读取间隔配置。
            forward_config = self.config.get("forward_config", {})
            check_interval = forward_config.get("check_interval", 60)
            send_interval = forward_config.get("send_interval", 60)

            # 任务 1：抓取 Telegram 更新。
            check_start_time = datetime.now() + timedelta(seconds=startup_grace)
            self.scheduler.add_job(
                self.forwarder.check_updates,
                "interval",
                seconds=check_interval,
                max_instances=1,
                coalesce=True,
                next_run_time=check_start_time,
            )

            # 任务 2：发送待处理消息。
            send_start_time = datetime.now() + timedelta(seconds=startup_grace + 5)
            self.scheduler.add_job(
                self.forwarder.send_pending_messages,
                "interval",
                seconds=send_interval,
                max_instances=1,
                coalesce=True,
                next_run_time=send_start_time,
            )

            # 启动调度器。
            self.scheduler.start()

            logger.info("Telegram Forwarder 已成功启动并激活调度器。")
            logger.info(
                f" - 抓取任务: 每 {check_interval}s 执行一次 (首次执行: {check_start_time.strftime('%H:%M:%S')})"
            )
            logger.info(
                f" - 发送任务: 每 {send_interval}s 执行一次 (首次执行: {send_start_time.strftime('%H:%M:%S')})"
            )
            source_channels = self.config.get("source_channels", [])
            channel_names = [
                c.get("channel_username")
                for c in source_channels
                if c.get("channel_username")
            ]
            logger.info(
                f"正在监控频道: {', '.join(channel_names) if channel_names else '无'}"
            )
        else:
            logger.error(
                "Telegram 客户端未授权，定时任务未启动。请检查 session 文件或 api_id/api_hash。"
            )

    async def terminate(self):
        """关闭插件时清理资源。"""
        logger.debug("[Main] 正在停止插件...")

        # 取消延迟运行时引导任务。
        if self._runtime_bootstrap_task and not self._runtime_bootstrap_task.done():
            self._runtime_bootstrap_task.cancel()
            try:
                await self._runtime_bootstrap_task
            except asyncio.CancelledError:
                pass

        # 0. 停止转发逻辑。
        if hasattr(self, "forwarder"):
            self.forwarder.stop()

        # 1. 停止调度器。
        if self.scheduler.running:
            logger.debug("[Main] 正在关闭调度器...")
            self.scheduler.pause()
            self.scheduler.shutdown(wait=False)
            logger.debug("[Main] 调度器已关闭。")
        if hasattr(self, "forwarder"):
            try:
                await self.forwarder.shutdown(timeout=10.0)
            except Exception as e:
                logger.warning(f"[Main] 等待 Forwarder 关闭时遇到异常: {e}")

        # 2. 客户端断开策略。
        if self.client_wrapper and self.client_wrapper.client:
            session_path = str(self.plugin_data_dir / "user_session")
            logger.debug(f"[Main] 正在安全断开客户端连接: {session_path}")
            try:
                # 等待足够时间，让基于 SQLite 的 Telethon session 完成刷盘。
                await self.client_wrapper.disconnect(timeout=5.0)
                logger.debug("[Main] 客户端已安全断开连接。")
            except asyncio.TimeoutError:
                logger.warning("[Main] 安全断开客户端连接超时，强制清理缓存。")
            except Exception as e:
                logger.debug(f"[Main] 断开连接时遇到异常 (通常不影响下次启动): {e}")
            finally:
                # 始终清理缓存，确保下次插件加载时重建客户端。
                from .core.client import TelegramClientWrapper

                await TelegramClientWrapper.disconnect_and_clear_cache(session_path)

        logger.info("Telegram Forwarder 已停止")


# ================= 命令 =================


@filter.command_group("tg")
@filter.permission_type(filter.PermissionType.ADMIN)
def tg(self):
    """管理 Telegram Forwarder 插件。"""
    pass


@tg.command("add")
async def add_channel(self, event: AstrMessageEvent, channel: str):
    """添加监控频道。"""
    async for result in self.command_handler.add_channel(event, channel):
        yield result


@tg.command("rm")
async def remove_channel(self, event: AstrMessageEvent, channel: str):
    """移除监控频道。"""
    async for result in self.command_handler.remove_channel(event, channel):
        yield result


@tg.command("ls")
async def list_channels(self, event: AstrMessageEvent):
    """列出监控频道。"""
    async for result in self.command_handler.list_channels(event):
        yield result


@tg.command("check")
async def force_check(self, event: AstrMessageEvent):
    """立即触发一次抓取和发送周期。"""
    async for result in self.command_handler.force_check(event):
        yield result


@tg.command("status")
async def status(self, event: AstrMessageEvent):
    """查看插件运行状态。"""
    async for result in self.command_handler.show_status(event):
        yield result


@tg.command("pause")
async def pause(self, event: AstrMessageEvent):
    """暂停抓取和发送任务。"""
    async for result in self.command_handler.pause(event):
        yield result


@tg.command("resume")
async def resume(self, event: AstrMessageEvent):
    """恢复抓取和发送任务。"""
    async for result in self.command_handler.resume(event):
        yield result


@tg.command("queue")
async def queue(self, event: AstrMessageEvent):
    """查看待发送队列。"""
    async for result in self.command_handler.show_queue(event):
        yield result


@tg.command("clearqueue")
async def clearqueue(self, event: AstrMessageEvent, target: str | None = None):
    """清空待发送队列。"""
    async for result in self.command_handler.clear_queue(event, target):
        yield result


@tg.command("get")
async def get(self, event: AstrMessageEvent, target: str = ""):
    """查看频道配置。"""
    async for result in self.command_handler.get_config(event, target):
        yield result


@tg.command("set")
async def set_config(self, event: AstrMessageEvent, args: str = ""):
    """更新频道配置。"""
    full_text = event.message_str.strip()
    prefix_variants = ["/tg set", "tg set"]
    cmd_text = full_text
    for p in prefix_variants:
        if full_text.lower().startswith(p):
            cmd_text = full_text[len(p) :].strip()
            break

    parts = cmd_text
    async for result in self.command_handler.set_config(event, parts):
        yield result


@tg.command("login")
async def login(self, event: AstrMessageEvent, args: str = ""):
    """通过 bot 执行 Telegram 登录流程。"""
    full_text = event.message_str.strip()
    prefix_variants = ["/tg login", "tg login"]
    cmd_text = full_text
    for p in prefix_variants:
        if full_text.lower().startswith(p):
            cmd_text = full_text[len(p) :].strip()
            break

    async for result in self.command_handler.handle_login(event, cmd_text):
        yield result


@tg.command("debug")
async def debug(self, event: AstrMessageEvent, action: str | None = None):
    """管理 QQ 发送诊断日志。"""
    async for result in self.command_handler.debug(event, action):
        yield result


@tg.command("help")
async def show_help(self, event: AstrMessageEvent):
    """显示插件命令帮助。"""
    async for result in self.command_handler.show_help(event):
        yield result
