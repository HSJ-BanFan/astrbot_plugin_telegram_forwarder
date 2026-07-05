import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


class FakeMessage:
    def __init__(self, message_id: int, text: str, date: datetime):
        self.id = message_id
        self.text = text
        self.date = date
        self.grouped_id = None


def _package(name: str, path: Path | None = None) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = [] if path is None else [str(path)]
    return module


def load_keyword_next_module() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "mergers" / "keyword_next.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.mergers.keyword_next"

    stubbed_modules = {
        "telethon": MagicMock(),
        "telethon.tl": MagicMock(),
        "telethon.tl.types": SimpleNamespace(Message=FakeMessage),
        "astrbot": MagicMock(),
        "astrbot.api": SimpleNamespace(logger=MagicMock()),
        "astrbot_plugin_telegram_forwarder": _package(
            "astrbot_plugin_telegram_forwarder", root
        ),
        "astrbot_plugin_telegram_forwarder.core": _package(
            "astrbot_plugin_telegram_forwarder.core", root / "core"
        ),
        "astrbot_plugin_telegram_forwarder.core.mergers": _package(
            "astrbot_plugin_telegram_forwarder.core.mergers",
            root / "core" / "mergers",
        ),
    }

    with patch.dict(sys.modules, stubbed_modules):
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder.core.mergers"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    return mod


def test_keyword_next_defer_ignores_other_channel_messages():
    mod = load_keyword_next_module()
    rule = mod.KeywordNextNMerge(
        {
            "trigger_keywords": ["start"],
            "next_count": 2,
            "time_window_seconds": 60,
        }
    )
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    messages = [
        ("channel-a", FakeMessage(1, "start", base_time)),
        ("channel-b", FakeMessage(2, "other", base_time + timedelta(seconds=1))),
        ("channel-a", FakeMessage(3, "same", base_time + timedelta(seconds=2))),
    ]

    assert (
        rule.find_defer_from_index(
            messages,
            "channel-a",
            now=base_time + timedelta(seconds=3),
        )
        == 0
    )


def test_keyword_next_defer_clears_after_enough_same_channel_messages():
    mod = load_keyword_next_module()
    rule = mod.KeywordNextNMerge(
        {
            "trigger_keywords": ["start"],
            "next_count": 2,
            "time_window_seconds": 60,
        }
    )
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    messages = [
        ("channel-a", FakeMessage(1, "start", base_time)),
        ("channel-b", FakeMessage(2, "other", base_time + timedelta(seconds=1))),
        ("channel-a", FakeMessage(3, "same 1", base_time + timedelta(seconds=2))),
        ("channel-a", FakeMessage(4, "same 2", base_time + timedelta(seconds=3))),
    ]

    assert (
        rule.find_defer_from_index(
            messages,
            "channel-a",
            now=base_time + timedelta(seconds=4),
        )
        is None
    )
