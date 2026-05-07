import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class SessionPasswordNeededError(Exception):
    pass


def load_relogin_module(
    client_factory: MagicMock, inputs: list[str] | None = None
):
    root = Path(__file__).resolve().parents[1]
    module_path = root / "relogin.py"
    module_name = "astrbot_plugin_telegram_forwarder.relogin"

    stubbed_modules = {
        "socks": SimpleNamespace(HTTP=1, SOCKS5=2),
        "telethon": SimpleNamespace(TelegramClient=client_factory),
        "telethon.errors": SimpleNamespace(
            SessionPasswordNeededError=SessionPasswordNeededError
        ),
    }

    with patch.dict(sys.modules, stubbed_modules):
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        with patch("builtins.input", side_effect=inputs or ["123456", "hash", ""]):
            spec.loader.exec_module(mod)
        return mod


def test_relogin_exits_when_api_id_is_not_integer():
    with pytest.raises(SystemExit, match="API ID 必须是整数"):
        load_relogin_module(MagicMock(), inputs=["not-int", "hash", ""])


@pytest.mark.asyncio
async def test_relogin_disconnects_when_sign_in_raises():
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=False)
    client.send_code_request = AsyncMock()
    client.sign_in = AsyncMock(side_effect=RuntimeError("login failed"))

    client_factory = MagicMock(return_value=client)
    relogin_module = load_relogin_module(client_factory)
    relogin_module._async_input = AsyncMock(side_effect=["+8613800000000", "12345"])

    await relogin_module.main()

    client_factory.assert_called_once_with(
        relogin_module.SESSION_FILE,
        123456,
        "hash",
        proxy=None,
    )
    client.disconnect.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_relogin_disconnects_when_get_me_raises():
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_me = AsyncMock(side_effect=RuntimeError("session check failed"))

    relogin_module = load_relogin_module(MagicMock(return_value=client))

    with pytest.raises(RuntimeError, match="session check failed"):
        await relogin_module.main()

    client.disconnect.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_relogin_preserves_original_error_when_disconnect_raises():
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_me = AsyncMock(side_effect=RuntimeError("session check failed"))

    relogin_module = load_relogin_module(MagicMock(return_value=client))

    with pytest.raises(RuntimeError, match="session check failed"):
        await relogin_module.main()

    client.disconnect.assert_awaited_once_with()
