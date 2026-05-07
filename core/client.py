import asyncio
import os
import shutil
import sqlite3
import sys
from urllib.parse import urlparse, urlunparse

import socks
from telethon import TelegramClient

from astrbot.api import AstrBotConfig, logger


# ========== 全局客户端缓存 ==========
# 避免插件重载时重新连接和授权，大幅提升配置保存速度
# 使用 sys 模块确保缓存跨插件重载持久化
def get_client_cache():
    if not hasattr(sys, "_telegram_forwarder_client_cache"):
        sys._telegram_forwarder_client_cache = {}
    return sys._telegram_forwarder_client_cache


def get_auth_cache():
    """获取授权状态缓存"""
    if not hasattr(sys, "_telegram_forwarder_auth_cache"):
        sys._telegram_forwarder_auth_cache = {}
    return sys._telegram_forwarder_auth_cache


class TelegramClientWrapper:
    """
    Telegram 客户端封装类

    负责创建和管理 Telethon 客户端实例。
    """

    @staticmethod
    def _is_wrong_session_error(exc: Exception) -> bool:
        error_text = f"{exc!r} {exc}".casefold()
        return "wrong session id" in error_text

    @staticmethod
    def _redact_proxy_url(proxy_url: str) -> str:
        parsed = urlparse(proxy_url)
        if not parsed.username and not parsed.password:
            return proxy_url
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunparse(
            (
                parsed.scheme,
                f"***@{host}",
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

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
        self._authorized = False
        self._init_client()

    def _session_path(self) -> str:
        return os.path.join(self.plugin_data_dir, "user_session")

    @staticmethod
    def _ensure_compatible_session_schema(session_path: str) -> None:
        session_file = f"{session_path}.session"
        if not os.path.exists(session_file):
            return

        conn = sqlite3.connect(session_file)
        try:
            columns = conn.execute("PRAGMA table_info(sessions)").fetchall()
            column_names = [column[1] for column in columns]
            if "tmp_auth_key" not in column_names:
                return

            rows = conn.execute(
                "SELECT dc_id, server_address, port, auth_key, takeout_id FROM sessions"
            ).fetchall()
        finally:
            conn.close()

        backup_file = f"{session_file}.bak"
        if not os.path.exists(backup_file):
            shutil.copy2(session_file, backup_file)

        conn = sqlite3.connect(session_file)
        try:
            conn.execute("ALTER TABLE sessions RENAME TO sessions_legacy")
            conn.execute(
                """
                CREATE TABLE sessions (
                    dc_id integer primary key,
                    server_address text,
                    port integer,
                    auth_key blob,
                    takeout_id integer
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO sessions
                (dc_id, server_address, port, auth_key, takeout_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.execute("DROP TABLE sessions_legacy")
            conn.commit()
        except Exception:
            conn.close()
            shutil.copy2(backup_file, session_file)
            raise
        else:
            conn.close()

    async def ensure_connected(self) -> bool:
        if not self.client:
            return False
        if self.client.is_connected():
            return True

        forward_cfg = self.config.get("forward_config", {}) if self.config else {}
        allow_rebuild = forward_cfg.get("wrong_session_rebuild_enabled", True)

        try:
            await self.client.connect()
        except Exception as e:
            if not allow_rebuild or not self._is_wrong_session_error(e):
                raise

            session_path = self._session_path()
            logger.warning(
                f"[Client] 检测到 wrong session ID，准备清理并重建会话: {session_path}"
            )
            await TelegramClientWrapper.disconnect_and_clear_cache(session_path)
            self._init_client()
            if not self.client:
                return False

            try:
                await self.client.connect()
            except Exception as retry_error:
                logger.error(
                    f"[Client] wrong session ID 自愈重连失败: {session_path} | first_error={e!r} | retry_error={retry_error!r}"
                )
                return False

        return self.client.is_connected()

    async def disconnect(self, timeout: float = 5.0) -> None:
        """Safely disconnect the current Telethon client."""
        if not self.client or not self.client.is_connected():
            return
        await asyncio.wait_for(self.client.disconnect(), timeout=timeout)

    async def send_login_code(self, phone: str) -> str:
        """发送登录验证码并返回 phone_code_hash。"""
        if not await self.ensure_connected():
            raise RuntimeError("Telegram 客户端未初始化，请先设置 api_id/api_hash")
        sent = await asyncio.wait_for(
            self.client.send_code_request(phone), timeout=30.0
        )
        return getattr(sent, "phone_code_hash", "")

    async def sign_in_with_code(self, phone: str, code: str, phone_code_hash: str = ""):
        """Use login code to sign in. Returns (ok, False); 2FA is signaled via SessionPasswordNeededError."""
        if not await self.ensure_connected():
            raise RuntimeError("Telegram 客户端未初始化，请先设置 api_id/api_hash")
        if phone_code_hash:
            await self.client.sign_in(
                phone=phone, code=code, phone_code_hash=phone_code_hash
            )
        else:
            await self.client.sign_in(phone=phone, code=code)
        return await self._mark_authorized_if_needed()

    async def sign_in_with_password(self, password: str) -> bool:
        """提交两步验证密码。"""
        if not await self.ensure_connected():
            raise RuntimeError("Telegram 客户端未初始化，请先设置 api_id/api_hash")
        await self.client.sign_in(password=password)
        ok, _ = await self._mark_authorized_if_needed()
        return ok

    async def _mark_authorized_if_needed(self):
        authorized = await self.client.is_user_authorized()
        if authorized:
            self._authorized = True
            auth_cache = get_auth_cache()
            auth_cache[self._session_path()] = True
            # 某些会话（例如 bot 会话）可能无权限调用 get_dialogs，
            # 此时不应影响“已授权”状态判定。
            try:
                await self.client.get_dialogs(limit=None)
            except Exception as e:
                logger.debug(f"[Client] skip get_dialogs after auth: {e}")
            return True, False
        return False, False

    def _init_client(self):
        """
        初始化 Telethon 客户端实例

        执行流程：
        1. 从配置读取 api_id 和 api_hash
        2. 设置会话文件路径
        3. 检查缓存中是否存在可用客户端
        4. 如不存在，解析代理配置并创建新客户端
        5. 将新客户端加入缓存

        代理支持：
            - HTTP 代理：http://host:port
            - SOCKS5 代理：socks5://host:port
            - 带认证的代理：socks5://user:pass@host:port

        Note:
            如果缺少 api_id/api_hash，client 将保持为 None
            使用全局缓存避免插件重载时重新连接，提升配置保存速度
        """
        # ========== 读取必要的 API 凭证 ==========
        api_id = self.config.get("api_id")
        api_hash = self.config.get("api_hash")

        # 只有在配置完整时才创建客户端
        if api_id and api_hash:
            # 会话文件路径：存储登录状态和缓存
            # 使用 .session 扩展名，Telethon 会自动添加
            session_path = self._session_path()
            try:
                self._ensure_compatible_session_schema(session_path)
            except Exception as e:
                logger.warning(
                    f"[Client] 会话 schema 自愈失败，继续尝试启动: {e}", exc_info=True
                )

            # ========== 检查缓存 ==========
            cache = get_client_cache()

            # 尝试从缓存中获取已连接的客户端
            if session_path in cache:
                cached_client = cache[session_path]
                if cached_client and cached_client.is_connected():
                    logger.debug(
                        f"[Client Cache] 复用现有的 Telegram 客户端连接: {session_path}"
                    )
                    self.client = cached_client
                    return
                else:
                    logger.debug(
                        f"[Client Cache] 缓存的客户端已断开，正在重新创建: {session_path}"
                    )
                    del cache[session_path]

            # ========== 代理配置解析 ==========
            proxy_url = self.config.get("proxy", "")
            proxy_setting = None

            if proxy_url:
                try:
                    parsed = urlparse(proxy_url)
                    proxy_type = (
                        socks.HTTP if parsed.scheme.startswith("http") else socks.SOCKS5
                    )
                    proxy_setting = (proxy_type, parsed.hostname, parsed.port)
                    logger.debug(
                        f"[Client] 使用代理: {self._redact_proxy_url(proxy_url)}"
                    )
                except (ValueError, AttributeError) as e:
                    logger.error(f"[Client] 代理 URL 格式错误: {e}")

            # ========== 创建 Telegram 客户端 ==========
            self.client = TelegramClient(
                session_path,
                api_id,
                api_hash,
                proxy=proxy_setting,
                connection_retries=None,
                retry_delay=5,
                auto_reconnect=True,
            )

            # ========== 加入缓存 ==========
            cache[session_path] = self.client
            logger.debug(
                f"[Client Cache] 已创建并缓存新的 Telegram 客户端: {session_path}"
            )

        else:
            logger.warning(
                "Telegram Forwarder: 缺少 api_id 或 api_hash，请在配置中填写。"
            )

    async def start(self):
        """
        启动 Telegram 客户端

        执行流程：
        1. 检查客户端是否已经连接并授权（从缓存复用）
        2. 如果已连接，跳过初始化直接返回
        3. 否则，连接到 Telegram 服务器
        4. 检查授权状态
        5. 如未授权，尝试登录（发送验证码）
        6. 同步对话框列表，确保能解析频道ID

        异常处理：
            - 网络超时：30秒后放弃
            - 未授权：输出错误提示，引导用户手动登录
            - 其他错误：记录日志并返回

        Note:
            在非交互式环境中无法完成验证码输入
            用户需要在交互式终端手动登录一次，生成会话文件
        """
        # 客户端未初始化时直接返回
        if not self.client:
            return

        try:
            # ========== 快速路径：检查是否已连接并授权 ==========
            # 如果客户端是从缓存复用的，且已经连接并授权，直接返回
            if self.client.is_connected():
                session_path = self._session_path()
                auth_cache = get_auth_cache()

                if auth_cache.get(session_path, False):
                    self._authorized = True
                    logger.debug(f"[Client Cache] 复用已授权的客户端: {session_path}")
                    return
                else:
                    authorized = await self.client.is_user_authorized()
                    if authorized:
                        auth_cache[session_path] = True
                        self._authorized = True
                        logger.debug(
                            f"[Client Cache] 复用已授权的客户端: {session_path}"
                        )
                        return

            # ========== 慢速路径：完整初始化 ==========
            if not await self.ensure_connected():
                logger.error("[Client] Telegram 客户端连接失败")
                self._authorized = False
                return

            # ========== 检查授权状态 ==========
            authorized = await self.client.is_user_authorized()
            if not authorized:
                logger.warning(
                    f"[Client] 客户端未授权。会话路径: {os.path.join(self.plugin_data_dir, 'user_session.session')}"
                )

                phone = self.config.get("phone")
                if phone:
                    logger.info(f"[Client] 正在尝试使用手机号 {phone} 登录...")
                    try:
                        await asyncio.wait_for(
                            self.client.send_code_request(phone), timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.error("[Client] 发送验证码请求超时")
                        return

                    logger.error(
                        "[Client] Telegram 客户端需要验证！请在交互式终端运行一次以完成登录。"
                    )
                    return
                else:
                    logger.error("[Client] 未提供手机号，无法登录。")
                    return

            # ========== 授权成功 ==========
            logger.info("[Client] Telegram 客户端授权成功！")
            self._authorized = True

            session_path = self._session_path()
            auth_cache = get_auth_cache()
            auth_cache[session_path] = True

            # ========== 同步对话框 ==========
            logger.debug("[Client] 正在同步对话框...")
            await self.client.get_dialogs(limit=None)
            logger.debug("[Client] 对话框同步完成")

        except Exception as e:
            logger.error(f"[Client] Telegram 客户端错误: {e}")
            self._authorized = False

    def is_connected(self):
        """检查客户端连接状态"""
        return self.client and self.client.is_connected()

    def is_authorized(self):
        """检查客户端是否已授权"""
        return getattr(self, "_authorized", False) and self.is_connected()

    @staticmethod
    def _close_client_session(client) -> None:
        """同步关闭 Telethon client 的 SQLite session，释放文件锁。"""
        try:
            session = getattr(client, "session", None)
            if session and callable(getattr(session, "close", None)):
                session.close()
        except Exception as e:
            logger.debug(
                f"[Client Cache] 关闭 session 时异常（通常不影响下次启动）: {e}"
            )

    @staticmethod
    def clear_cache(session_path=None):
        """清理客户端缓存和授权状态缓存"""
        cache = get_client_cache()
        auth_cache = get_auth_cache()

        if session_path:
            if session_path in cache:
                logger.debug(f"[Client Cache] 清理会话缓存: {session_path}")
                TelegramClientWrapper._close_client_session(cache[session_path])
                del cache[session_path]
            if session_path in auth_cache:
                del auth_cache[session_path]
        else:
            for sp, client in cache.items():
                TelegramClientWrapper._close_client_session(client)
            client_count = len(cache)
            logger.debug(f"[Client Cache] 清理所有缓存 ({client_count} 个会话)")
            cache.clear()
            auth_cache.clear()

    @staticmethod
    async def disconnect_and_clear_cache(
        session_path: str, timeout: float = 5.0
    ) -> None:
        """Disconnect any cached client for a session and then clear caches."""
        cache = get_client_cache()
        cached_client = cache.get(session_path)

        try:
            if cached_client and cached_client.is_connected():
                await asyncio.wait_for(cached_client.disconnect(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[Client Cache] 断开缓存客户端超时: {session_path}")
        except Exception as e:
            logger.debug(f"[Client Cache] 断开缓存客户端失败 {session_path}: {e}")
        finally:
            if cached_client:
                TelegramClientWrapper._close_client_session(cached_client)
            TelegramClientWrapper.clear_cache(session_path)
