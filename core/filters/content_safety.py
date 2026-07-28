import asyncio
import base64
import io
import ipaddress
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp
from astrbot.api import logger

JSON_CONTRACT = """
无论上面的自定义规则如何描述，你都必须只返回 JSON 对象，不得使用 Markdown 代码块或附加文字。
JSON 必须且只能包含两个字段：
{"filter": true/false, "msg": "判断原因"}
filter 必须是 JSON 布尔值；msg 必须是非空字符串。
""".strip()

DEFAULT_AI_FILTER_PROMPT = """判断消息文字和图片是否应该过滤。重点过滤：
1. NSFW、色情、裸露或明显性暗示内容；
2. 二维码推广，尤其是网贷、借款、博彩、色情、诈骗类二维码；
3. 诱导扫码、贷款秒到账、虚假金融推广等风险信息。
正常新闻、普通聊天、技术文档和无风险二维码不要过滤。
""".strip()

DEFAULT_QR_RISK_KEYWORDS = [
    "loan",
    "借款",
    "贷款",
    "网贷",
    "秒到账",
    "博彩",
    "赌博",
    "色情",
    "约炮",
    "裸聊",
]


def parse_ai_decision(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"filter", "msg"}:
        raise ValueError("AI 返回必须且只能包含 filter 和 msg")
    if not isinstance(value["filter"], bool):
        raise ValueError("filter 必须是布尔值")
    if not isinstance(value["msg"], str) or not value["msg"].strip():
        raise ValueError("msg 必须是非空字符串")
    return {"filter": value["filter"], "msg": value["msg"].strip()}


def build_ai_prompt(custom_prompt: str) -> str:
    prompt = str(custom_prompt or "").strip() or DEFAULT_AI_FILTER_PROMPT
    return f"{prompt}\n\n{JSON_CONTRACT}"


def openai_chat_url(base_url: str, allow_private_endpoint: bool = False) -> str:
    url = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI Base URL 必须是完整的 HTTP(S) 地址")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not allow_private_endpoint:
        raise ValueError("AI Base URL 不允许使用 IP 地址，请显式开启私网端点")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def image_data_url(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type = "image/png"
    elif image_bytes.startswith((b"GIF87a", b"GIF89a")):
        mime_type = "image/gif"
    elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        mime_type = "image/webp"
    else:
        mime_type = "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def is_risky_qr_payload(payload: str, keywords: list[str]) -> bool:
    text = unquote(str(payload or "")).lower()
    return any(
        str(keyword).strip().lower() in text
        for keyword in keywords
        if str(keyword).strip()
    )


class ContentSafetyFilter:
    def __init__(
        self,
        *,
        qr_decoder: Callable[[bytes], list[str]] | None = None,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
    ):
        self.qr_decoder = qr_decoder or self._decode_qr
        self.session_factory = session_factory

    @staticmethod
    def _decode_qr(image_bytes: bytes) -> list[str] | None:
        try:
            import zxingcpp
            from PIL import Image
        except ImportError:
            return None
        with Image.open(io.BytesIO(image_bytes)) as image:
            payloads = []
            for frame_index in range(min(4, getattr(image, "n_frames", 1))):
                image.seek(frame_index)
                frame = image.copy()
                if frame.width * frame.height > 16_000_000:
                    frame.thumbnail((4000, 4000))
                payloads.extend(
                    str(item.text or "") for item in zxingcpp.read_barcodes(frame)
                )
        return list(dict.fromkeys(payloads))

    def check_qr(self, image_bytes: bytes, config: dict) -> dict[str, Any]:
        if not config.get("qr_filter_enabled", False) or not image_bytes:
            return {"filter": False, "msg": "二维码过滤未启用"}
        try:
            payloads = self.qr_decoder(image_bytes)
        except Exception as exc:
            logger.warning(f"[ContentSafety] 二维码识别不可用: {type(exc).__name__}")
            return {"filter": False, "msg": "二维码识别不可用，已放行"}
        if payloads is None:
            logger.warning("[ContentSafety] 二维码识别依赖不可用")
            return {"filter": False, "msg": "二维码识别不可用，已放行"}
        if not payloads:
            return {"filter": False, "msg": "未检测到二维码"}
        mode = str(config.get("qr_filter_mode") or "风险二维码")
        if mode == "全部二维码":
            return {"filter": True, "msg": "检测到二维码"}
        keywords = config.get("qr_risk_keywords") or DEFAULT_QR_RISK_KEYWORDS
        if any(is_risky_qr_payload(payload, keywords) for payload in payloads):
            return {"filter": True, "msg": "二维码命中风险规则"}
        return {"filter": False, "msg": "二维码未命中风险规则"}

    async def check(
        self, text: str, image_bytes: bytes | None, config: dict
    ) -> dict[str, Any]:
        qr_result = await asyncio.to_thread(self.check_qr, image_bytes or b"", config)
        if qr_result["filter"]:
            return qr_result
        return await self.check_ai(text, image_bytes, config)

    async def check_ai(
        self, text: str, image_bytes: bytes | None, config: dict
    ) -> dict[str, Any]:
        if not config.get("ai_filter_enabled", False):
            return {"filter": False, "msg": "AI 过滤未启用"}
        base_url = str(config.get("ai_filter_base_url") or "").strip()
        api_key = str(config.get("ai_filter_api_key") or "")
        model = str(config.get("ai_filter_model") or "").strip()
        if not base_url or not api_key or not model:
            return {"filter": False, "msg": "AI 过滤配置不完整，已放行"}

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": text or "(无文字)",
            }
        ]
        if image_bytes:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url(image_bytes)},
                }
            )
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": build_ai_prompt(config.get("ai_filter_prompt", "")),
                },
                {"role": "user", "content": content},
            ],
            "temperature": 0,
        }
        try:
            timeout_seconds = min(
                60, max(1, int(config.get("ai_filter_timeout") or 20))
            )
        except (TypeError, ValueError):
            timeout_seconds = 20
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with self.session_factory(timeout=timeout) as session:
                async with session.post(
                    openai_chat_url(
                        base_url,
                        bool(config.get("ai_filter_allow_private_endpoint", False)),
                    ),
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                    allow_redirects=False,
                ) as response:
                    if response.status < 200 or response.status >= 300:
                        raise RuntimeError(f"AI HTTP {response.status}")
                    data = await response.json()
            raw = data["choices"][0]["message"]["content"]
            return parse_ai_decision(raw)
        except Exception as exc:
            logger.warning(f"[ContentSafety] AI 过滤不可用: {type(exc).__name__}")
            return {"filter": False, "msg": "AI 过滤不可用，已放行"}
