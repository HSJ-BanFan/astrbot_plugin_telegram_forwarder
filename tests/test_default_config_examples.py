import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_schema():
    return json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))


def test_default_filter_regex_catches_nsfw_warning_without_broad_false_positive():
    schema = load_schema()
    pattern = schema["forward_config"]["items"]["filter_regex"]["default"]
    regex = re.compile(pattern, re.IGNORECASE | re.DOTALL)

    assert regex.search("#NSFW #我有一个朋友")
    assert regex.search("⚠️ NSFW 提前预警 ⚠️")
    assert regex.search("⚠️ 前方高能 提前预警 ⚠️")
    assert not regex.search("本周进行网络安全预警演练")
    assert not regex.search("普通图片消息")


def test_default_nsfw_merge_example_links_warning_to_one_following_message():
    schema = load_schema()
    rules = schema["merge_rules"]["default"]
    rule = next(item for item in rules if item["name"] == "心惊报 NSFW 预警关联下一条")

    assert rule == {
        "__template_key": "default",
        "name": "心惊报 NSFW 预警关联下一条",
        "channel": "xinjingdaily",
        "rule_class": "KeywordNextNMerge",
        "params": {
            "trigger_keywords": [],
            "trigger_regex": r"(?:NSFW|前方高能)\s*提前预警",
            "next_count": 1,
            "time_window_seconds": 15,
        },
    }


def test_default_examples_do_not_include_local_filter_configuration():
    schema = load_schema()
    pattern = schema["forward_config"]["items"]["filter_regex"]["default"]

    assert schema["forward_config"]["items"]["filter_keywords"]["default"] == []
    assert pattern == r"#\s*NSFW\b|(?:NSFW|前方高能)\s*提前预警"


def test_default_content_safety_config_is_disabled_and_complete():
    schema = load_schema()
    items = schema["forward_config"]["items"]

    assert items["ai_filter_enabled"]["default"] is False
    assert items["qr_filter_enabled"]["default"] is False
    assert items["ai_filter_base_url"]["default"] == ""
    assert items["ai_filter_api_key"]["default"] == ""
    assert items["ai_filter_model"]["default"] == ""
    assert "NSFW" in items["ai_filter_prompt"]["default"]
    assert "二维码" in items["ai_filter_prompt"]["default"]
    assert "\\n" not in items["ai_filter_prompt"]["default"]
    assert items["qr_filter_mode"]["default"] == "风险二维码"
    assert "网贷" in items["qr_risk_keywords"]["default"]
