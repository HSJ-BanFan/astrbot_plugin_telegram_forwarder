import asyncio
import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "core" / "filters" / "content_safety.py"
MODULE_NAME = "astrbot_plugin_telegram_forwarder.core.filters.content_safety"


async def resolve_public_example(_hostname: str, _port: int):
    return [(socket.AF_INET, "93.184.216.34")]


def load_module():
    astrbot_api = SimpleNamespace(logger=MagicMock())
    with patch.dict(
        sys.modules,
        {
            "astrbot": MagicMock(),
            "astrbot.api": astrbot_api,
        },
    ):
        sys.modules.pop(MODULE_NAME, None)
        spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def test_parse_ai_decision_accepts_strict_json():
    module = load_module()

    assert module.parse_ai_decision('{"filter": true, "msg": "NSFW 图片"}') == {
        "filter": True,
        "msg": "NSFW 图片",
    }
    assert module.parse_ai_decision('{"filter": false, "msg": "普通内容"}') == {
        "filter": False,
        "msg": "普通内容",
    }


def test_parse_ai_decision_accepts_markdown_json_fence():
    module = load_module()

    assert module.parse_ai_decision(
        '```json\n{"filter": true, "msg": "NSFW 图片"}\n```'
    ) == {"filter": True, "msg": "NSFW 图片"}


@pytest.mark.parametrize(
    "raw",
    [
        '判断如下：{"filter": true, "msg": "bad"}',
        '{"filter": "true", "msg": "bad"}',
        '{"filter": true}',
        "not json",
    ],
)
def test_parse_ai_decision_rejects_non_strict_output(raw):
    module = load_module()

    with pytest.raises(ValueError):
        module.parse_ai_decision(raw)


def test_ai_prompt_always_appends_json_contract():
    module = load_module()

    prompt = module.build_ai_prompt("自定义判断标准")

    assert prompt.startswith("自定义判断标准")
    assert "只返回 JSON 对象" in prompt
    assert '"filter": true/false' in prompt
    assert '"msg": "判断原因"' in prompt


def test_openai_url_normalization():
    module = load_module()

    assert module.openai_chat_url("https://api.example.com/v1") == (
        "https://api.example.com/v1/chat/completions"
    )
    assert module.openai_chat_url("https://api.example.com/v1/") == (
        "https://api.example.com/v1/chat/completions"
    )
    assert module.openai_chat_url("https://api.example.com/v1/chat/completions") == (
        "https://api.example.com/v1/chat/completions"
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "ftp://example.com/v1",
        "file:///etc/passwd",
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "https://localhost./v1",
        "http://localhost:11434/v1",
        "https://metadata.google.internal/v1",
    ],
)
def test_openai_url_rejects_unsafe_endpoints_by_default(base_url):
    module = load_module()

    with pytest.raises(ValueError):
        module.openai_chat_url(base_url)


def test_openai_url_allows_private_endpoint_only_when_enabled():
    module = load_module()

    assert module.openai_chat_url("http://127.0.0.1:11434/v1", True) == (
        "http://127.0.0.1:11434/v1/chat/completions"
    )
    assert module.openai_chat_url("http://localhost:11434/v1", True) == (
        "http://localhost:11434/v1/chat/completions"
    )


def test_image_data_url_uses_detected_mime_type():
    module = load_module()

    assert module.image_data_url(b"\x89PNG\r\n\x1a\nrest").startswith(
        "data:image/png;base64,"
    )
    assert module.image_data_url(b"\xff\xd8\xffrest").startswith(
        "data:image/jpeg;base64,"
    )


def test_qr_risk_classification():
    module = load_module()

    assert module.is_risky_qr_payload(
        "https://loan.example/apply?channel=fast", ["loan", "借款"]
    )
    assert module.is_risky_qr_payload("立即借款，秒到账", ["loan", "借款"])
    assert module.is_risky_qr_payload(
        "https://example.com/?q=%E7%BD%91%E8%B4%B7", ["网贷"]
    )
    assert not module.is_risky_qr_payload("https://example.org/docs", ["loan", "借款"])


def test_local_qr_filter_modes():
    module = load_module()
    detector = MagicMock(return_value=["https://example.org/docs"])
    content_filter = module.ContentSafetyFilter(qr_decoder=detector)

    all_result = content_filter.check_qr(
        b"image", {"qr_filter_enabled": True, "qr_filter_mode": "全部二维码"}
    )
    risk_result = content_filter.check_qr(
        b"image",
        {
            "qr_filter_enabled": True,
            "qr_filter_mode": "风险二维码",
            "qr_risk_keywords": ["loan", "借款"],
        },
    )

    assert all_result == {"filter": True, "msg": "检测到二维码"}
    assert risk_result == {"filter": False, "msg": "二维码未命中风险规则"}


def test_qr_decoder_failure_is_fail_open():
    module = load_module()
    content_filter = module.ContentSafetyFilter(
        qr_decoder=MagicMock(side_effect=ValueError("broken image"))
    )

    result = content_filter.check_qr(
        b"broken", {"qr_filter_enabled": True, "qr_filter_mode": "全部二维码"}
    )

    assert result == {"filter": False, "msg": "二维码识别不可用，已放行"}


def test_qr_dependency_missing_is_reported_as_unavailable(monkeypatch):
    module = load_module()
    content_filter = module.ContentSafetyFilter()
    monkeypatch.setitem(sys.modules, "zxingcpp", None)

    result = content_filter.check_qr(
        b"image", {"qr_filter_enabled": True, "qr_filter_mode": "全部二维码"}
    )

    assert result == {"filter": False, "msg": "二维码识别不可用，已放行"}


def test_ai_request_contains_text_image_and_forced_json_contract():
    module = load_module()
    response = SimpleNamespace(
        status=200,
        json=AsyncMock(
            return_value={
                "choices": [
                    {"message": {"content": '{"filter": true, "msg": "网贷二维码"}'}}
                ]
            }
        ),
        text=AsyncMock(return_value=""),
    )
    response_context = MagicMock()
    response_context.__aenter__.return_value = response
    session = MagicMock()
    session.post.return_value = response_context
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    content_filter = module.ContentSafetyFilter(
        session_factory=lambda **_: session_context,
        address_resolver=resolve_public_example,
    )
    config = {
        "ai_filter_enabled": True,
        "ai_filter_base_url": "https://api.example.com/v1",
        "ai_filter_api_key": "test-key",
        "ai_filter_model": "vision-model",
        "ai_filter_prompt": "过滤不良推广",
        "ai_filter_timeout": 15,
    }

    result = asyncio.run(content_filter.check_ai("消息正文", b"jpeg", config))

    assert result == {"filter": True, "msg": "网贷二维码"}
    _, kwargs = session.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"]["model"] == "vision-model"
    assert kwargs["allow_redirects"] is False
    messages = kwargs["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("过滤不良推广")
    assert "只返回 JSON 对象" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    assert content[0]["text"] == "消息正文"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_ai_failure_is_fail_open_without_secret_logging():
    module = load_module()
    session = MagicMock()
    session.post.side_effect = RuntimeError("request failed with test-key")
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    content_filter = module.ContentSafetyFilter(
        session_factory=lambda **_: session_context,
        address_resolver=resolve_public_example,
    )
    config = {
        "ai_filter_enabled": True,
        "ai_filter_base_url": "https://api.example.com/v1",
        "ai_filter_api_key": "test-key",
        "ai_filter_model": "model",
        "ai_filter_prompt": "filter",
    }

    result = asyncio.run(content_filter.check_ai("text", None, config))

    assert result == {"filter": False, "msg": "AI 过滤不可用，已放行"}
    logged = " ".join(str(call) for call in module.logger.warning.call_args_list)
    assert "test-key" not in logged


def test_ai_invalid_timeout_uses_default_instead_of_crashing():
    module = load_module()
    session = MagicMock()
    session.post.side_effect = RuntimeError("offline")
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    content_filter = module.ContentSafetyFilter(
        session_factory=lambda **_: session_context,
        address_resolver=resolve_public_example,
    )
    config = {
        "ai_filter_enabled": True,
        "ai_filter_base_url": "https://api.example.com/v1",
        "ai_filter_api_key": "test-key",
        "ai_filter_model": "model",
        "ai_filter_timeout": "invalid",
    }

    result = asyncio.run(content_filter.check_ai("text", None, config))

    assert result == {"filter": False, "msg": "AI 过滤不可用，已放行"}


def test_ai_rejects_domain_that_resolves_to_private_address():
    module = load_module()

    async def resolve_private(_hostname: str, _port: int):
        return [(socket.AF_INET, "127.0.0.1")]

    session_factory = MagicMock()
    content_filter = module.ContentSafetyFilter(
        session_factory=session_factory,
        address_resolver=resolve_private,
    )
    config = {
        "ai_filter_enabled": True,
        "ai_filter_base_url": "https://private.example/v1",
        "ai_filter_api_key": "test-key",
        "ai_filter_model": "model",
    }

    result = asyncio.run(content_filter.check_ai("text", None, config))

    assert result == {"filter": False, "msg": "AI 过滤不可用，已放行"}
    session_factory.assert_not_called()


def test_combined_filter_short_circuits_ai_when_qr_matches():
    module = load_module()
    content_filter = module.ContentSafetyFilter(
        qr_decoder=lambda _: ["https://loan.example/apply"]
    )
    content_filter.check_ai = AsyncMock(
        return_value={"filter": False, "msg": "AI 放行"}
    )
    config = {
        "qr_filter_enabled": True,
        "qr_filter_mode": "风险二维码",
        "qr_risk_keywords": ["loan"],
        "ai_filter_enabled": True,
    }

    result = asyncio.run(content_filter.check("text", b"image", config))

    assert result == {"filter": True, "msg": "二维码命中风险规则"}
    content_filter.check_ai.assert_not_awaited()


def test_combined_filter_uses_ai_after_qr_passes():
    module = load_module()
    content_filter = module.ContentSafetyFilter(qr_decoder=lambda _: [])
    content_filter.check_ai = AsyncMock(
        return_value={"filter": True, "msg": "AI 判断为 NSFW"}
    )
    config = {
        "qr_filter_enabled": True,
        "ai_filter_enabled": True,
    }

    result = asyncio.run(content_filter.check("text", b"image", config))

    assert result == {"filter": True, "msg": "AI 判断为 NSFW"}
    content_filter.check_ai.assert_awaited_once_with("text", b"image", config)
