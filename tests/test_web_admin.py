import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_config = MagicMock()


class DummyStringSession:
    def __init__(self, *args, **kwargs):
        self.auth_key = None

    @staticmethod
    def save(session):
        return ""


class DummyTelegramClientWrapper:
    @staticmethod
    def clear_cache(session_path):
        return None

    @staticmethod
    def _ensure_compatible_session_schema(session_path):
        return None

    @staticmethod
    async def disconnect_and_clear_cache(session_path):
        return None


class FakePlatformMeta:
    def __init__(self, platform_id="aiocqhttp", name="aiocqhttp"):
        self.id = platform_id
        self.name = name


class FakeQQClient:
    def __init__(self, groups):
        self.groups = groups
        self.calls = 0

    async def call_action(self, action):
        self.calls += 1
        assert action == "get_group_list"
        return {"data": self.groups}


class FakeQQPlatform:
    def __init__(self, client, platform_id="aiocqhttp"):
        self.client = client
        self._meta = FakePlatformMeta(platform_id, "aiocqhttp")

    def meta(self):
        return self._meta

    def get_client(self):
        return self.client


class FakeTGClient:
    def __init__(self, dialogs=None, *, connected=True, authorized=True):
        self.dialogs = dialogs or []
        self.connected = connected
        self.authorized = authorized
        self.calls = 0

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    def iter_dialogs(self):
        self.calls += 1

        async def iterate():
            for dialog in self.dialogs:
                yield dialog

        return iterate()


def fake_dialog(
    *,
    title,
    entity_id,
    username="",
    is_channel=True,
    is_user=False,
    megagroup=False,
    broadcast=True,
    participants_count=None,
):
    entity = SimpleNamespace(
        id=entity_id,
        username=username,
        title=title,
        megagroup=megagroup,
        broadcast=broadcast,
        participants_count=participants_count,
    )
    return SimpleNamespace(
        title=title,
        entity=entity,
        is_channel=is_channel,
        is_user=is_user,
    )


def _package(name: str, path: Path | None = None) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = [] if path is None else [str(path)]
    return module


def load_web_admin_module() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "web_admin.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.web_admin"

    stubbed_modules = {
        "telethon": MagicMock(),
        "telethon.errors": SimpleNamespace(
            FloodWaitError=Exception,
            PhoneCodeExpiredError=Exception,
            PhoneCodeInvalidError=Exception,
            SessionPasswordNeededError=Exception,
        ),
        "telethon.sessions": SimpleNamespace(StringSession=DummyStringSession),
        "astrbot": MagicMock(),
        "astrbot.api": SimpleNamespace(logger=MagicMock()),
        "astrbot_plugin_telegram_forwarder": _package(
            "astrbot_plugin_telegram_forwarder", root
        ),
        "astrbot_plugin_telegram_forwarder.core": _package(
            "astrbot_plugin_telegram_forwarder.core", root / "core"
        ),
        "astrbot_plugin_telegram_forwarder.core.client": SimpleNamespace(
            TelegramClientWrapper=DummyTelegramClientWrapper
        ),
    }

    with patch.dict(sys.modules, stubbed_modules):
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder.core"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def web_admin_module():
    return load_web_admin_module()


@pytest.fixture
def web_admin(web_admin_module):
    loop = asyncio.new_event_loop()
    plugin = SimpleNamespace(
        config=FakeConfig(
            {
                "web_config": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": 8180,
                    "token": "secret-token",
                }
            }
        ),
        client_wrapper=SimpleNamespace(is_authorized=MagicMock(return_value=False)),
        command_handler=SimpleNamespace(_paused=False),
        activate_runtime_after_authorized=AsyncMock(),
    )
    server = web_admin_module.WebAdminServer(plugin, loop)
    server._run_on_loop = lambda coro, timeout=45.0: loop.run_until_complete(coro)
    try:
        yield SimpleNamespace(
            server=server,
            plugin=plugin,
            module=web_admin_module,
            loop=loop,
        )
    finally:
        loop.close()


def test_web_request_log_entry_suppresses_successful_static_assets(web_admin_module):
    assert web_admin_module._web_request_log_entry("GET", "/assets/app.js", 200) is None


def test_web_request_log_entry_describes_runtime_actions(web_admin_module):
    level, message = web_admin_module._web_request_log_entry(
        "POST",
        "/api/runtime/resume",
        "200",
    )

    assert level == "info"
    assert "恢复抓取与发送" in message
    assert "成功 200" in message
    assert "POST /api/runtime/resume" in message


def test_web_request_log_entry_keeps_failed_static_assets(web_admin_module):
    level, message = web_admin_module._web_request_log_entry(
        "GET",
        "/assets/missing.css?v=1",
        404,
    )

    assert level == "warning"
    assert "加载静态资源 missing.css" in message
    assert "失败 404" in message
    assert "GET /assets/missing.css" in message


def test_web_request_handler_uses_readable_logger(web_admin):
    handler = object.__new__(web_admin.server._request_handler_cls)
    handler.command = "POST"
    handler.path = "/api/runtime/resume"
    web_admin.module.logger.reset_mock()

    handler.log_request(200, "-")

    web_admin.module.logger.info.assert_called_once()
    assert "恢复抓取与发送" in web_admin.module.logger.info.call_args.args[0]


def test_auth_check_accepts_header_and_body_tokens(web_admin):
    client = web_admin.server.app.test_client()

    x_token = client.post(
        "/api/auth/check",
        headers={"X-Admin-Token": "secret-token"},
    )
    assert x_token.status_code == 200
    assert x_token.get_json()["data"]["authorized"] is True

    bearer = client.post(
        "/api/auth/check",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert bearer.status_code == 200
    assert bearer.get_json()["data"]["authorized"] is True

    body = client.post("/api/auth/check", json={"token": "secret-token"})
    assert body.status_code == 200
    assert body.get_json()["data"]["authorized"] is True


def test_query_token_is_not_accepted(web_admin):
    client = web_admin.server.app.test_client()

    auth_check = client.post("/api/auth/check?token=secret-token")
    assert auth_check.status_code == 200
    assert auth_check.get_json()["data"]["authorized"] is False

    protected = client.get("/api/status?token=secret-token")
    assert protected.status_code == 401


def test_status_uses_cached_telegram_state_without_rpc(web_admin):
    client = SimpleNamespace(
        is_user_authorized=AsyncMock(),
        get_me=AsyncMock(),
    )
    wrapper = SimpleNamespace(
        client=client,
        is_connected=MagicMock(return_value=True),
        is_authorized=MagicMock(return_value=True),
    )
    web_admin.plugin.client_wrapper = wrapper
    web_admin.plugin.forwarder = SimpleNamespace(
        storage=SimpleNamespace(get_all_pending=MagicMock(return_value=[])),
        stats={},
        _send_dispatch_lock=asyncio.Lock(),
        _global_send_lock=asyncio.Lock(),
        _channel_locks={},
    )
    web_admin.plugin.scheduler = SimpleNamespace(
        running=True,
        get_jobs=MagicMock(return_value=[]),
    )
    web_admin.server._telegram_me_cache = {
        "id": 12345,
        "username": "demo_user",
        "first_name": "Demo",
        "last_name": None,
        "phone": "8613800000000",
    }

    result = asyncio.run(web_admin.server.get_status())

    assert result["telegram"]["connected"] is True
    assert result["telegram"]["authorized"] is True
    assert result["telegram"]["me"]["id"] == 12345
    assert result["telegram"]["me"]["username"] == "demo_user"
    assert result["telegram"]["me"].get("cached") is not True
    client.is_user_authorized.assert_not_awaited()
    client.get_me.assert_not_awaited()


def test_refresh_telegram_me_updates_status_cache(web_admin):
    me = SimpleNamespace(
        id=99,
        username="nick",
        first_name="小明",
        last_name="",
        phone="8613111111111",
    )
    client = SimpleNamespace(
        is_connected=MagicMock(return_value=True),
        is_user_authorized=AsyncMock(return_value=True),
        get_me=AsyncMock(return_value=me),
    )
    wrapper = SimpleNamespace(
        client=client,
        is_connected=MagicMock(return_value=True),
        is_authorized=MagicMock(return_value=True),
        _authorized=False,
    )
    web_admin.plugin.client_wrapper = wrapper

    profile = asyncio.run(web_admin.server._refresh_telegram_me())

    assert profile == {
        "id": 99,
        "username": "nick",
        "first_name": "小明",
        "last_name": "",
        "phone": "8613111111111",
    }
    assert web_admin.server._telegram_me_cache == profile
    client.get_me.assert_awaited_once()


def test_normalize_merge_rules_keeps_valid_rules(web_admin):
    rule = {
        "__template_key": "custom",
        "name": " Rule ",
        "channel": "@channel-a",
        "rule_class": "KeywordNextNMerge",
        "params": {"next_count": 2},
    }

    assert web_admin.server._normalize_merge_rules([rule]) == [
        {
            "__template_key": "custom",
            "name": "Rule",
            "channel": "channel-a",
            "rule_class": "KeywordNextNMerge",
            "params": {"next_count": 2},
        }
    ]


def test_normalize_merge_rules_defaults_none_params(web_admin):
    assert web_admin.server._normalize_merge_rules([{"params": None}]) == [
        {
            "__template_key": "default",
            "name": "",
            "channel": "",
            "rule_class": "",
            "params": {},
        }
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"not": "a list"},
        ["bad-rule"],
        [{"params": "bad"}],
        [{"params": ["bad"]}],
    ],
)
def test_normalize_merge_rules_rejects_malformed_values(web_admin, value):
    with pytest.raises(web_admin.module.WebAdminError):
        web_admin.server._normalize_merge_rules(value)


def test_save_config_rejects_malformed_merge_rules(web_admin):
    client = web_admin.server.app.test_client()

    response = client.post(
        "/api/config",
        headers={"X-Admin-Token": "secret-token"},
        json={"merge_rules": ["bad-rule"]},
    )

    assert response.status_code == 400
    web_admin.plugin.config.save_config.assert_not_called()


def test_proxy_config_migrates_legacy_url(web_admin):
    config = web_admin.server.normalize_proxy_config(
        None,
        "socks5://adm%40in:sec%3Aret@proxy.example.com:12311",
    )

    assert config == {
        "protocol": "socks5",
        "host": "proxy.example.com",
        "port": 12311,
        "username": "adm@in",
        "password": "sec:ret",
    }


@pytest.mark.parametrize(
    ("url", "protocol"),
    [
        ("https://proxy.example.com:8443", "http"),
        ("socks5h://proxy.example.com:1080", "socks5"),
        ("socks4a://proxy.example.com:1080", "socks4"),
    ],
)
def test_proxy_config_migrates_legacy_protocol_aliases(web_admin, url, protocol):
    config = web_admin.server.normalize_proxy_config(None, url)

    assert config["protocol"] == protocol


def test_proxy_config_to_url_encodes_credentials(web_admin):
    url = web_admin.server.proxy_config_to_url(
        {
            "protocol": "socks5",
            "host": "proxy.example.com",
            "port": 12311,
            "username": "adm@in",
            "password": "sec:ret",
        }
    )

    assert url == "socks5://adm%40in:sec%3Aret@proxy.example.com:12311"


def test_proxy_config_to_url_brackets_ipv6_host(web_admin):
    url = web_admin.server.proxy_config_to_url(
        {
            "protocol": "socks5",
            "host": "::1",
            "port": 1080,
            "username": "",
            "password": "",
        }
    )

    assert url == "socks5://[::1]:1080"


def test_proxy_config_credentials_preserve_whitespace(web_admin):
    config = web_admin.server.normalize_proxy_config(
        {
            "protocol": "socks5",
            "host": "proxy.example.com",
            "port": 12311,
            "username": " user ",
            "password": " secret ",
        }
    )

    assert config["username"] == " user "
    assert config["password"] == " secret "
    assert (
        web_admin.server.proxy_config_to_url(config)
        == "socks5://%20user%20:%20secret%20@proxy.example.com:12311"
    )


def test_save_config_persists_structured_proxy_and_legacy_url(web_admin):
    web_admin.server._rebuild_client = AsyncMock()
    result = asyncio.run(
        web_admin.server.save_config(
            {
                "config": {
                    "proxy": "socks5://stale:stale@old.example.com:1080",
                    "proxy_config": {
                        "protocol": "socks5",
                        "host": "127.0.0.1",
                        "port": 12311,
                        "username": "admin",
                        "password": "secret",
                    },
                }
            }
        )
    )

    assert web_admin.plugin.config["proxy_config"]["host"] == "127.0.0.1"
    assert web_admin.plugin.config["proxy"] == "socks5://admin:secret@127.0.0.1:12311"
    assert result["config"]["proxy_config"]["port"] == 12311
    web_admin.server._rebuild_client.assert_awaited_once_with()


def test_save_config_legacy_proxy_replaces_existing_structured_proxy(web_admin):
    web_admin.plugin.config.update(
        {
            "proxy": "socks5://old.example.com:1080",
            "proxy_config": {
                "protocol": "socks5",
                "host": "old.example.com",
                "port": 1080,
                "username": "",
                "password": "",
            },
        }
    )
    web_admin.server._rebuild_client = AsyncMock()

    asyncio.run(
        web_admin.server.save_config(
            {"config": {"proxy": "https://new.example.com:8443"}}
        )
    )

    assert web_admin.plugin.config["proxy_config"] == {
        "protocol": "http",
        "host": "new.example.com",
        "port": 8443,
        "username": "",
        "password": "",
    }
    assert web_admin.plugin.config["proxy"] == "http://new.example.com:8443"
    web_admin.server._rebuild_client.assert_awaited_once_with()


def test_save_config_rejects_unpaired_proxy_credentials(web_admin):
    with pytest.raises(
        web_admin.module.WebAdminError, match="用户名和密码必须同时填写"
    ):
        asyncio.run(
            web_admin.server.save_config(
                {
                    "config": {
                        "proxy_config": {
                            "protocol": "socks5",
                            "host": "127.0.0.1",
                            "port": 12311,
                            "username": "admin",
                            "password": "",
                        }
                    }
                }
            )
        )

    web_admin.plugin.config.save_config.assert_not_called()


def test_get_config_masks_ai_filter_key(web_admin):
    web_admin.plugin.config["forward_config"] = {
        "ai_filter_api_key": "sk-real-secret-key",
        "ai_filter_enabled": True,
    }

    result = asyncio.run(web_admin.server.get_config())

    fwd = result["config"]["forward_config"]
    assert fwd["ai_filter_api_key"] == "[REDACTED]"
    assert fwd["ai_filter_enabled"] is True


def test_export_config_masks_ai_filter_key(web_admin):
    web_admin.plugin.config["forward_config"] = {
        "ai_filter_api_key": "sk-export-secret",
    }

    result = asyncio.run(web_admin.server.export_config())

    assert result["config"]["forward_config"]["ai_filter_api_key"] == "[REDACTED]"


def test_save_config_placeholder_preserves_ai_filter_key(web_admin):
    web_admin.plugin.config["forward_config"] = {
        "ai_filter_api_key": "sk-old-secret",
        "ai_filter_enabled": False,
    }

    asyncio.run(
        web_admin.server.save_config(
            {
                "config": {
                    "forward_config": {
                        "ai_filter_api_key": "[REDACTED]",
                        "ai_filter_enabled": True,
                    }
                }
            }
        )
    )

    fwd = web_admin.plugin.config["forward_config"]
    assert fwd["ai_filter_api_key"] == "sk-old-secret"
    assert fwd["ai_filter_enabled"] is True


def test_proxy_test_endpoint_requires_auth(web_admin):
    client = web_admin.server.app.test_client()

    response = client.post(
        "/api/proxy/test",
        json={"mode": "connectivity", "proxy_config": {}},
    )

    assert response.status_code == 401


def test_probe_proxy_connectivity_reports_latency(web_admin):
    fake_socket = MagicMock()
    with (
        patch.object(
            web_admin.module.socket, "create_connection", return_value=fake_socket
        ) as create_connection,
        patch.object(
            web_admin.module.time,
            "perf_counter",
            side_effect=[1.0, 1.0, 1.025],
        ),
    ):
        result = web_admin.server._probe_proxy_sync(
            {
                "protocol": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "",
                "password": "",
            },
            "connectivity",
            8.0,
        )

    assert result == {"success": True, "status": "ok", "latency_ms": 25}
    create_connection.assert_called_once_with(("proxy.example.com", 1080), timeout=8.0)
    fake_socket.close.assert_called_once_with()


def test_probe_proxy_quality_connects_to_telegram_through_authenticated_proxy(
    web_admin,
):
    proxy_socket = MagicMock()
    tls_socket = MagicMock()
    ssl_context = MagicMock()
    ssl_context.wrap_socket.return_value = tls_socket

    with (
        patch.object(web_admin.module.socks, "socksocket", return_value=proxy_socket),
        patch.object(
            web_admin.module.ssl, "create_default_context", return_value=ssl_context
        ),
        patch.object(
            web_admin.module.time,
            "perf_counter",
            side_effect=[1.0, 1.0, 1.0, 1.042],
        ),
    ):
        result = web_admin.server._probe_proxy_sync(
            {
                "protocol": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "admin",
                "password": "secret",
            },
            "quality",
            8.0,
        )

    assert result == {"success": True, "status": "ok", "latency_ms": 42}
    proxy_socket.set_proxy.assert_called_once_with(
        web_admin.module.socks.SOCKS5,
        "proxy.example.com",
        1080,
        rdns=True,
        username="admin",
        password="secret",
    )
    proxy_socket.connect.assert_called_once_with(("api.telegram.org", 443))
    ssl_context.wrap_socket.assert_called_once_with(
        proxy_socket, server_hostname="api.telegram.org"
    )
    tls_socket.close.assert_called_once_with()


def test_probe_http_proxy_quality_sends_connect_with_basic_auth(web_admin):
    proxy_socket = MagicMock()
    proxy_socket.recv.return_value = b"HTTP/1.1 200 Connection established\r\n\r\n"
    tls_socket = MagicMock()
    ssl_context = MagicMock()
    ssl_context.wrap_socket.return_value = tls_socket

    with (
        patch.object(
            web_admin.module.socket, "create_connection", return_value=proxy_socket
        ),
        patch.object(
            web_admin.module.ssl, "create_default_context", return_value=ssl_context
        ),
        patch.object(
            web_admin.module.time,
            "perf_counter",
            # started, create_connection timeout, settimeout before send,
            # loop settimeout, final wrap settimeout, latency
            side_effect=[1.0, 1.0, 1.0, 1.0, 1.0, 1.050],
        ),
    ):
        result = web_admin.server._probe_proxy_sync(
            {
                "protocol": "http",
                "host": "proxy.example.com",
                "port": 8080,
                "username": "admin",
                "password": "secret",
            },
            "quality",
            8.0,
        )

    assert result == {"success": True, "status": "ok", "latency_ms": 50}
    request = proxy_socket.sendall.call_args.args[0]
    assert b"CONNECT api.telegram.org:443 HTTP/1.1" in request
    assert b"Proxy-Authorization: Basic YWRtaW46c2VjcmV0" in request
    assert b"Basic ***" not in request
    ssl_context.wrap_socket.assert_called_once_with(
        proxy_socket, server_hostname="api.telegram.org"
    )
    tls_socket.close.assert_called_once_with()


def test_probe_proxy_timeout_returns_timeout_status(web_admin):
    with patch.object(
        web_admin.module.socket,
        "create_connection",
        side_effect=TimeoutError("timed out"),
    ):
        result = web_admin.server._probe_proxy_sync(
            {
                "protocol": "http",
                "host": "proxy.example.com",
                "port": 8080,
                "username": "",
                "password": "",
            },
            "connectivity",
            8.0,
        )

    assert result == {"success": False, "status": "timeout", "latency_ms": None}


def test_probe_proxy_uses_single_timeout_budget(web_admin):
    proxy_socket = MagicMock()
    proxy_socket.recv.return_value = b"HTTP/1.1 200 Connection established\r\n\r\n"
    with (
        patch.object(
            web_admin.module.socket, "create_connection", return_value=proxy_socket
        ),
        patch.object(
            web_admin.module.time,
            "perf_counter",
            side_effect=[1.0, 1.0, 8.5, 9.1],
        ),
    ):
        result = web_admin.server._probe_proxy_sync(
            {
                "protocol": "http",
                "host": "proxy.example.com",
                "port": 8080,
                "username": "",
                "password": "",
            },
            "quality",
            8.0,
        )

    assert result == {"success": False, "status": "timeout", "latency_ms": None}


def test_proxy_test_rejects_invalid_mode(web_admin):
    with pytest.raises(web_admin.module.WebAdminError, match="测试类型无效"):
        asyncio.run(
            web_admin.server.test_proxy(
                {
                    "mode": "unknown",
                    "proxy_config": {
                        "protocol": "socks5",
                        "host": "proxy.example.com",
                        "port": 1080,
                    },
                }
            )
        )


def test_qq_groups_requires_auth(web_admin):
    client = web_admin.server.app.test_client()

    response = client.get("/api/qq/groups")

    assert response.status_code == 401


def test_qq_groups_returns_live_groups(web_admin):
    qq_client = FakeQQClient(
        [
            {
                "group_id": 12345,
                "group_name": "Main Group",
                "member_count": 12,
                "max_member_count": 500,
            }
        ]
    )
    web_admin.plugin.context = SimpleNamespace(
        platform_manager=SimpleNamespace(
            platform_insts=[FakeQQPlatform(qq_client, "qq-platform")]
        )
    )
    client = web_admin.server.app.test_client()

    response = client.get("/api/qq/groups", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert response.status_code == 200
    assert payload["available"] is True
    assert payload["groups"] == [
        {
            "group_id": "12345",
            "group_name": "Main Group",
            "avatar": "https://p.qlogo.cn/gh/12345/12345/640",
            "member_count": 12,
            "max_member_count": 500,
            "source": "live",
            "platform_id": "qq-platform",
            "session": "qq-platform:GroupMessage:12345",
        }
    ]


def test_qq_groups_prefers_aiocqhttp_adapter_when_available(web_admin, monkeypatch):
    class PreferredQQPlatform(FakeQQPlatform):
        pass

    monkeypatch.setitem(
        web_admin.server.qq_group_cache._iter_qq_platforms.__globals__,
        "AiocqhttpAdapter",
        PreferredQQPlatform,
    )
    duck_client = FakeQQClient([{"group_id": 12345, "group_name": "Duck Typed Group"}])
    preferred_client = FakeQQClient(
        [{"group_id": 12345, "group_name": "Adapter Group"}]
    )
    web_admin.plugin.context = SimpleNamespace(
        platform_manager=SimpleNamespace(
            platform_insts=[
                FakeQQPlatform(duck_client, "duck-platform"),
                PreferredQQPlatform(preferred_client, "adapter-platform"),
            ]
        )
    )
    client = web_admin.server.app.test_client()

    response = client.get("/api/qq/groups", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert response.status_code == 200
    assert payload["groups"][0]["group_name"] == "Adapter Group"
    assert payload["groups"][0]["platform_id"] == "adapter-platform"


def test_qq_groups_refresh_forces_client_call(web_admin):
    qq_client = FakeQQClient([{"group_id": 12345, "group_name": "Main Group"}])
    web_admin.plugin.context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[FakeQQPlatform(qq_client)])
    )
    client = web_admin.server.app.test_client()

    client.get("/api/qq/groups", headers={"X-Admin-Token": "secret-token"})
    client.post("/api/qq/groups/refresh", headers={"X-Admin-Token": "secret-token"})

    assert qq_client.calls == 2


def test_qq_groups_keeps_configured_numeric_fallback(web_admin):
    web_admin.plugin.config["target_qq_session"] = ["12345", "bad"]
    web_admin.plugin.config["source_channels"] = [
        {"channel_username": "src", "target_qq_sessions": ["p:GroupMessage:67890"]}
    ]
    client = web_admin.server.app.test_client()

    response = client.get("/api/qq/groups", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert payload["available"] is False
    assert [group["group_id"] for group in payload["groups"]] == ["12345", "67890"]
    assert {group["source"] for group in payload["groups"]} == {"configured"}


def test_qq_groups_handles_missing_platform(web_admin):
    web_admin.plugin.context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[])
    )
    client = web_admin.server.app.test_client()

    response = client.get("/api/qq/groups", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert response.status_code == 200
    assert payload["available"] is False
    assert payload["groups"] == []


def test_tg_channels_requires_auth(web_admin):
    client = web_admin.server.app.test_client()

    response = client.get("/api/tg/channels")

    assert response.status_code == 401


def test_tg_channels_returns_dialog_channels(web_admin):
    tg_client = FakeTGClient(
        [
            fake_dialog(
                title="Public Channel",
                entity_id=111,
                username="public_channel",
                participants_count=42,
            ),
            fake_dialog(
                title="Private Supergroup",
                entity_id=222,
                username="",
                megagroup=True,
                broadcast=False,
            ),
            fake_dialog(
                title="User Chat",
                entity_id=333,
                is_channel=False,
                is_user=True,
                broadcast=False,
            ),
        ]
    )
    web_admin.plugin.client_wrapper.client = tg_client
    web_admin.plugin.client_wrapper.is_authorized = MagicMock(return_value=True)
    client = web_admin.server.app.test_client()

    response = client.get("/api/tg/channels", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert response.status_code == 200
    assert payload["available"] is True
    assert payload["channels"] == [
        {
            "id": "222",
            "title": "Private Supergroup",
            "username": "",
            "channel_ref": "-100222",
            "kind": "supergroup",
            "source": "live",
            "member_count": None,
        },
        {
            "id": "111",
            "title": "Public Channel",
            "username": "public_channel",
            "channel_ref": "public_channel",
            "kind": "channel",
            "source": "live",
            "member_count": 42,
        },
    ]


def test_tg_channels_refresh_forces_dialog_reload(web_admin):
    tg_client = FakeTGClient([fake_dialog(title="Channel", entity_id=111)])
    web_admin.plugin.client_wrapper.client = tg_client
    web_admin.plugin.client_wrapper.is_authorized = MagicMock(return_value=True)
    client = web_admin.server.app.test_client()

    client.get("/api/tg/channels", headers={"X-Admin-Token": "secret-token"})
    client.post("/api/tg/channels/refresh", headers={"X-Admin-Token": "secret-token"})

    assert tg_client.calls == 2


def test_tg_channels_handles_unauthorized_client(web_admin):
    tg_client = FakeTGClient([fake_dialog(title="Channel", entity_id=111)])
    tg_client.authorized = False
    web_admin.plugin.client_wrapper.client = tg_client
    web_admin.plugin.client_wrapper.is_authorized = MagicMock(return_value=False)
    client = web_admin.server.app.test_client()

    response = client.get("/api/tg/channels", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert response.status_code == 200
    assert payload["available"] is False
    assert payload["channels"] == []


def test_tg_channels_keeps_configured_fallback(web_admin):
    web_admin.plugin.config["source_channels"] = [
        {"channel_username": "@configured_channel"},
        {"channel_username": "-100987654"},
    ]
    client = web_admin.server.app.test_client()

    response = client.get("/api/tg/channels", headers={"X-Admin-Token": "secret-token"})
    payload = response.get_json()["data"]

    assert payload["available"] is False
    assert [channel["channel_ref"] for channel in payload["channels"]] == [
        "-100987654",
        "configured_channel",
    ]
    assert {channel["source"] for channel in payload["channels"]} == {"configured"}


def test_save_config_preserves_manual_qq_sessions(web_admin):
    client = web_admin.server.app.test_client()

    response = client.post(
        "/api/config",
        headers={"X-Admin-Token": "secret-token"},
        json={
            "target_qq_session": [
                "12345",
                "platform:GroupMessage:67890",
                "platform:FriendMessage:42",
            ]
        },
    )

    assert response.status_code == 200
    assert web_admin.plugin.config["target_qq_session"] == [
        "12345",
        "platform:GroupMessage:67890",
        "platform:FriendMessage:42",
    ]


def test_save_config_preserves_manual_tg_channel_refs(web_admin):
    client = web_admin.server.app.test_client()

    response = client.post(
        "/api/config",
        headers={"X-Admin-Token": "secret-token"},
        json={
            "source_channels": [
                {"channel_username": "https://t.me/manual_channel"},
                {"channel_username": "-100987654321"},
            ]
        },
    )

    assert response.status_code == 200
    assert [
        item["channel_username"] for item in web_admin.plugin.config["source_channels"]
    ] == ["https://t.me/manual_channel", "-100987654321"]


def test_channel_empty_targets_means_inherit_default(web_admin):
    result = web_admin.server._normalize_source_channels(
        [{"channel_username": "src", "target_qq_sessions": ""}]
    )

    assert result[0]["target_qq_sessions"] == []


@pytest.mark.asyncio
async def test_runtime_check_forces_fetch_then_sends(web_admin):
    calls = []

    async def check_updates(*, force=False):
        calls.append(("check", force))

    async def send_pending_messages(*, force_immediate=False):
        calls.append(("send", force_immediate))

    captured = []
    web_admin.plugin.forwarder = SimpleNamespace(
        _stopping=True,
        check_updates=AsyncMock(side_effect=check_updates),
        send_pending_messages=AsyncMock(side_effect=send_pending_messages),
    )
    original_track = web_admin.server._track_runtime_task

    def capture_task(coro, operation=None):
        captured.append((coro, operation))

    web_admin.server._track_runtime_task = capture_task

    result = await web_admin.server.runtime_check()
    await captured[0][0]
    web_admin.server._finish_runtime_operation(
        captured[0][1],
        "success",
        "执行完成。",
    )

    web_admin.server._track_runtime_task = original_track
    assert result["message"] == "已开始后台执行：强制抓取后发送。"
    assert result["operation"]["status"] == "running"
    assert web_admin.plugin.forwarder._stopping is False
    web_admin.plugin.forwarder.check_updates.assert_awaited_once_with(force=True)
    web_admin.plugin.forwarder.send_pending_messages.assert_awaited_once_with(
        force_immediate=True
    )
    assert calls == [("check", True), ("send", True)]
    assert web_admin.server._runtime_operation_snapshots()[0]["status"] == "success"


@pytest.mark.asyncio
async def test_runtime_check_rejects_when_paused(web_admin):
    web_admin.plugin.command_handler._paused = True
    web_admin.plugin.forwarder = SimpleNamespace(
        _stopping=True,
        check_updates=AsyncMock(),
        send_pending_messages=AsyncMock(),
    )

    with pytest.raises(web_admin.module.WebAdminError):
        await web_admin.server.runtime_check()

    assert web_admin.plugin.forwarder._stopping is True
    web_admin.plugin.forwarder.check_updates.assert_not_awaited()
    web_admin.plugin.forwarder.send_pending_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_check_reuses_running_operation(web_admin):
    running = web_admin.server._new_runtime_operation(
        "立即抓取发送",
        "正在发送待发送队列。",
    )
    web_admin.plugin.forwarder = SimpleNamespace(
        _stopping=True,
        check_updates=AsyncMock(),
        send_pending_messages=AsyncMock(),
    )
    web_admin.server._track_runtime_task = MagicMock()

    result = await web_admin.server.runtime_check()

    assert result["message"] == "已有立即抓取发送任务在执行。"
    assert result["operation"]["id"] == running["id"]
    assert len(web_admin.server._runtime_operations) == 1
    assert web_admin.plugin.forwarder._stopping is True
    web_admin.plugin.forwarder.check_updates.assert_not_awaited()
    web_admin.plugin.forwarder.send_pending_messages.assert_not_awaited()
    web_admin.server._track_runtime_task.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_pause_requests_active_send_stop(web_admin):
    request_stop = MagicMock(return_value=1)
    web_admin.plugin.forwarder = SimpleNamespace(
        _stopping=False,
        request_stop=request_stop,
    )
    web_admin.plugin.scheduler = SimpleNamespace(
        running=True,
        pause=MagicMock(),
    )

    result = await web_admin.server.runtime_pause()

    assert web_admin.plugin.command_handler._paused is True
    request_stop.assert_called_once_with()
    web_admin.plugin.scheduler.pause.assert_called_once_with()
    assert "已请求停止 1 个在途发送任务" in result["message"]


@pytest.mark.asyncio
async def test_runtime_clear_queue_uses_forwarder_queue_clear(web_admin):
    clear_pending_queue = AsyncMock(
        return_value={
            "target": "all",
            "cleared": 3,
            "cancelled_sends": 1,
            "fast_forwarded": 2,
            "fast_forward_failed": ["failed_channel"],
        }
    )
    web_admin.plugin.forwarder = SimpleNamespace(
        clear_pending_queue=clear_pending_queue,
    )

    result = await web_admin.server.runtime_clear_queue({"target": "all"})

    clear_pending_queue.assert_awaited_once_with("all")
    assert result["cleared"] == 3
    assert result["cancelled_sends"] == 1
    assert result["fast_forwarded"] == 2
    assert result["fast_forward_failed"] == ["failed_channel"]
    assert "已清空所有待发送队列（3 条）" in result["message"]
    assert "已请求取消 1 个在途发送任务" in result["message"]
    assert "已同步 2 个频道到最新消息" in result["message"]
    assert "failed_channel" in result["message"]


def test_static_assets_serving(web_admin):
    client = web_admin.server.app.test_client()

    # 验证主页面
    r = client.get("/")
    assert r.status_code == 200
    assert b"<!doctype html>" in r.data.lower()

    # 验证静态资源
    paths = [
        "/assets/style.css",
        "/assets/css/variables.css",
        "/assets/css/base.css",
        "/assets/css/components.css",
        "/assets/css/section-channels.css",
        "/assets/app.js",
        "/assets/js/context.js",
        "/assets/js/config.js",
        "/assets/js/api.js",
        "/assets/js/store.js",
        "/assets/js/utils.js",
        "/assets/js/ui_config.js",
        "/assets/js/ui_overview.js",
        "/assets/js/ui_login.js",
        "/assets/js/ui_selector.js",
        "/assets/js/ui_channels.js",
        "/assets/js/ui_topology.js",
    ]
    for path in paths:
        assert client.get(path).status_code == 200, (
            f"Static asset {path} failed to resolve"
        )


def test_normalize_proxy_config_rejects_invalid_legacy_url(web_admin):
    with pytest.raises(web_admin.module.WebAdminError, match="代理 URL 格式无效"):
        web_admin.server.normalize_proxy_config(None, "socks5://proxy.example.com:not-a-port")


def test_probe_http_proxy_quality_applies_deadline_on_fragmented_recv(web_admin):
    proxy_socket = MagicMock()
    # drip-fed CONNECT response; each recv must re-apply remaining timeout
    proxy_socket.recv.side_effect = [
        b"HTTP/1.1 200 Connection",
        b" established\r\n\r\n",
    ]
    tls_socket = MagicMock()
    ssl_context = MagicMock()
    ssl_context.wrap_socket.return_value = tls_socket
    with (
        patch.object(web_admin.module.socket, "create_connection", return_value=proxy_socket),
        patch.object(web_admin.module.ssl, "create_default_context", return_value=ssl_context),
        patch.object(
            web_admin.module.time,
            "perf_counter",
            # started, create_connection, settimeout before send,
            # recv1 remaining, recv2 remaining, wrap settimeout, latency
            side_effect=[1.0, 1.0, 1.0, 1.1, 1.2, 1.3, 1.050],
        ),
    ):
        result = web_admin.server._probe_proxy_sync(
            {
                "protocol": "http",
                "host": "proxy.example.com",
                "port": 8080,
                "username": "",
                "password": "",
            },
            "quality",
            8.0,
        )
    assert result["success"] is True
    assert proxy_socket.recv.call_count == 2
    # settimeout called before each recv (plus earlier pre-send / wrap)
    assert proxy_socket.settimeout.call_count >= 3
