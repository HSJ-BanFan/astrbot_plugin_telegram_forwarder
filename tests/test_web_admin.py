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
    web_admin.server._track_runtime_task = captured.append

    result = await web_admin.server.runtime_check()
    await captured[0]

    assert result["message"] == "已触发一次抓取与发送。"
    assert web_admin.plugin.forwarder._stopping is False
    web_admin.plugin.forwarder.check_updates.assert_awaited_once_with(force=True)
    web_admin.plugin.forwarder.send_pending_messages.assert_awaited_once_with(
        force_immediate=True
    )
    assert calls == [("check", True), ("send", True)]
