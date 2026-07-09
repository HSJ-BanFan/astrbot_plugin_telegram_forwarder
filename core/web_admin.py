from __future__ import annotations

import asyncio
import base64
import hmac
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from astrbot.api import logger

from .client import TelegramClientWrapper
from .qq_group_cache import QQGroupCache
from .tg_channel_cache import TGChannelCache

PLUGIN_NAME = "astrbot_plugin_telegram_forwarder"

DEFAULT_WEB_CONFIG = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 8180,
    "token": "",
}
WEAK_DEFAULT_WEB_TOKENS = {"123456"}
SILENT_OK_WEB_PATHS = {"/api/status"}
SILENT_OK_WEB_PREFIXES = ("/assets/",)
WEB_REQUEST_LABELS = {
    ("GET", "/"): "打开 Web 管理页面",
    ("POST", "/api/auth/check"): "验证 Web Token",
    ("GET", "/api/config"): "读取配置",
    ("POST", "/api/config"): "保存配置",
    ("GET", "/api/qq/groups"): "加载 QQ 群列表",
    ("POST", "/api/qq/groups/refresh"): "刷新 QQ 群列表",
    ("GET", "/api/tg/channels"): "加载 Telegram 频道列表",
    ("POST", "/api/tg/channels/refresh"): "刷新 Telegram 频道列表",
    ("GET", "/api/export/config"): "导出配置",
    ("POST", "/api/import/config"): "导入配置",
    ("GET", "/api/export/session"): "导出 Telegram 登录信息",
    ("POST", "/api/import/session"): "导入 Telegram 登录信息",
    ("GET", "/api/login/status"): "检查 Telegram 登录状态",
    ("POST", "/api/login/start"): "发送 Telegram 登录验证码",
    ("POST", "/api/login/code"): "提交 Telegram 登录验证码",
    ("POST", "/api/login/password"): "提交 Telegram 两步验证密码",
    ("POST", "/api/login/cancel"): "取消 Telegram 登录流程",
    ("POST", "/api/login/reset"): "重置 Telegram 登录流程",
    ("POST", "/api/runtime/check"): "立即抓取并发送",
    ("POST", "/api/runtime/pause"): "暂停抓取与发送",
    ("POST", "/api/runtime/resume"): "恢复抓取与发送",
    ("POST", "/api/runtime/clear-queue"): "清空待发送队列",
}


def _status_code_value(code: Any) -> int | None:
    try:
        return int(str(code).split()[0])
    except (TypeError, ValueError):
        return None


def _web_request_label(method: str, path: str) -> str:
    label = WEB_REQUEST_LABELS.get((method, path))
    if label:
        return label
    if path.startswith("/assets/"):
        asset_name = path.rsplit("/", 1)[-1] or path
        return f"加载静态资源 {asset_name}"
    if path.startswith("/api/"):
        return f"调用 Web API {path}"
    return f"访问 Web 路径 {path}"


def _web_request_log_entry(
    method: str, raw_path: str, code: Any
) -> tuple[str, str] | None:
    path = (raw_path or "-").split("?", 1)[0]
    method = (method or "-").upper()
    status_code = _status_code_value(code)
    is_success = status_code is not None and status_code < 400

    if is_success and (
        path in SILENT_OK_WEB_PATHS
        or any(path.startswith(prefix) for prefix in SILENT_OK_WEB_PREFIXES)
    ):
        return None

    if status_code is None:
        level = "info"
        outcome = f"HTTP {code}"
    elif status_code < 400:
        level = "info"
        outcome = f"成功 {status_code}"
    elif status_code < 500:
        level = "warning"
        outcome = f"失败 {status_code}"
    else:
        level = "error"
        outcome = f"异常 {status_code}"

    return (
        level,
        f"[WebAdmin] {_web_request_label(method, path)}：{outcome}（{method} {path}）",
    )


class WebAdminError(RuntimeError):
    pass


class WebAdminServer:
    def __init__(self, plugin: Any, loop: asyncio.AbstractEventLoop):
        self.plugin = plugin
        self.loop = loop
        self._login_data: dict[str, Any] = {}
        self._login_wrapper: TelegramClientWrapper | None = None
        self._thread: threading.Thread | None = None
        self._http_server = None
        self._runtime_operations: list[dict[str, Any]] = []
        self._runtime_operation_seq = 0
        self.qq_group_cache = QQGroupCache(plugin)
        self.tg_channel_cache = TGChannelCache(plugin)

        raw_web_config = plugin.config.get("web_config", {})
        web_config = self.normalize_web_config(raw_web_config)
        self._persist_web_config_if_changed(raw_web_config, web_config)
        self.enabled = web_config["enabled"]
        self.host = web_config["host"]
        self.port = web_config["port"]
        self.token = web_config["token"]
        self.app = self._create_app()

    @staticmethod
    def normalize_web_config(raw: Any) -> dict[str, Any]:
        cfg = dict(DEFAULT_WEB_CONFIG)
        if isinstance(raw, dict):
            cfg.update(raw)

        cfg["enabled"] = WebAdminServer._to_bool(cfg.get("enabled"), True)
        cfg["host"] = str(cfg.get("host") or DEFAULT_WEB_CONFIG["host"]).strip()
        if not cfg["host"]:
            cfg["host"] = DEFAULT_WEB_CONFIG["host"]

        try:
            port = int(cfg.get("port", DEFAULT_WEB_CONFIG["port"]))
        except (TypeError, ValueError):
            port = DEFAULT_WEB_CONFIG["port"]
        if port < 1 or port > 65535:
            port = DEFAULT_WEB_CONFIG["port"]
        cfg["port"] = port

        token = str(cfg.get("token") or "").strip()
        if not token or token in WEAK_DEFAULT_WEB_TOKENS:
            token = secrets.token_urlsafe(32)
        cfg["token"] = token
        return cfg

    def _persist_web_config_if_changed(
        self, raw: Any, normalized: dict[str, Any]
    ) -> None:
        if isinstance(raw, dict) and raw == normalized:
            return
        try:
            self.plugin.config["web_config"] = dict(normalized)
            self.plugin.config.save_config()
            logger.info(
                "[WebAdmin] 已写入安全的 Web 管理页面配置，Token 可在 web_config.token 查看。"
            )
        except Exception as exc:
            logger.warning(f"[WebAdmin] 写入 Web 管理页面配置失败: {exc}")

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
            "enable",
            "enabled",
            "开启",
            "开",
            "是",
        }

    @staticmethod
    def _to_plain(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): WebAdminServer._to_plain(v) for k, v in value.items()}
        if isinstance(value, list):
            return [WebAdminServer._to_plain(v) for v in value]
        if isinstance(value, tuple):
            return [WebAdminServer._to_plain(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _as_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            source = value
        else:
            source = str(value).replace("\n", ",").split(",")
        return [str(item).strip() for item in source if str(item).strip()]

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        return (phone or "").replace(" ", "").replace("-", "").strip()

    def _create_app(self):
        try:
            from flask import Flask, jsonify, request, send_from_directory
            from werkzeug.serving import WSGIRequestHandler, make_server
        except Exception as exc:  # pragma: no cover - import guard for plugin load
            raise RuntimeError(
                "Flask 未安装，请安装 requirements.txt 中的 flask"
            ) from exc

        root_dir = Path(__file__).resolve().parent.parent
        web_dir = root_dir / "web"
        asset_dir = web_dir / "assets"

        class WebAdminRequestHandler(WSGIRequestHandler):
            def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
                entry = _web_request_log_entry(
                    getattr(self, "command", ""),
                    getattr(self, "path", ""),
                    code,
                )
                if entry is None:
                    return
                level, message = entry
                if level == "error":
                    logger.error(message)
                elif level == "warning":
                    logger.warning(message)
                else:
                    logger.info(message)

        app = Flask(
            __name__,
            static_folder=str(asset_dir),
            static_url_path="/assets",
        )
        self._make_server = make_server
        self._request_handler_cls = WebAdminRequestHandler

        def json_ok(data: Any = None, message: str = ""):
            return jsonify({"ok": True, "message": message, "data": data or {}})

        def json_error(message: str, status_code: int = 400):
            response = jsonify({"ok": False, "message": message, "data": {}})
            response.status_code = status_code
            return response

        def extract_token() -> str:
            auth_header = request.headers.get("Authorization", "").strip()
            if auth_header.lower().startswith("bearer "):
                return auth_header[7:].strip()
            body = request.get_json(silent=True) or {}
            return (
                request.headers.get("X-Admin-Token", "").strip()
                or str(body.get("token", "")).strip()
            )

        def token_matches(candidate: str) -> bool:
            return hmac.compare_digest(
                str(candidate).encode("utf-8"),
                str(self.token).encode("utf-8"),
            )

        def require_auth(handler):
            def wrapped(*args, **kwargs):
                if not token_matches(extract_token()):
                    return json_error("未授权：请提供正确的 Web Token。", 401)
                return handler(*args, **kwargs)

            wrapped.__name__ = handler.__name__
            return wrapped

        def run_api(coro, timeout: float = 45.0):
            try:
                return json_ok(self._run_on_loop(coro, timeout=timeout))
            except WebAdminError as exc:
                return json_error(str(exc), 400)
            except Exception as exc:
                logger.error(f"[WebAdmin] API 调用失败: {exc}", exc_info=True)
                return json_error(str(exc), 500)

        @app.after_request
        def add_headers(response):
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/")
        def index():
            return send_from_directory(str(web_dir), "index.html")

        @app.post("/api/auth/check")
        def auth_check():
            return json_ok({"authorized": token_matches(extract_token())})

        @app.get("/api/status")
        @require_auth
        def api_status():
            return run_api(self.get_status())

        @app.get("/api/config")
        @require_auth
        def api_get_config():
            return run_api(self.get_config())

        @app.get("/api/qq/groups")
        @require_auth
        def api_qq_groups():
            return run_api(self.list_qq_groups())

        @app.post("/api/qq/groups/refresh")
        @require_auth
        def api_qq_groups_refresh():
            return run_api(self.list_qq_groups(force=True))

        @app.get("/api/tg/channels")
        @require_auth
        def api_tg_channels():
            return run_api(self.list_tg_channels())

        @app.post("/api/tg/channels/refresh")
        @require_auth
        def api_tg_channels_refresh():
            return run_api(self.list_tg_channels(force=True))

        @app.post("/api/config")
        @require_auth
        def api_save_config():
            payload = request.get_json(silent=True) or {}
            return run_api(self.save_config(payload), timeout=60.0)

        @app.get("/api/export/config")
        @require_auth
        def api_export_config():
            return run_api(self.export_config())

        @app.post("/api/import/config")
        @require_auth
        def api_import_config():
            payload = request.get_json(silent=True) or {}
            return run_api(self.import_config(payload), timeout=60.0)

        @app.get("/api/export/session")
        @require_auth
        def api_export_session():
            return run_api(self.export_session())

        @app.post("/api/import/session")
        @require_auth
        def api_import_session():
            payload = request.get_json(silent=True) or {}
            return run_api(self.import_session(payload), timeout=60.0)

        @app.get("/api/login/status")
        @require_auth
        def api_login_status():
            return run_api(self.get_login_status())

        @app.post("/api/login/start")
        @require_auth
        def api_login_start():
            payload = request.get_json(silent=True) or {}
            return run_api(self.login_start(payload), timeout=60.0)

        @app.post("/api/login/code")
        @require_auth
        def api_login_code():
            payload = request.get_json(silent=True) or {}
            return run_api(self.login_code(payload), timeout=60.0)

        @app.post("/api/login/password")
        @require_auth
        def api_login_password():
            payload = request.get_json(silent=True) or {}
            return run_api(self.login_password(payload), timeout=60.0)

        @app.post("/api/login/cancel")
        @require_auth
        def api_login_cancel():
            return run_api(self.login_cancel())

        @app.post("/api/login/reset")
        @require_auth
        def api_login_reset():
            return run_api(self.login_reset(), timeout=60.0)

        @app.post("/api/runtime/check")
        @require_auth
        def api_runtime_check():
            return run_api(self.runtime_check())

        @app.post("/api/runtime/pause")
        @require_auth
        def api_runtime_pause():
            return run_api(self.runtime_pause())

        @app.post("/api/runtime/resume")
        @require_auth
        def api_runtime_resume():
            return run_api(self.runtime_resume())

        @app.post("/api/runtime/clear-queue")
        @require_auth
        def api_runtime_clear_queue():
            payload = request.get_json(silent=True) or {}
            return run_api(self.runtime_clear_queue(payload))

        return app

    def _run_on_loop(self, coro, timeout: float = 45.0):
        if self.loop.is_closed():
            raise WebAdminError("AstrBot 主事件循环已关闭。")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout as exc:
            future.cancel()
            raise WebAdminError("操作超时，请稍后重试。") from exc

    def start(self) -> None:
        if not self.enabled:
            logger.info("[WebAdmin] Web 管理页面未启用。")
            return
        if self._thread and self._thread.is_alive():
            return

        def serve():
            try:
                self._http_server = self._make_server(
                    self.host,
                    self.port,
                    self.app,
                    request_handler=self._request_handler_cls,
                )
                logger.info(
                    f"[WebAdmin] Telegram Forwarder Web 已启动: http://{self.host}:{self.port}/"
                )
                self._http_server.serve_forever()
            except Exception as exc:
                logger.error(
                    f"[WebAdmin] Web 管理页面监听失败 {self.host}:{self.port}: {exc}"
                )

        self._thread = threading.Thread(
            target=serve,
            name="telegram-forwarder-web-admin",
            daemon=True,
        )
        self._thread.start()
        logger.info("[WebAdmin] Web 管理页面后台启动中，不阻塞插件加载。")

    def stop(self) -> None:
        if self._http_server:
            try:
                self._http_server.shutdown()
                self._http_server.server_close()
            except Exception as exc:
                logger.debug(f"[WebAdmin] shutdown failed: {exc}")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        self._http_server = None

    async def _ensure_wrapper_ready(self):
        wrapper = self.plugin.client_wrapper
        if not wrapper.client:
            wrapper._init_client()
        if not wrapper.client:
            raise WebAdminError("Telegram 客户端未就绪，请先配置 api_id / api_hash。")
        return wrapper

    @staticmethod
    def _session_files(session_path: str) -> list[str]:
        return [
            f"{session_path}{suffix}"
            for suffix in (
                ".session",
                ".session-journal",
                ".session-shm",
                ".session-wal",
            )
        ]

    def _temp_login_dir(self) -> str:
        return os.path.join(self.plugin.client_wrapper.plugin_data_dir, "web_login_tmp")

    def _read_session_bundle(self, session_path: str) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in self._session_files(session_path):
            if not os.path.exists(path):
                continue
            suffix = path.removeprefix(session_path)
            with open(path, "rb") as session_file:
                files[suffix] = base64.b64encode(session_file.read()).decode("ascii")
        return files

    @staticmethod
    def _session_to_string(client: Any) -> str:
        session = getattr(client, "session", None)
        if not session:
            return ""
        return StringSession.save(session)

    def _write_session_bundle(self, session_path: str, files: dict[str, Any]) -> None:
        if ".session" not in files:
            raise WebAdminError("登录信息包缺少 .session 文件。")

        with tempfile.TemporaryDirectory(prefix="tg_session_import_") as temp_dir:
            temp_path = os.path.join(temp_dir, "user_session")
            for suffix, encoded in files.items():
                if suffix not in (
                    ".session",
                    ".session-journal",
                    ".session-shm",
                    ".session-wal",
                ):
                    continue
                try:
                    raw = base64.b64decode(str(encoded), validate=True)
                except Exception as exc:
                    raise WebAdminError(
                        f"登录信息包中的 {suffix} 不是有效 base64。"
                    ) from exc
                with open(f"{temp_path}{suffix}", "wb") as session_file:
                    session_file.write(raw)

            try:
                conn = sqlite3.connect(f"{temp_path}.session")
                try:
                    conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
                finally:
                    conn.close()
            except Exception as exc:
                raise WebAdminError(
                    "导入的 .session 文件不是有效 SQLite session。"
                ) from exc

            for path in self._session_files(session_path):
                if os.path.exists(path):
                    os.remove(path)
            for temp_file in self._session_files(temp_path):
                if not os.path.exists(temp_file):
                    continue
                suffix = temp_file.removeprefix(temp_path)
                shutil.copyfile(temp_file, f"{session_path}{suffix}")
            TelegramClientWrapper._ensure_compatible_session_schema(session_path)

    @staticmethod
    def _write_string_session(session_path: str, string_session: str) -> None:
        from telethon.sessions.sqlite import SQLiteSession

        memory_session = StringSession(string_session)
        if not memory_session.auth_key:
            raise WebAdminError("登录信息中缺少授权密钥。")

        sqlite_session = SQLiteSession(session_path)
        try:
            sqlite_session.set_dc(
                memory_session.dc_id,
                memory_session.server_address,
                memory_session.port,
            )
            sqlite_session._auth_key = memory_session.auth_key
            sqlite_session._update_session_table()
            sqlite_session.save()
        finally:
            sqlite_session.close()
        TelegramClientWrapper._ensure_compatible_session_schema(session_path)

    async def _discard_login_attempt(self, remove_files: bool = True) -> None:
        wrapper = self._login_wrapper
        session_path = (
            wrapper._session_path()
            if wrapper
            else os.path.join(self._temp_login_dir(), "user_session")
        )
        if wrapper and wrapper.client:
            try:
                if wrapper.client.is_connected():
                    await wrapper.disconnect(timeout=5.0)
            except Exception as exc:
                logger.debug(f"[WebAdmin] disconnect temp login client failed: {exc}")
        TelegramClientWrapper.clear_cache(session_path)
        self._login_wrapper = None
        if remove_files:
            for path in self._session_files(session_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as exc:
                        logger.debug(
                            f"[WebAdmin] remove temp login session failed {path}: {exc}"
                        )

    async def _ensure_login_wrapper_ready(self):
        if self._login_wrapper and self._login_wrapper.client:
            return self._login_wrapper

        temp_dir = self._temp_login_dir()
        os.makedirs(temp_dir, exist_ok=True)
        self._login_wrapper = TelegramClientWrapper(self.plugin.config, Path(temp_dir))
        if not self._login_wrapper.client:
            raise WebAdminError("Telegram 客户端未就绪，请先配置 api_id / api_hash。")
        return self._login_wrapper

    async def _install_login_session(self, phone: str) -> None:
        login_wrapper = self._login_wrapper
        if not login_wrapper or not login_wrapper.client:
            raise WebAdminError("临时登录客户端不存在，请重新发送验证码。")

        temp_session_path = login_wrapper._session_path()
        official_wrapper = self.plugin.client_wrapper
        official_session_path = os.path.join(
            official_wrapper.plugin_data_dir, "user_session"
        )
        backup_dir = os.path.join(
            official_wrapper.plugin_data_dir,
            f"user_session_backup_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        )

        try:
            if login_wrapper.client.is_connected():
                await login_wrapper.disconnect(timeout=5.0)
        except Exception as exc:
            logger.debug(
                f"[WebAdmin] disconnect temp login before install failed: {exc}"
            )
        TelegramClientWrapper.clear_cache(temp_session_path)

        copied_official_backup = False
        backup_completed = False
        deleted_official_files = False
        try:
            for path in self._session_files(official_session_path):
                if os.path.exists(path):
                    os.makedirs(backup_dir, exist_ok=True)
                    shutil.copyfile(
                        path, os.path.join(backup_dir, os.path.basename(path))
                    )
                    copied_official_backup = True
            backup_completed = True

            try:
                if official_wrapper.client and official_wrapper.client.is_connected():
                    await official_wrapper.disconnect(timeout=5.0)
            except Exception as exc:
                logger.debug(
                    f"[WebAdmin] disconnect official client before install failed: {exc}"
                )
            TelegramClientWrapper.clear_cache(official_session_path)

            for path in self._session_files(official_session_path):
                if os.path.exists(path):
                    os.remove(path)
            deleted_official_files = True

            installed = False
            for temp_path in self._session_files(temp_session_path):
                if not os.path.exists(temp_path):
                    continue
                suffix = temp_path.removeprefix(temp_session_path)
                shutil.copyfile(temp_path, f"{official_session_path}{suffix}")
                installed = True

            if not installed:
                raise WebAdminError("临时登录会话文件不存在，请重新登录。")
        except Exception:
            if deleted_official_files and backup_completed and copied_official_backup:
                for path in self._session_files(official_session_path):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                for backup_path in Path(backup_dir).glob("*"):
                    target = os.path.join(
                        official_wrapper.plugin_data_dir, backup_path.name
                    )
                    shutil.copyfile(str(backup_path), target)
            raise

        self.plugin.config["phone"] = phone
        self.plugin.config.save_config()
        official_wrapper.client = None
        official_wrapper._authorized = False
        official_wrapper._init_client()
        await official_wrapper.ensure_connected()
        await official_wrapper._mark_authorized_if_needed()
        await self.plugin.activate_runtime_after_authorized(startup_grace=0)
        await self._discard_login_attempt(remove_files=True)

    async def _rebuild_client(self) -> None:
        wrapper = self.plugin.client_wrapper
        session_path = os.path.join(wrapper.plugin_data_dir, "user_session")
        try:
            if wrapper.client and wrapper.client.is_connected():
                await wrapper.disconnect(timeout=5.0)
        except Exception as exc:
            logger.debug(f"[WebAdmin] disconnect before rebuild failed: {exc}")
        await TelegramClientWrapper.disconnect_and_clear_cache(session_path)
        # cast 避免 pyright 把 client 收窄固定为 None（_init_client 会重建实例）
        wrapper.client = cast(Any, None)
        wrapper._authorized = False
        wrapper._init_client()

    async def get_status(self) -> dict[str, Any]:
        login_status = await self.get_login_status()
        forwarder = self.plugin.forwarder
        all_pending = forwarder.storage.get_all_pending()
        queue_by_channel: dict[str, int] = {}
        for item in all_pending:
            channel = str(item.get("channel", ""))
            queue_by_channel[channel] = queue_by_channel.get(channel, 0) + 1

        scheduler = self.plugin.scheduler
        runtime_tasks = getattr(self, "_runtime_tasks", set())
        send_lock = getattr(forwarder, "_send_dispatch_lock", None)
        global_send_lock = getattr(forwarder, "_global_send_lock", None)
        channel_locks = getattr(forwarder, "_channel_locks", {})
        return {
            "telegram": login_status,
            "web": self.normalize_web_config(self.plugin.config.get("web_config", {})),
            "runtime": {
                "paused": bool(getattr(self.plugin.command_handler, "_paused", False)),
                "scheduler_running": bool(scheduler and scheduler.running),
                "jobs": len(scheduler.get_jobs()) if scheduler else 0,
                "active_web_operations": len(runtime_tasks),
                "capture_busy": any(
                    lock.locked()
                    for lock in getattr(channel_locks, "values", lambda: [])()
                ),
                "send_busy": bool(send_lock and send_lock.locked()),
                "global_send_busy": bool(
                    global_send_lock and global_send_lock.locked()
                ),
                "operations": self._runtime_operation_snapshots(),
            },
            "channels": {
                "count": len(
                    [
                        item
                        for item in self.plugin.config.get("source_channels", [])
                        if isinstance(item, dict) and item.get("channel_username")
                    ]
                ),
            },
            "stats": self._to_plain(getattr(forwarder, "stats", {})),
            "queue": {
                "total": len(all_pending),
                "by_channel": queue_by_channel,
            },
        }

    def _runtime_operation_snapshots(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in operation.items() if not key.startswith("_")}
            for operation in self._runtime_operations
        ]

    def _new_runtime_operation(self, label: str, message: str) -> dict[str, Any]:
        self._runtime_operation_seq += 1
        started = datetime.now()
        operation = {
            "id": self._runtime_operation_seq,
            "label": label,
            "status": "running",
            "message": message,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": "",
            "duration_ms": None,
            "_started_ts": started.timestamp(),
        }
        self._runtime_operations.insert(0, operation)
        self._runtime_operations = self._runtime_operations[:8]
        return operation

    def _pending_queue_count(self) -> int | None:
        storage = getattr(self.plugin.forwarder, "storage", None)
        if storage is None:
            return None
        try:
            return len(storage.get_all_pending() or [])
        except Exception as exc:
            logger.debug(f"[WebAdmin] 获取待发送队列数量失败: {exc}")
            return None

    @staticmethod
    def _format_queue_count(count: int | None) -> str:
        if count is None:
            return ""
        return f"（当前队列 {count} 条）"

    @staticmethod
    def _finish_runtime_operation(
        operation: dict[str, Any], status: str, message: str
    ) -> None:
        finished = datetime.now()
        operation["status"] = status
        operation["message"] = message
        operation["finished_at"] = finished.isoformat(timespec="seconds")
        started_ts = operation.get("_started_ts")
        if isinstance(started_ts, (int, float)):
            operation["duration_ms"] = max(
                0,
                int((finished.timestamp() - started_ts) * 1000),
            )

    async def get_config(self) -> dict[str, Any]:
        config = self._to_plain(dict(self.plugin.config))
        config["web_config"] = self.normalize_web_config(config.get("web_config", {}))
        return {"config": config}

    async def list_qq_groups(self, force: bool = False) -> dict[str, Any]:
        return await self.qq_group_cache.list_groups(
            self._configured_qq_group_ids(),
            force=force,
        )

    async def list_tg_channels(self, force: bool = False) -> dict[str, Any]:
        return await self.tg_channel_cache.list_channels(
            self._configured_tg_channel_refs(),
            force=force,
        )

    def _configured_qq_group_ids(self) -> list[str]:
        group_ids: list[str] = []

        def add_target(raw_target: Any) -> None:
            target = str(raw_target or "").strip()
            if not target:
                return
            group_id = ""
            if target.isdigit():
                group_id = target
            else:
                parts = target.split(":")
                if len(parts) >= 3 and parts[1] == "GroupMessage":
                    group_id = parts[2].strip()
            if group_id and group_id.isdigit() and group_id not in group_ids:
                group_ids.append(group_id)

        for target in self._as_string_list(self.plugin.config.get("target_qq_session")):
            add_target(target)
        for channel in self.plugin.config.get("source_channels", []) or []:
            if not isinstance(channel, dict):
                continue
            for target in self._as_string_list(channel.get("target_qq_sessions")):
                add_target(target)
        return group_ids

    def _configured_tg_channel_refs(self) -> list[str]:
        channel_refs: list[str] = []
        for channel in self.plugin.config.get("source_channels", []) or []:
            if not isinstance(channel, dict):
                continue
            channel_ref = str(channel.get("channel_username") or "").strip()
            if channel_ref and channel_ref not in channel_refs:
                channel_refs.append(channel_ref)
        return channel_refs

    def _normalize_source_channels(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            channel = str(item.get("channel_username", "")).lstrip("@#").strip()
            if not channel:
                continue
            cfg = dict(item)
            cfg["__template_key"] = cfg.get("__template_key") or "default"
            cfg["channel_username"] = channel
            for list_key in (
                "forward_types",
                "filter_keywords",
                "monitor_keywords",
                "target_qq_sessions",
            ):
                cfg[list_key] = self._as_string_list(cfg.get(list_key))
            normalized.append(cfg)
        return normalized

    def _normalize_merge_rules(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise WebAdminError("merge_rules 必须是列表。")

        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise WebAdminError(f"merge_rules[{idx}] 必须是对象。")
            cfg = dict(item)
            cfg["__template_key"] = cfg.get("__template_key") or "default"
            cfg["name"] = str(cfg.get("name", "") or "").strip()
            cfg["channel"] = str(cfg.get("channel", "")).lstrip("@#").strip()
            cfg["rule_class"] = str(cfg.get("rule_class", "") or "").strip()
            params = cfg.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise WebAdminError(f"merge_rules[{idx}].params 必须是对象。")
            cfg["params"] = dict(params)
            normalized.append(cfg)
        return normalized

    async def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming = (
            payload.get("config")
            if isinstance(payload.get("config"), dict)
            else payload
        )
        if not isinstance(incoming, dict):
            raise WebAdminError("配置内容必须是 JSON 对象。")

        old_client_keys = (
            self.plugin.config.get("api_id"),
            self.plugin.config.get("api_hash"),
            self.plugin.config.get("proxy"),
        )
        old_web_config = self.normalize_web_config(
            self.plugin.config.get("web_config", {})
        )

        root_fields = {
            "target_qq_session",
            "target_channel",
            "phone",
            "api_id",
            "api_hash",
            "proxy",
            "debug_enabled_default",
            "telegram_session",
        }

        for key in root_fields:
            if key not in incoming:
                continue
            value = incoming[key]
            if key == "api_id":
                try:
                    value = int(value or 0)
                except (TypeError, ValueError) as exc:
                    raise WebAdminError("api_id 必须是数字。") from exc
            elif key in ("target_qq_session", "telegram_session"):
                value = self._as_string_list(value)
            elif key == "debug_enabled_default":
                value = self._to_bool(value)
            else:
                value = str(value or "").strip()
            self.plugin.config[key] = value

        if "forward_config" in incoming:
            if not isinstance(incoming["forward_config"], dict):
                raise WebAdminError("forward_config 必须是对象。")
            forward_config = dict(incoming["forward_config"])
            for list_key in ("forward_types", "filter_keywords", "monitor_keywords"):
                if list_key in forward_config:
                    forward_config[list_key] = self._as_string_list(
                        forward_config.get(list_key)
                    )
            self.plugin.config["forward_config"] = forward_config

        if "source_channels" in incoming:
            self.plugin.config["source_channels"] = self._normalize_source_channels(
                incoming["source_channels"]
            )

        if "merge_rules" in incoming:
            self.plugin.config["merge_rules"] = self._normalize_merge_rules(
                incoming["merge_rules"]
            )

        if "web_config" in incoming:
            web_config = self.normalize_web_config(incoming["web_config"])
            self.plugin.config["web_config"] = web_config
            self.token = web_config["token"]

        self.plugin.config.save_config()
        if hasattr(self.plugin, "forwarder"):
            self.plugin.forwarder.reload_runtime_config()

        new_client_keys = (
            self.plugin.config.get("api_id"),
            self.plugin.config.get("api_hash"),
            self.plugin.config.get("proxy"),
        )
        reinitialized_client = False
        if new_client_keys != old_client_keys:
            await self._rebuild_client()
            reinitialized_client = True

        try:
            if self.plugin.client_wrapper.is_authorized():
                await self.plugin.activate_runtime_after_authorized(startup_grace=0)
        except Exception as exc:
            logger.debug(f"[WebAdmin] reschedule after config save failed: {exc}")

        new_web_config = self.normalize_web_config(
            self.plugin.config.get("web_config", {})
        )
        restart_required = (
            new_web_config["enabled"] != old_web_config["enabled"]
            or new_web_config["host"] != old_web_config["host"]
            or new_web_config["port"] != old_web_config["port"]
        )
        return {
            "config": (await self.get_config())["config"],
            "reinitialized_client": reinitialized_client,
            "web_restart_required": restart_required,
        }

    async def export_config(self) -> dict[str, Any]:
        return {
            "kind": "telegram_forwarder_config",
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "config": (await self.get_config())["config"],
        }

    async def import_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming = (
            payload.get("config")
            if isinstance(payload.get("config"), dict)
            else payload
        )
        if not isinstance(incoming, dict):
            raise WebAdminError("导入配置必须是 JSON 对象，或包含 config 对象。")
        result = await self.save_config({"config": incoming})
        result["message"] = "配置已导入。"
        return result

    async def export_session(self) -> dict[str, Any]:
        wrapper = self.plugin.client_wrapper
        if not wrapper.client:
            raise WebAdminError("当前没有可导出的 Telegram 登录信息。")

        if not wrapper.client.is_connected():
            await wrapper.ensure_connected()
        string_session = self._session_to_string(wrapper.client)
        if not string_session:
            raise WebAdminError("当前 Telegram 登录信息未就绪，无法导出。")
        return {
            "kind": "telegram_forwarder_session",
            "version": 2,
            "format": "string_session",
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "phone": self.plugin.config.get("phone", ""),
            "string_session": string_session,
        }

    async def import_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = (
            payload.get("session")
            if isinstance(payload.get("session"), dict)
            else payload
        )
        if not isinstance(bundle, dict):
            raise WebAdminError("导入登录信息必须是 JSON 对象。")
        has_string_session = bool(str(bundle.get("string_session") or "").strip())
        has_file_bundle = isinstance(bundle.get("files"), dict)
        if not has_string_session and not has_file_bundle:
            raise WebAdminError("导入登录信息必须包含 string_session 或 files 对象。")

        wrapper = self.plugin.client_wrapper
        session_path = os.path.join(wrapper.plugin_data_dir, "user_session")
        backup_dir = os.path.join(
            wrapper.plugin_data_dir,
            f"user_session_import_backup_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        )
        copied_backup = False
        backup_completed = False
        deleted_session_files = False

        try:
            if wrapper.client and wrapper.client.is_connected():
                await wrapper.disconnect(timeout=5.0)
        except Exception as exc:
            logger.debug(
                f"[WebAdmin] disconnect official client before import failed: {exc}"
            )
        TelegramClientWrapper.clear_cache(session_path)

        try:
            for path in self._session_files(session_path):
                if os.path.exists(path):
                    os.makedirs(backup_dir, exist_ok=True)
                    shutil.copyfile(
                        path, os.path.join(backup_dir, os.path.basename(path))
                    )
                    copied_backup = True
            backup_completed = True

            if has_string_session:
                for path in self._session_files(session_path):
                    if os.path.exists(path):
                        os.remove(path)
                deleted_session_files = True
                self._write_string_session(
                    session_path,
                    str(bundle.get("string_session")).strip(),
                )
            else:
                deleted_session_files = True
                self._write_session_bundle(session_path, bundle["files"])
        except Exception:
            if deleted_session_files and backup_completed:
                for path in self._session_files(session_path):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                if copied_backup:
                    for backup_path in Path(backup_dir).glob("*"):
                        shutil.copyfile(
                            str(backup_path),
                            os.path.join(wrapper.plugin_data_dir, backup_path.name),
                        )
            raise

        phone = str(bundle.get("phone") or "").strip()
        if phone:
            self.plugin.config["phone"] = phone
            self.plugin.config.save_config()

        # cast 避免 pyright 把 client 收窄固定为 None（_init_client 会重建实例）
        wrapper.client = cast(Any, None)
        wrapper._authorized = False
        wrapper._init_client()
        authorized = False
        if wrapper.client and await wrapper.ensure_connected():
            authorized = bool(await wrapper.client.is_user_authorized())
            if authorized:
                await wrapper._mark_authorized_if_needed()
                await self.plugin.activate_runtime_after_authorized(startup_grace=0)

        await self._discard_login_attempt(remove_files=True)
        return {
            "authorized": authorized,
            "message": "登录信息已导入并验证成功。"
            if authorized
            else "登录信息已导入，但 Telegram 未授权，请重新登录。",
        }

    async def get_login_status(self) -> dict[str, Any]:
        wrapper = self.plugin.client_wrapper
        if not wrapper or not wrapper.client:
            return {
                "connected": False,
                "authorized": False,
                "login_in_progress": bool(self._login_data),
                "need_password": bool(self._login_data.get("need_password")),
                "replace_existing": bool(self._login_data.get("replace_existing")),
                "code_sent": bool(self._login_data.get("phone_code_hash")),
                "phone": self._login_data.get("phone")
                or self.plugin.config.get("phone", ""),
                "me": None,
            }

        connected = bool(wrapper.is_connected())
        authorized = bool(wrapper.is_authorized())
        me_data = None
        try:
            if connected:
                authorized = bool(await wrapper.client.is_user_authorized())
                if authorized:
                    wrapper._authorized = True
                    me = await wrapper.client.get_me()
                    me_data = {
                        "id": getattr(me, "id", None),
                        "username": getattr(me, "username", None),
                        "first_name": getattr(me, "first_name", None),
                        "last_name": getattr(me, "last_name", None),
                        "phone": getattr(me, "phone", None),
                    }
        except Exception as exc:
            logger.debug(f"[WebAdmin] login status check failed: {exc}")

        return {
            "connected": connected,
            "authorized": authorized,
            "login_in_progress": bool(self._login_data),
            "need_password": bool(self._login_data.get("need_password")),
            "replace_existing": bool(self._login_data.get("replace_existing")),
            "code_sent": bool(self._login_data.get("phone_code_hash")),
            "phone": self._login_data.get("phone")
            or self.plugin.config.get("phone", ""),
            "created_at": self._login_data.get("created_at"),
            "me": me_data,
        }

    async def login_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        replace_existing = self._to_bool(payload.get("replace_existing"), False)
        current_wrapper = await self._ensure_wrapper_ready()
        phone = self._normalize_phone(
            str(payload.get("phone") or self.plugin.config.get("phone") or "")
        )
        if not phone:
            raise WebAdminError("请填写 Telegram 手机号。")

        try:
            if replace_existing:
                await self._discard_login_attempt(remove_files=True)
                wrapper = await self._ensure_login_wrapper_ready()
            else:
                wrapper = current_wrapper

            await wrapper.ensure_connected()
            if not replace_existing and await wrapper.client.is_user_authorized():
                await wrapper._mark_authorized_if_needed()
                self._login_data.clear()
                await self.plugin.activate_runtime_after_authorized(startup_grace=0)
                return {"authorized": True, "message": "当前 Telegram 账号已授权。"}

            phone_code_hash = await wrapper.send_login_code(phone)
            if not replace_existing:
                self.plugin.config["phone"] = phone
                self.plugin.config.save_config()
            self._login_data = {
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "need_password": False,
                "replace_existing": replace_existing,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            return {
                "authorized": False,
                "code_sent": True,
                "phone": phone,
                "message": "验证码已发送，请输入 Telegram 收到的验证码原文。",
            }
        except FloodWaitError as exc:
            seconds = getattr(exc, "seconds", 0) or 0
            raise WebAdminError(f"请求过于频繁，请等待 {seconds} 秒后重试。") from exc
        except WebAdminError:
            raise
        except Exception as exc:
            raise WebAdminError(f"发送验证码失败：{exc}") from exc

    async def login_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = str(payload.get("code") or "").strip()
        if not code:
            raise WebAdminError("请填写验证码。")
        if not self._login_data:
            raise WebAdminError("当前没有进行中的登录流程，请先发送验证码。")

        wrapper = (
            await self._ensure_login_wrapper_ready()
            if self._login_data.get("replace_existing")
            else await self._ensure_wrapper_ready()
        )
        try:
            ok, _ = await wrapper.sign_in_with_code(
                phone=self._login_data["phone"],
                code=code,
                phone_code_hash=self._login_data.get("phone_code_hash", ""),
            )
            if ok:
                phone = self._login_data.get("phone", "")
                if self._login_data.get("replace_existing"):
                    await self._install_login_session(phone)
                else:
                    self.plugin.config["phone"] = phone
                    self.plugin.config.save_config()
                    await self.plugin.activate_runtime_after_authorized(startup_grace=0)
                self._login_data.clear()
                return {"authorized": True, "message": "登录成功。"}
            return {"authorized": False, "message": "验证码已提交，但账号仍未授权。"}
        except SessionPasswordNeededError:
            self._login_data["need_password"] = True
            return {
                "authorized": False,
                "need_password": True,
                "message": "该账号已开启两步验证，请继续提交密码。",
            }
        except PhoneCodeInvalidError as exc:
            raise WebAdminError("验证码错误。") from exc
        except PhoneCodeExpiredError as exc:
            await self._discard_login_attempt(remove_files=True)
            self._login_data.clear()
            raise WebAdminError("验证码已过期，请重新发送验证码。") from exc
        except FloodWaitError as exc:
            seconds = getattr(exc, "seconds", 0) or 0
            raise WebAdminError(f"请求过于频繁，请等待 {seconds} 秒后重试。") from exc
        except Exception as exc:
            raise WebAdminError(f"提交验证码失败：{exc}") from exc

    async def login_password(self, payload: dict[str, Any]) -> dict[str, Any]:
        password = str(payload.get("password") or "").strip()
        if not password:
            raise WebAdminError("请填写两步验证密码。")
        if not self._login_data:
            raise WebAdminError("当前没有进行中的登录流程，请先发送验证码。")

        wrapper = (
            await self._ensure_login_wrapper_ready()
            if self._login_data.get("replace_existing")
            else await self._ensure_wrapper_ready()
        )
        try:
            ok = await wrapper.sign_in_with_password(password)
            if ok:
                phone = self._login_data.get("phone", "")
                if self._login_data.get("replace_existing"):
                    await self._install_login_session(phone)
                else:
                    self.plugin.config["phone"] = phone
                    self.plugin.config.save_config()
                    await self.plugin.activate_runtime_after_authorized(startup_grace=0)
                self._login_data.clear()
                return {"authorized": True, "message": "两步验证通过，登录完成。"}
            return {"authorized": False, "message": "密码已提交，但账号仍未授权。"}
        except FloodWaitError as exc:
            seconds = getattr(exc, "seconds", 0) or 0
            raise WebAdminError(f"请求过于频繁，请等待 {seconds} 秒后重试。") from exc
        except Exception as exc:
            raise WebAdminError(f"提交两步验证密码失败：{exc}") from exc

    async def login_cancel(self) -> dict[str, Any]:
        await self._discard_login_attempt(remove_files=True)
        self._login_data.clear()
        return {"message": "已取消当前登录流程。"}

    async def login_reset(self) -> dict[str, Any]:
        await self._discard_login_attempt(remove_files=True)
        self._login_data = {
            "replace_existing": True,
            "need_password": False,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        return {"message": "已进入重新登录流程，当前已登录账号会保留到新账号登录成功。"}

    async def runtime_check(self) -> dict[str, Any]:
        if getattr(self.plugin.command_handler, "_paused", False):
            raise WebAdminError("当前已暂停抓取与发送，请先恢复运行。")
        running_operation = next(
            (
                operation
                for operation in self._runtime_operations
                if operation.get("status") == "running"
                and operation.get("label") == "立即抓取发送"
            ),
            None,
        )
        if running_operation is not None:
            return {
                "message": "已有立即抓取发送任务在执行。",
                "operation": {
                    key: value
                    for key, value in running_operation.items()
                    if not key.startswith("_")
                },
            }
        self.plugin.forwarder._stopping = False
        operation = self._new_runtime_operation(
            "立即抓取发送",
            "已排队，准备强制抓取频道更新。",
        )

        async def run_once():
            before_fetch = self._pending_queue_count()
            operation["message"] = (
                f"正在强制抓取频道更新。{self._format_queue_count(before_fetch)}"
            )
            await self.plugin.forwarder.check_updates(force=True)
            after_fetch = self._pending_queue_count()
            operation["message"] = (
                f"正在发送待发送队列。{self._format_queue_count(after_fetch)}"
            )
            await self.plugin.forwarder.send_pending_messages(force_immediate=True)
            after_send = self._pending_queue_count()
            if after_fetch == 0 and after_send == 0:
                operation["_success_message"] = "执行完成：抓取后没有待发送消息。"
            elif after_send == 0 and after_fetch:
                operation["_success_message"] = "执行完成：本轮待发送队列已处理完。"
            elif after_fetch is not None and after_send is not None:
                operation["_success_message"] = (
                    f"执行完成：待发送队列 {after_fetch} -> {after_send} 条。"
                )
            elif after_send is not None:
                operation["_success_message"] = (
                    f"执行完成：当前待发送队列 {after_send} 条。"
                )

        self._track_runtime_task(run_once(), operation=operation)
        return {
            "message": "已开始后台执行：强制抓取后发送。",
            "operation": self._runtime_operation_snapshots()[0],
        }

    def _track_runtime_task(
        self, coro, operation: dict[str, Any] | None = None
    ) -> None:
        task = asyncio.create_task(coro)
        runtime_tasks = getattr(self, "_runtime_tasks", None)
        if runtime_tasks is None:
            runtime_tasks = self._runtime_tasks = set()
        runtime_tasks.add(task)

        def on_done(done_task):
            runtime_tasks.discard(done_task)
            if done_task.cancelled():
                if operation is not None:
                    self._finish_runtime_operation(
                        operation, "cancelled", "任务已取消。"
                    )
                return
            try:
                done_task.result()
            except Exception as exc:
                if operation is not None:
                    self._finish_runtime_operation(
                        operation,
                        "failed",
                        f"执行失败：{exc}",
                    )
                logger.error(f"[WebAdmin] 运行任务失败: {exc}", exc_info=True)
            else:
                if operation is not None:
                    self._finish_runtime_operation(
                        operation,
                        "success",
                        str(operation.get("_success_message") or "执行完成。"),
                    )

        task.add_done_callback(on_done)

    async def runtime_pause(self) -> dict[str, Any]:
        self.plugin.command_handler._paused = True
        cancelled_count = (
            self.plugin.forwarder.request_stop()
            if hasattr(self.plugin.forwarder, "request_stop")
            else 0
        )
        if not hasattr(self.plugin.forwarder, "request_stop"):
            self.plugin.forwarder._stopping = True
        if self.plugin.scheduler and self.plugin.scheduler.running:
            self.plugin.scheduler.pause()
        message = "已暂停抓取与发送。"
        if cancelled_count:
            message += f" 已请求停止 {cancelled_count} 个在途发送任务。"
        return {"message": message}

    async def runtime_resume(self) -> dict[str, Any]:
        self.plugin.command_handler._paused = False
        self.plugin.forwarder._stopping = False
        if self.plugin.client_wrapper.is_authorized():
            await self.plugin.activate_runtime_after_authorized(startup_grace=0)
            if self.plugin.scheduler and self.plugin.scheduler.running:
                self.plugin.scheduler.resume()
            return {"message": "已恢复抓取与发送。"}
        if self.plugin.scheduler:
            if not self.plugin.scheduler.running:
                self.plugin.scheduler.start()
            else:
                self.plugin.scheduler.resume()
        return {"message": "已恢复抓取与发送。"}

    async def runtime_clear_queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = str(payload.get("target") or "all").strip().lower()
        if hasattr(self.plugin.forwarder, "clear_pending_queue"):
            result = await self.plugin.forwarder.clear_pending_queue(target)
            if result.get("target") == "all":
                message = f"已清空所有待发送队列（{result.get('cleared', 0)} 条）。"
            else:
                message = (
                    f"已清空 {result.get('target', target)} 的待发送队列"
                    f"（{result.get('cleared', 0)} 条）。"
                )
            if result.get("cancelled_sends", 0):
                message += f" 已请求取消 {result['cancelled_sends']} 个在途发送任务。"
            if result.get("fast_forwarded", 0):
                message += f" 已同步 {result['fast_forwarded']} 个频道到最新消息。"
            if result.get("fast_forward_failed"):
                message += " 以下频道最新消息同步失败：" + ", ".join(
                    result["fast_forward_failed"]
                )
            return {"message": message, **result}

        storage = self.plugin.forwarder.storage
        if target in ("", "all"):
            old_len = len(storage.get_all_pending())
            for channel_data in storage.persistence.get("channels", {}).values():
                channel_data["pending_queue"] = []
            storage.save()
            return {"message": f"已清空所有待发送队列（{old_len} 条）。"}

        channel = target.lstrip("@#")
        data = storage.get_channel_data(channel)
        old_len = len(data.get("pending_queue", []))
        data["pending_queue"] = []
        storage.save()
        return {"message": f"已清空 {channel} 的待发送队列（{old_len} 条）。"}
