import asyncio
import inspect
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import socks
import telethon
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
    def _is_session_schema_error(exc: Exception) -> bool:
        error_text = f"{exc!r} {exc}".casefold()
        return "too many values to unpack" in error_text and "expected 5" in error_text

    @staticmethod
    def _is_unsupported_session_schema_error(exc: Exception) -> bool:
        error_text = f"{exc!r} {exc}".casefold()
        return (
            "not enough values to unpack" in error_text and "expected 6" in error_text
        )

    @staticmethod
    def _telethon_version_text() -> str:
        version = getattr(telethon, "__version__", "")
        return version if isinstance(version, str) else ""

    @staticmethod
    def _telethon_version_tuple(version: str) -> tuple[int, int, int] | None:
        match = re.match(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?", version)
        if not match:
            return None
        major, minor, patch = match.groups()
        return int(major), int(minor), int(patch or 0)

    @classmethod
    def _is_unsupported_telethon_version(cls) -> bool:
        version_tuple = cls._telethon_version_tuple(cls._telethon_version_text())
        return version_tuple is not None and version_tuple >= (1, 43, 0)

    @classmethod
    def _log_unsupported_telethon_version(cls) -> None:
        version_text = cls._telethon_version_text() or "unknown"
        logger.error(
            f"[Client] 当前 Telethon 版本 {version_text} 与本插件 session schema 不兼容。"
            " 请在 AstrBot 插件环境中重装依赖，使 telethon 满足 >=1.42.0,<1.43.0；"
            ' 例如: pip install --upgrade --force-reinstall "telethon>=1.42.0,<1.43.0"',
        )

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

    @staticmethod
    def _parse_proxy_url(proxy_url: str):
        parsed = urlparse(proxy_url)
        if not parsed.hostname or parsed.port is None:
            raise ValueError("代理 URL 必须包含主机和端口")

        proxy_type = socks.HTTP if parsed.scheme.startswith("http") else socks.SOCKS5
        username = unquote(parsed.username) if parsed.username is not None else None
        password = unquote(parsed.password) if parsed.password is not None else None
        if bool(username) != bool(password):
            raise ValueError("代理用户名和密码必须同时填写")
        if username and password:
            return (proxy_type, parsed.hostname, parsed.port, True, username, password)
        return (proxy_type, parsed.hostname, parsed.port)

    def __init__(self, config: AstrBotConfig, plugin_data_dir: Path):
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
        return str(self.plugin_data_dir / "user_session")

    @staticmethod
    def _get_current_session_columns() -> list[str]:
        """Return the session table columns expected by the installed Telethon."""
        fallback_columns = [
            "dc_id",
            "server_address",
            "port",
            "auth_key",
            "takeout_id",
        ]
        try:
            from telethon.sessions.sqlite import SQLiteSession

            source = inspect.getsource(SQLiteSession)
            if "tmp_auth_key" not in source:
                return fallback_columns

            columns = [
                "dc_id",
                "server_address",
                "port",
                "auth_key",
                "takeout_id",
                "tmp_auth_key",
            ]
            if TelegramClientWrapper._telethon_reads_takeout_id_as_tmp_key():
                columns.remove("tmp_auth_key")
                takeout_idx = columns.index("takeout_id")
                columns.insert(takeout_idx, "tmp_auth_key")
            return columns
        except Exception as e:
            logger.debug(f"[Client] 检测 Telethon session schema 失败: {e}")
            return fallback_columns

    @staticmethod
    def _telethon_reads_takeout_id_as_tmp_key() -> bool:
        """Detect Telethon versions whose read order treats takeout_id as tmp_auth_key."""
        try:
            from telethon.sessions.sqlite import SQLiteSession

            source = inspect.getsource(SQLiteSession.__init__)
            normalized = "".join(source.replace("\\", "").split())
            return "key,tmp_key,self._takeout_id=tuple_" in normalized
        except Exception as e:
            logger.debug(f"[Client] 检测 Telethon session 读取顺序失败: {e}")
            return False

    @staticmethod
    def _rotate_existing_backup(backup_file: Path) -> None:
        if not backup_file.exists():
            return
        suffix = 1
        while True:
            archived_backup = backup_file.with_name(f"{backup_file.name}.{suffix}")
            if not archived_backup.exists():
                backup_file.replace(archived_backup)
                return
            suffix += 1

    @staticmethod
    def _ensure_compatible_session_schema(session_path: str) -> None:
        session_file = Path(f"{session_path}.session")
        if not session_file.exists():
            return

        desired_columns = TelegramClientWrapper._get_current_session_columns()
        column_defs = {
            "dc_id": "dc_id integer primary key",
            "server_address": "server_address text",
            "port": "port integer",
            "auth_key": "auth_key blob",
            "takeout_id": "takeout_id integer",
            "tmp_auth_key": "tmp_auth_key blob",
        }
        default_values = {
            "dc_id": "0",
            "server_address": "NULL",
            "port": "NULL",
            "auth_key": "x''",
            "takeout_id": "NULL",
            "tmp_auth_key": "NULL",
        }

        conn = sqlite3.connect(session_file)
        try:
            columns = conn.execute("PRAGMA table_info(sessions)").fetchall()
            column_names = [column[1] for column in columns]
            if not column_names or column_names == desired_columns:
                return

            unsupported = [col for col in desired_columns if col not in column_defs]
            if unsupported:
                logger.debug(
                    f"[Client] 未识别的 Telethon session 字段 {unsupported}，跳过 schema 自愈。"
                )
                return

            select_exprs = [
                col if col in column_names else f"{default_values[col]} AS {col}"
                for col in desired_columns
            ]
            rows = conn.execute(
                f"SELECT {', '.join(select_exprs)} FROM sessions"
            ).fetchall()
        finally:
            conn.close()

        if (
            TelegramClientWrapper._telethon_reads_takeout_id_as_tmp_key()
            and "takeout_id" in desired_columns
            and "tmp_auth_key" in desired_columns
            and desired_columns.index("takeout_id")
            < desired_columns.index("tmp_auth_key")
        ):
            takeout_idx = desired_columns.index("takeout_id")
            sanitized_rows = []
            for row in rows:
                row_list = list(row)
                if row_list[takeout_idx] is not None and not isinstance(
                    row_list[takeout_idx], (bytes, bytearray)
                ):
                    row_list[takeout_idx] = None
                sanitized_rows.append(tuple(row_list))
            rows = sanitized_rows

        backup_file = Path(f"{session_file}.bak")
        TelegramClientWrapper._rotate_existing_backup(backup_file)
        shutil.copy2(session_file, backup_file)

        conn = sqlite3.connect(session_file)
        backup_table = "sessions_schema_migration_backup"
        migration_failed = False
        try:
            conn.execute(f"DROP TABLE IF EXISTS {backup_table}")
            conn.execute(f"ALTER TABLE sessions RENAME TO {backup_table}")
            conn.execute(
                "CREATE TABLE sessions ("
                + ", ".join(column_defs[col] for col in desired_columns)
                + ")"
            )
            placeholders = ", ".join("?" for _ in desired_columns)
            column_list = ", ".join(desired_columns)
            conn.executemany(
                f"INSERT INTO sessions ({column_list}) VALUES ({placeholders})",
                rows,
            )
            conn.execute(f"DROP TABLE {backup_table}")
            conn.commit()
        except Exception:
            migration_failed = True
            raise
        finally:
            # 始终关闭连接；迁移失败时在关闭后再回滚备份，
            # 确保回滚时 SQLite 句柄已释放（Windows 下覆盖 .session 尤为依赖此点）。
            conn.close()
            if migration_failed:
                shutil.copy2(backup_file, session_file)

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
        """安全断开当前 Telethon 客户端。"""
        if not self.client or not self.client.is_connected():
            return
        await asyncio.wait_for(self.client.disconnect(), timeout=timeout)  # type: ignore

    async def send_login_code(self, phone: str) -> str:
        """发送登录验证码并返回 phone_code_hash。"""
        if not await self.ensure_connected():
            raise RuntimeError("Telegram 客户端未初始化，请先设置 api_id/api_hash")
        sent = await asyncio.wait_for(
            self.client.send_code_request(phone), timeout=30.0
        )
        return getattr(sent, "phone_code_hash", "")

    async def sign_in_with_code(self, phone: str, code: str, phone_code_hash: str = ""):
        """使用验证码登录。返回 (ok, False)；两步验证由 SessionPasswordNeededError 表示。"""
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
            if self._is_unsupported_telethon_version():
                self._log_unsupported_telethon_version()
                self.client = None
                return

            # 会话文件路径：存储登录状态和缓存
            # 使用 .session 扩展名，Telethon 会自动添加
            session_path = self._session_path()

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
                    proxy_setting = self._parse_proxy_url(proxy_url)
                    logger.debug(
                        f"[Client] 使用代理: {self._redact_proxy_url(proxy_url)}"
                    )
                except (ValueError, AttributeError) as e:
                    logger.error(f"[Client] 代理 URL 格式错误: {e}")

            # ========== 创建 Telegram 客户端 ==========
            client_kwargs = {}
            if proxy_setting is not None:
                client_kwargs["proxy"] = proxy_setting

            try:
                self.client = TelegramClient(
                    session_path,
                    api_id,
                    api_hash,
                    connection_retries=None,  # type: ignore
                    retry_delay=5,
                    auto_reconnect=True,
                    **client_kwargs,
                )
            except Exception as e:
                if self._is_unsupported_session_schema_error(e):
                    self._log_unsupported_telethon_version()
                    self.client = None
                    return
                if not self._is_session_schema_error(e):
                    raise
                logger.warning(
                    f"[Client] 检测到 Telethon 会话 schema 不兼容，尝试备份并修复 {session_path}.session: {e}"
                )
                try:
                    self._ensure_compatible_session_schema(session_path)
                    self.client = TelegramClient(
                        session_path,
                        api_id,
                        api_hash,
                        connection_retries=None,  # type: ignore
                        retry_delay=5,
                        auto_reconnect=True,
                        **client_kwargs,
                    )
                except Exception as schema_error:
                    logger.error(
                        f"[Client] 会话 schema 自愈失败，无法加载 {session_path}.session。"
                        f" 请使用 relogin.py 重新生成 session: {schema_error}"
                    )
                    self.client = None
                    return

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
        5. 如未授权，提示用户使用命令登录
        6. 同步对话框列表，确保能解析频道ID

        异常处理：
            - 网络超时：30秒后放弃
            - 未授权：输出错误提示，引导用户手动登录
            - 其他错误：记录日志并返回

        Note:
            验证码登录必须通过 /tg login start 命令发起。
            该命令会保存 phone_code_hash，避免验证码和登录流程不匹配。
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
                    f"[Client] 客户端未授权。会话路径: {self.plugin_data_dir / 'user_session.session'}"
                )
                logger.error(
                    "[Client] Telegram 客户端未授权，请使用 /tg login start <手机号> 发起登录。"
                )
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
        """断开指定会话的缓存客户端并清理缓存。"""
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
