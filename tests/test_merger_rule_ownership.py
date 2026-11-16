import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


class FakeMessage:
    def __init__(self, message_id, text, date):
        self.id = message_id
        self.text = text
        self.date = date
        self.grouped_id = None


class SyntheticAssociationRule:
    def __init__(self, _config):
        pass

    def can_merge(self, _channel_name, first, second):
        return first[1].text == "preview" and second[1].text == "original"

    def get_group_key(self, message):
        return f"{message[0]}:association:{message[1].id}"

    def apply_merge_marker(self, messages, group_key):
        for _, message in messages:
            message._merge_group_id = group_key


def load_merger_module():
    root = Path(__file__).resolve().parents[1]
    package = "astrbot_plugin_telegram_forwarder"
    modules = {
        "telethon.tl.types": SimpleNamespace(
            Message=FakeMessage,
        ),
        "astrbot.api": SimpleNamespace(logger=MagicMock()),
    }
    for suffix, directory in (
        ("", root),
        (".core", root / "core"),
        (".core.mergers", root / "core" / "mergers"),
    ):
        name = package + suffix
        module = ModuleType(name)
        module.__path__ = [str(directory)]
        modules[name] = module
    name = package + ".core.mergers.merger"
    with patch.dict(sys.modules, modules):
        for suffix in ("base", "keyword_next", "merger"):
            sys.modules.pop(package + ".core.mergers." + suffix, None)
        spec = importlib.util.spec_from_file_location(
            name, root / "core" / "mergers" / "merger.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def keyword_rule(keyword, count, channel="example"):
    return {
        "channel": channel,
        "rule_class": "KeywordNextNMerge",
        "params": {
            "trigger_keywords": [keyword],
            "next_count": count,
            "time_window_seconds": 60,
        },
    }


def messages_from_texts(*texts, channel="example"):
    now = datetime.now(timezone.utc) - timedelta(seconds=10)
    return [
        (channel, FakeMessage(i + 1, text, now + timedelta(seconds=i)))
        for i, text in enumerate(texts)
    ]


def test_nested_trigger_cannot_defer_messages_owned_by_a_complete_group():
    merger = load_merger_module().MessageMerger(
        {"merge_rules": [keyword_rule("start", 2)]}
    )
    messages = messages_from_texts("start", "start", "payload")

    assert merger.find_defer_from_index("example", messages) is None
    merged = merger.merge_messages(messages)
    assert [message.id for _, message in merged] == [1, 2, 3]
    assert len({message._merge_group_id for _, message in merged}) == 1


def test_incomplete_rule_cannot_defer_a_group_completed_by_another_rule():
    merger = load_merger_module().MessageMerger(
        {"merge_rules": [keyword_rule("start", 5), keyword_rule("start", 1)]}
    )
    messages = messages_from_texts("start", "payload")

    assert merger.find_defer_from_index("example", messages) is None
    merged = merger.merge_messages(messages)
    assert len({message._merge_group_id for _, message in merged}) == 1


def test_pending_group_after_complete_group_defers_only_the_unowned_tail():
    merger = load_merger_module().MessageMerger(
        {"merge_rules": [keyword_rule("start", 2)]}
    )
    messages = messages_from_texts("start", "start", "payload", "start", "tail")

    assert merger.find_defer_from_index("example", messages) == 3
    merged = merger.merge_messages(messages[:3])
    assert len({message._merge_group_id for _, message in merged}) == 1


def test_rule_order_selects_first_complete_group_without_reusing_messages():
    merger = load_merger_module().MessageMerger(
        {"merge_rules": [keyword_rule("start", 1), keyword_rule("start", 2)]}
    )
    messages = messages_from_texts("start", "payload", "another")

    assert merger.find_defer_from_index("example", messages) is None
    merged = merger.merge_messages(messages)
    assert merged[0][1]._merge_group_id == merged[1][1]._merge_group_id
    assert not hasattr(merged[2][1], "_merge_group_id")
    assert [message.id for _, message in merged] == [1, 2, 3]


def test_incomplete_trigger_without_another_complete_rule_still_waits():
    merger = load_merger_module().MessageMerger(
        {"merge_rules": [keyword_rule("start", 2)]}
    )
    messages = messages_from_texts("ordinary", "start", "payload")

    assert merger.find_defer_from_index("example", messages) == 1


def test_other_channels_cannot_complete_or_defer_the_requested_channel():
    merger = load_merger_module().MessageMerger(
        {"merge_rules": [keyword_rule("start", 2)]}
    )
    messages = messages_from_texts("start", "payload", channel="other")

    assert merger.find_defer_from_index("example", messages) is None
    assert merger.merge_messages(messages) == messages


def association_merger(next_count=None):
    module = load_merger_module()
    module.MessageMerger.RULE_CLASSES = {
        **module.MessageMerger.RULE_CLASSES,
        "SyntheticAssociationRule": SyntheticAssociationRule,
    }
    rules = [
        {"channel": "example", "rule_class": "SyntheticAssociationRule", "params": {}}
    ]
    if next_count is not None:
        rules.append(keyword_rule("start", next_count))
    return module.MessageMerger({"merge_rules": rules})


def association_messages():
    return messages_from_texts("preview", "start", "payload", "original")


def test_defer_boundary_cannot_split_an_earlier_noncontiguous_group():
    merger = association_merger(5)

    assert merger.find_defer_from_index("example", association_messages()) == 0


def test_a_reserved_original_cannot_count_as_another_rules_missing_message():
    merger = association_merger(2)

    assert merger.find_defer_from_index("example", association_messages()) == 0


def test_noncontiguous_groups_without_a_keyword_wait_rule_remain_supported():
    merger = association_merger()
    messages = association_messages()

    assert merger.find_defer_from_index("example", messages) is None
    merger.merge_messages(messages)
    assert messages[0][1]._merge_group_id == messages[3][1]._merge_group_id
    assert not hasattr(messages[1][1], "_merge_group_id")
    assert not hasattr(messages[2][1], "_merge_group_id")


def test_retired_channel_strategy_is_not_registered_or_applied():
    module = load_merger_module()
    assert set(module.MessageMerger.RULE_CLASSES) == {"KeywordNextNMerge"}
    merger = module.MessageMerger(
        {
            "merge_rules": [
                {
                    "channel": "example",
                    "rule_class": "SomeACGPreviewPlusOriginal",
                    "params": {},
                }
            ]
        }
    )
    messages = messages_from_texts("preview", "original")

    assert merger.merge_messages(messages) == messages
    assert merger.find_defer_from_index("example", messages) is None
    assert all(not hasattr(message, "_merge_group_id") for _, message in messages)
