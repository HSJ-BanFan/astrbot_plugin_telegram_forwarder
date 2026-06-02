from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sqlite3
import tempfile
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from astrbot.api import logger

from .client import TelegramClientWrapper

PLUGIN_NAME = "astrbot_plugin_telegram_forwarder"

DEFAULT_WEB_CONFIG = {
    "enabled": True,
    "host": "0.0.0.0",
    "port": 8180,
    "token": "123456",
}


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

        web_config = self.normalize_web_config(plugin.config.get("web_config", {}))
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

        token = str(cfg.get("token") or DEFAULT_WEB_CONFIG["token"]).strip()
        cfg["token"] = token or DEFAULT_WEB_CONFIG["token"]
        return cfg

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
            from werkzeug.serving import make_server
        except Exception as exc:  # pragma: no cover - import guard for plugin load
            raise RuntimeError("Flask 未安装，请安装 requirements.txt 中的 flask") from exc

        root_dir = Path(__file__).resolve().parent.parent
        web_dir = root_dir / "web"
        asset_dir = web_dir / "assets"
        app = Flask(
            __name__,
            static_folder=str(asset_dir),
            static_url_path="/assets",
        )
        self._make_server = make_server

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
            return (
                request.headers.get("X-Admin-Token", "").strip()
                or request.args.get("token", "").strip()
                or (request.get_json(silent=True) or {}).get("token", "")
            )

        def require_auth(handler):
            def wrapped(*args, **kwargs):
                if extract_token() != self.token:
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
            return json_ok({"authorized": extract_token() == self.token})

        @app.get("/api/status")
        @require_auth
        def api_status():
            return run_api(self.get_status())

        @app.get("/api/config")
        @require_auth
        def api_get_config():
            return run_api(self.get_config())

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
                self._http_server = self._make_server(self.host, self.port, self.app)
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
            for suffix in (".session", ".session-journal", ".session-shm", ".session-wal")
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
                if suffix not in (".session", ".session-journal", ".session-shm", ".session-wal"):
                    continue
                try:
                    raw = base64.b64decode(str(encoded), validate=True)
                except Exception as exc:
                    raise WebAdminError(f"登录信息包中的 {suffix} 不是有效 base64。") from exc
                with open(f"{temp_path}{suffix}", "wb") as session_file:
                    session_file.write(raw)

            try:
                conn = sqlite3.connect(f"{temp_path}.session")
                try:
                    conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
                finally:
                    conn.close()
            except Exception as exc:
                raise WebAdminError("导入的 .session 文件不是有效 SQLite session。") from exc

            for path in self._session_files(session_path):
                if os.path.exists(path):
                    os.remove(path)
            for temp_file in self._session_files(temp_path):
                if not os.path.exists(temp_file):
                    continue
                suffix = temp_file.removeprefix(temp_path)
                shutil.copy2(temp_file, f"{session_path}{suffix}")
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
                        logger.debug(f"[WebAdmin] remove temp login session failed {path}: {exc}")

    async def _ensure_login_wrapper_ready(self):
        if self._login_wrapper and self._login_wrapper.client:
            return self._login_wrapper

        temp_dir = self._temp_login_dir()
        os.makedirs(temp_dir, exist_ok=True)
        self._login_wrapper = TelegramClientWrapper(self.plugin.config, temp_dir)
        if not self._login_wrapper.client:
            raise WebAdminError("Telegram 客户端未就绪，请先配置 api_id / api_hash。")
        return self._login_wrapper

    async def _install_login_session(self, phone: str) -> None:
        login_wrapper = self._login_wrapper
        if not login_wrapper or not login_wrapper.client:
            raise WebAdminError("临时登录客户端不存在，请重新发送验证码。")

        temp_session_path = login_wrapper._session_path()
        official_wrapper = self.plugin.client_wrapper
        official_session_path = os.path.join(official_wrapper.plugin_data_dir, "user_session")
        backup_dir = os.path.join(
            official_wrapper.plugin_data_dir,
            f"user_session_backup_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        )

        try:
            if login_wrapper.client.is_connected():
                await login_wrapper.disconnect(timeout=5.0)
        except Exception as exc:
            logger.debug(f"[WebAdmin] disconnect temp login before install failed: {exc}")
        TelegramClientWrapper.clear_cache(temp_session_path)

        copied_official_backup = False
        try:
            for path in self._session_files(official_session_path):
                if os.path.exists(path):
                    os.makedirs(backup_dir, exist_ok=True)
                    shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
                    copied_official_backup = True

            try:
                if official_wrapper.client and official_wrapper.client.is_connected():
                    await official_wrapper.disconnect(timeout=5.0)
            except Exception as exc:
                logger.debug(f"[WebAdmin] disconnect official client before install failed: {exc}")
            TelegramClientWrapper.clear_cache(official_session_path)

            for path in self._session_files(official_session_path):
                if os.path.exists(path):
                    os.remove(path)

            installed = False
            for temp_path in self._session_files(temp_session_path):
                if not os.path.exists(temp_path):
                    continue
                suffix = temp_path.removeprefix(temp_session_path)
                shutil.copy2(temp_path, f"{official_session_path}{suffix}")
                installed = True

            if not installed:
                raise WebAdminError("临时登录会话文件不存在，请重新登录。")
        except Exception:
            if copied_official_backup:
                for path in self._session_files(official_session_path):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                for backup_path in Path(backup_dir).glob("*"):
                    target = os.path.join(official_wrapper.plugin_data_dir, backup_path.name)
                    shutil.copy2(str(backup_path), target)
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
        wrapper.client = None
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
        return {
            "telegram": login_status,
            "web": self.normalize_web_config(self.plugin.config.get("web_config", {})),
            "runtime": {
                "paused": bool(getattr(self.plugin.command_handler, "_paused", False)),
                "scheduler_running": bool(scheduler and scheduler.running),
                "jobs": len(scheduler.get_jobs()) if scheduler else 0,
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

    async def get_config(self) -> dict[str, Any]:
        config = self._to_plain(dict(self.plugin.config))
        config["web_config"] = self.normalize_web_config(config.get("web_config", {}))
        return {"config": config}

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

    async def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming = payload.get("config") if isinstance(payload.get("config"), dict) else payload
        if not isinstance(incoming, dict):
            raise WebAdminError("配置内容必须是 JSON 对象。")

        old_client_keys = (
            self.plugin.config.get("api_id"),
            self.plugin.config.get("api_hash"),
            self.plugin.config.get("proxy"),
        )
        old_web_config = self.normalize_web_config(self.plugin.config.get("web_config", {}))

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
                except (TypeError, ValueError):
                    raise WebAdminError("api_id 必须是数字。")
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

        if "merge_rules" in incoming and isinstance(incoming["merge_rules"], list):
            self.plugin.config["merge_rules"] = incoming["merge_rules"]

        if "web_config" in incoming:
            web_config = self.normalize_web_config(incoming["web_config"])
            self.plugin.config["web_config"] = web_config
            self.token = web_config["token"]

        self.plugin.config.save_config()

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

        new_web_config = self.normalize_web_config(self.plugin.config.get("web_config", {}))
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
        incoming = payload.get("config") if isinstance(payload.get("config"), dict) else payload
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
        bundle = payload.get("session") if isinstance(payload.get("session"), dict) else payload
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

        try:
            if wrapper.client and wrapper.client.is_connected():
                await wrapper.disconnect(timeout=5.0)
        except Exception as exc:
            logger.debug(f"[WebAdmin] disconnect official client before import failed: {exc}")
        TelegramClientWrapper.clear_cache(session_path)

        try:
            for path in self._session_files(session_path):
                if os.path.exists(path):
                    os.makedirs(backup_dir, exist_ok=True)
                    shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
                    copied_backup = True

            if has_string_session:
                for path in self._session_files(session_path):
                    if os.path.exists(path):
                        os.remove(path)
                self._write_string_session(
                    session_path,
                    str(bundle.get("string_session")).strip(),
                )
            else:
                self._write_session_bundle(session_path, bundle["files"])
        except Exception:
            for path in self._session_files(session_path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            if copied_backup:
                for backup_path in Path(backup_dir).glob("*"):
                    shutil.copy2(str(backup_path), os.path.join(wrapper.plugin_data_dir, backup_path.name))
            raise

        phone = str(bundle.get("phone") or "").strip()
        if phone:
            self.plugin.config["phone"] = phone
            self.plugin.config.save_config()

        wrapper.client = None
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
            "message": "登录信息已导入并验证成功。" if authorized else "登录信息已导入，但 Telegram 未授权，请重新登录。",
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
                "phone": self._login_data.get("phone") or self.plugin.config.get("phone", ""),
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
            "phone": self._login_data.get("phone") or self.plugin.config.get("phone", ""),
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
        self.plugin.forwarder._stopping = False
        asyncio.create_task(self.plugin.forwarder.check_updates())
        asyncio.create_task(self.plugin.forwarder.send_pending_messages())
        return {"message": "已触发一次抓取与发送。"}

    async def runtime_pause(self) -> dict[str, Any]:
        self.plugin.command_handler._paused = True
        self.plugin.forwarder._stopping = True
        if self.plugin.scheduler and self.plugin.scheduler.running:
            self.plugin.scheduler.pause()
        return {"message": "已暂停抓取与发送。"}

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
        storage = self.plugin.forwarder.storage
        if target in ("", "all"):
            for channel_data in storage.persistence.get("channels", {}).values():
                channel_data["pending_queue"] = []
            storage.save()
            return {"message": "已清空所有待发送队列。"}

        channel = target.lstrip("@#")
        data = storage.get_channel_data(channel)
        old_len = len(data.get("pending_queue", []))
        data["pending_queue"] = []
        storage.save()
        return {"message": f"已清空 {channel} 的待发送队列（{old_len} 条）。"}
