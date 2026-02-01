"""
Telegram 客户端封装模块

提供 Telegram 客户端的初始化、连接、认证等功能。
支持代理配置、会话管理、自动重连等特性。

使用 Telethon 库：https://docs.telethon.dev/
"""

from telethon import TelegramClient
import socks
from astrbot.api import logger, AstrBotConfig
import asyncio
import os
from urllib.parse import urlparse

class TelegramClientWrapper:
    """
    Telegram 客户端封装类

    负责创建和管理 Telethon 客户端实例。
    """
    def __init__(self, config: AstrBotConfig, plugin_data_dir: str):
        """
        初始化客户端封装

        Args:
            config: AstrBot 配置对象，包含 api_id、api_hash、代理等
            plugin_data_dir: 插件数据目录，用于存储会话文件
        """
        self.config = config
        self.plugin_data_dir = plugin_data_dir
        self.client = None
        self._init_client()

    def _init_client(self):
        """
        初始化 Telethon 客户端实例

        执行流程：
        1. 从配置读取 api_id 和 api_hash
        2. 设置会话文件路径
        3. 解析代理配置（如果提供）
        4. 创建 TelegramClient 实例

        代理支持：
            - HTTP 代理：http://host:port
            - SOCKS5 代理：socks5://host:port
            - 带认证的代理：socks5://user:pass@host:port

        Note:
            如果缺少 api_id/api_hash，client 将保持为 None
        """
        # ========== 读取必要的 API 凭证 ==========
        api_id = self.config.get("api_id")
        api_hash = self.config.get("api_hash")

        # 只有在配置完整时才创建客户端
        if api_id and api_hash:
            # 会话文件路径：存储登录状态和缓存
            # 使用 .session 扩展名，Telethon 会自动添加
            session_path = os.path.join(self.plugin_data_dir, "user_session")

            # ========== 代理配置解析 ==========
            proxy_url = self.config.get("proxy", "")
            proxy_setting = None

            if proxy_url:
                try:
                    # 使用 urlparse 进行健壮的 URL 解析
                    parsed = urlparse(proxy_url)

                    # 根据协议确定代理类型
                    proxy_type = socks.HTTP if parsed.scheme.startswith('http') else socks.SOCKS5

                    # 构建 Telethon 代理元组：(类型, 主机, 端口)
                    proxy_setting = (proxy_type, parsed.hostname, parsed.port)

                    logger.info(f"Using proxy: {proxy_setting}")
                except (ValueError, AttributeError) as e:
                    # 捕获解析错误，避免程序崩溃
                    logger.error(f"Invalid proxy URL: {e}")

            # ========== 创建 Telegram 客户端 ==========
            # connection_retries=None 表示无限重连
            self.client = TelegramClient(
                session_path, 
                api_id, 
                api_hash, 
                proxy=proxy_setting,
                connection_retries=None,
                retry_delay=5,
                auto_reconnect=True,
                # Optimization: Hardcode DC IPs to avoid DNS issues and potentially improve speed
                # DC 1: Miami, DC 2: Amsterdam, DC 3: Miami, DC 4: Amsterdam, DC 5: Singapore
                # system_version="4.16.30-vxCustom"
            )
            # self.client.session.set_dc(2, '149.154.167.50', 443)

        else:
            # 配置不完整时输出警告
            logger.warning("Telegram Forwarder: api_id/api_hash missing. Please configure them.")

    async def start(self):
        """
        启动 Telegram 客户端

        执行流程：
        1. 连接到 Telegram 服务器
        2. 检查授权状态
        3. 如未授权，尝试登录（发送验证码）
        4. 同步对话框列表，确保能解析频道ID

        异常处理：
            - 网络超时：30秒后放弃
            - 未授权：输出错误提示，引导用户手动登录
            - 其他错误：记录日志并返回

        Note:
            在非交互式环境中无法完成验证码输入
            用户需要在交互式终端手动登录一次，生成会话文件
        """
        # 客户端未初始化时直接返回
        if not self.client: return

        try:
            # ========== 连接服务器 ==========
            await self.client.connect()

            # ========== 检查授权状态 ==========
            authorized = await self.client.is_user_authorized()
            if not authorized:
                logger.warning(f"Telegram Forwarder: Client NOT authorized. Session path: {os.path.join(self.plugin_data_dir, 'user_session.session')}")
                
                # 检查 session 文件是否存在且大小不为 0
                s_path = os.path.join(self.plugin_data_dir, 'user_session.session')
                if os.path.exists(s_path):
                    logger.info(f"Session file exists, size: {os.path.getsize(s_path)} bytes")
                else:
                    logger.error(f"Session file NOT FOUND at {s_path}")

                # 尝试使用电话号码登录
                phone = self.config.get("phone")
                if phone:
                    logger.info(f"Attempting to login with phone {phone}...")

                    try:
                        # 发送验证码请求，设置30秒超时
                        await asyncio.wait_for(
                            self.client.send_code_request(phone),
                            timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.error("Send code request timed out")
                        return

                    try:
                        # 非阻塞式错误提示
                        # 在插件加载环境中无法交互式输入验证码
                        logger.error(f"Telegram Client needs authentication! Please authenticate via CLI or providing session file.")
                        logger.error(f"Cannot prompt for code in this environment. Please run the script in interactive mode to login once.")
                        return
                    except Exception as e:
                         logger.error(f"Login failed: {e}")
                         return
                else:
                    # 没有提供电话号码
                    logger.error("No phone number provided in config. Cannot login.")
                    return

            # ========== 授权成功 ==========
            logger.info("Telegram Forwarder: Client authorized successfully!")
            self._authorized = True

            # ========== 同步对话框 ==========
            # 获取所有对话框，确保能正确解析频道ID和用户名
            logger.info("Syncing dialogs...")
            await self.client.get_dialogs(limit=None)

        except Exception as e:
            # 捕获所有异常并记录日志
            logger.error(f"Telegram Client Error: {e}")
            self._authorized = False

    def is_connected(self):
        """
        检查客户端连接状态

        Returns:
            bool: 如果客户端存在且已连接返回 True，否则返回 False
        """
        return self.client and self.client.is_connected()

    def is_authorized(self):
        """
        检查客户端是否已授权
        """
        return getattr(self, '_authorized', False) and self.is_connected()
