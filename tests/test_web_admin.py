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
