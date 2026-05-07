"""Tests for PluginCommands debug command."""

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FloodWaitError(Exception):
    pass


class PhoneCodeExpiredError(Exception):
    pass


class PhoneCodeInvalidError(Exception):
    pass


class SessionPasswordNeededError(Exception):
    pass


class FakeConfig(dict):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.save_config = MagicMock()


class FakeQQSender:
    def __init__(self, config_default: bool = False) -> None:
        self._debug_override: dict[str, bool] = {}
        self.config = {"debug_enabled_default": config_default}

    def _debug_enabled(self) -> bool:
        if self._debug_override:
            return any(self._debug_override.values())
        return bool(self.config.get("debug_enabled_default", False))


@lru_cache(maxsize=1)
def load_commands_module() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "commands.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.commands"

    text_tools = SimpleNamespace(
        is_numeric_channel_id=lambda s: str(s).lstrip("-").isdigit(),
        normalize_telegram_channel_name=lambda s: s,
    )
    stubbed_modules = {
        "telethon": MagicMock(),
        "telethon.errors": SimpleNamespace(
            FloodWaitError=FloodWaitError,
            PhoneCodeExpiredError=PhoneCodeExpiredError,
            PhoneCodeInvalidError=PhoneCodeInvalidError,
            SessionPasswordNeededError=SessionPasswordNeededError,
        ),
        "astrbot": MagicMock(),
        "astrbot.api": SimpleNamespace(AstrBotConfig=dict, logger=MagicMock()),
        "astrbot.api.event": SimpleNamespace(AstrMessageEvent=object),
        "astrbot.api.star": SimpleNamespace(Context=object),
        "astrbot_plugin_telegram_forwarder": MagicMock(__path__=[]),
        "astrbot_plugin_telegram_forwarder.core": MagicMock(__path__=[]),
        "astrbot_plugin_telegram_forwarder.common": MagicMock(__path__=[]),
        "astrbot_plugin_telegram_forwarder.common.text_tools": text_tools,
    }

    with patch.dict(sys.modules, stubbed_modules):
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder.core"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod


async def collect_results(
    commands: Any, event: MagicMock, action: str | None = None
) -> list[str]:
    results = []
    async for result in commands.debug(event, action):
        results.append(result)
    return results


def make_event() -> MagicMock:
    event = MagicMock()
    event.plain_result.side_effect = lambda message: message
    return event


def make_commands(
    *, config_default: bool = False, qq_sender: FakeQQSender | None = None
) -> Any:
    commands_module = load_commands_module()
    config = FakeConfig({"debug_enabled_default": config_default})
    forwarder = SimpleNamespace(qq_sender=qq_sender)
    if qq_sender is not None:
        qq_sender.config = config
    return commands_module.PluginCommands(MagicMock(), config, forwarder)


class TestPluginCommandsDebug:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", [None, "", "  "])
    async def test_debug_without_action_returns_usage(self, action: str | None) -> None:
        commands = make_commands(qq_sender=FakeQQSender())
        event = make_event()

        results = await collect_results(commands, event, action)

        assert len(results) == 1
        assert "/tg debug <on|off|status>" in results[0]

    @pytest.mark.asyncio
    async def test_debug_returns_uninitialized_message_when_sender_missing(self) -> None:
        commands = make_commands(qq_sender=None)
        event = make_event()

        results = await collect_results(commands, event, "status")

        assert results == ["QQ 发送器未初始化。"]

    @pytest.mark.asyncio
    async def test_debug_on_sets_runtime_override(self) -> None:
        qq_sender = FakeQQSender()
        commands = make_commands(qq_sender=qq_sender)
        event = make_event()

        results = await collect_results(commands, event, "on")

        assert qq_sender._debug_override == {"__global": True}
        assert "已开启 QQ 发送诊断日志" in results[0]

    @pytest.mark.asyncio
    async def test_debug_normalizes_action_case_and_whitespace(self) -> None:
        qq_sender = FakeQQSender()
        commands = make_commands(qq_sender=qq_sender)
        event = make_event()

        results = await collect_results(commands, event, " ON ")

        assert qq_sender._debug_override == {"__global": True}
        assert "已开启 QQ 发送诊断日志" in results[0]

    @pytest.mark.asyncio
    async def test_debug_off_clears_override_when_config_default_off(self) -> None:
        qq_sender = FakeQQSender()
        qq_sender._debug_override["__global"] = True
        commands = make_commands(config_default=False, qq_sender=qq_sender)
        event = make_event()

        results = await collect_results(commands, event, "off")

        assert qq_sender._debug_override == {}
        assert "回退到配置页默认：关闭" in results[0]

    @pytest.mark.asyncio
    async def test_debug_off_clears_override_when_config_default_on(self) -> None:
        qq_sender = FakeQQSender(config_default=True)
        qq_sender._debug_override["__global"] = True
        commands = make_commands(config_default=True, qq_sender=qq_sender)
        event = make_event()

        results = await collect_results(commands, event, "off")

        assert qq_sender._debug_override == {}
        assert results == ["已清除运行时覆盖，当前配置页默认为开启。"]

    @pytest.mark.asyncio
    async def test_debug_off_without_override_returns_noop_message(self) -> None:
        commands = make_commands(qq_sender=FakeQQSender())
        event = make_event()

        results = await collect_results(commands, event, "off")

        assert results == ["当前没有运行时覆盖，无需清除。"]

    @pytest.mark.asyncio
    async def test_debug_status_reports_runtime_override(self) -> None:
        qq_sender = FakeQQSender()
        qq_sender._debug_override["__global"] = True
        commands = make_commands(config_default=False, qq_sender=qq_sender)
        event = make_event()

        results = await collect_results(commands, event, "status")

        assert results == ["QQ 发送诊断日志：开启（来源：runtime override）"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config_default", "expected"),
        [
            (False, "QQ 发送诊断日志：关闭（来源：config default (关闭)）"),
            (True, "QQ 发送诊断日志：开启（来源：config default (开启)）"),
        ],
    )
    async def test_debug_status_reports_config_default(
        self, config_default: bool, expected: str
    ) -> None:
        commands = make_commands(
            config_default=config_default,
            qq_sender=FakeQQSender(config_default=config_default),
        )
        event = make_event()

        results = await collect_results(commands, event, "status")

        assert results == [expected]

    @pytest.mark.asyncio
    async def test_debug_unknown_action_returns_usage(self) -> None:
        commands = make_commands(qq_sender=FakeQQSender())
        event = make_event()

        results = await collect_results(commands, event, "wat")

        assert len(results) == 1
        assert "/tg debug <on|off|status>" in results[0]

    @pytest.mark.asyncio
    async def test_get_root_reports_debug_enabled_default(self) -> None:
        commands = make_commands(config_default=True, qq_sender=FakeQQSender())
        event = make_event()

        results = []
        async for result in commands.get_config(event, "root"):
            results.append(result)

        assert len(results) == 1
        assert "debug_enabled_default" in results[0]
        assert "True" in results[0]

    @pytest.mark.asyncio
    async def test_set_root_debug_enabled_default_updates_config(self) -> None:
        commands = make_commands(qq_sender=FakeQQSender())
        commands.context._star_manager.reload = AsyncMock(return_value=(True, None))
        event = make_event()

        results = []
        async for result in commands.set_config(event, "root debug_enabled_default true"):
            results.append(result)

        assert commands.config["debug_enabled_default"] is True
        commands.config.save_config.assert_called_once_with()
        assert "已修改根配置 debug_enabled_default" in results[0]
        assert "已自动重载插件" in results[1]

    @pytest.mark.asyncio
    async def test_set_root_debug_enabled_default_rejects_unknown_token(self) -> None:
        commands = make_commands(qq_sender=FakeQQSender())
        commands.config.pop("debug_enabled_default", None)
        event = make_event()

        results = []
        async for result in commands.set_config(event, "root debug_enabled_default maybe"):
            results.append(result)

        assert "❌ 值格式错误" in results[0]
        assert "debug_enabled_default" in results[0]
        assert "maybe" in results[0]
        assert "debug_enabled_default" not in commands.config
        commands.config.save_config.assert_not_called()
        commands.context._star_manager.reload.assert_not_called()
