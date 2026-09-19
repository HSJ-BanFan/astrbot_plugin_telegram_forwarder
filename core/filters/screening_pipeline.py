from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from telethon.tl.types import Message  # type: ignore

from astrbot.api import logger

try:
    from .content_safety import DEFAULT_QR_RISK_KEYWORDS, ContentSafetyFilter
except (ImportError, ValueError):
    try:
        from core.filters.content_safety import (
            DEFAULT_QR_RISK_KEYWORDS,
            ContentSafetyFilter,
        )
    except (ImportError, ValueError):
        from content_safety import (  # type: ignore
            DEFAULT_QR_RISK_KEYWORDS,
            ContentSafetyFilter,
        )


class VerdictAction(str, Enum):
    ALLOW = "allow"
    DROP = "drop"


class ScreeningStage(str, Enum):
    INGESTION = "ingestion"
    DISPATCH = "dispatch"


@dataclass(frozen=True, slots=True)
class ScreeningVerdict:
    action: VerdictAction
    reason: str = ""
    stage: ScreeningStage = ScreeningStage.INGESTION
    matched_policy: str | None = None

    @property
    def is_allowed(self) -> bool:
        return self.action == VerdictAction.ALLOW

    @property
    def is_dropped(self) -> bool:
        return self.action == VerdictAction.DROP

    @classmethod
    def allow(
        cls, stage: ScreeningStage = ScreeningStage.INGESTION, reason: str = ""
    ) -> ScreeningVerdict:
        return cls(action=VerdictAction.ALLOW, reason=reason, stage=stage)

    @classmethod
    def drop(
        cls,
        reason: str,
        *,
        stage: ScreeningStage = ScreeningStage.INGESTION,
        matched_policy: str | None = None,
    ) -> ScreeningVerdict:
        return cls(
            action=VerdictAction.DROP,
            reason=reason,
            stage=stage,
            matched_policy=matched_policy,
        )


@dataclass(frozen=True, slots=True)
class ChannelScreeningPolicy:
    channel_name: str
    keywords: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()
    qr_filter_enabled: bool = False
    qr_filter_mode: str = "风险二维码"
    qr_risk_keywords: tuple[str, ...] = ()
    ai_filter_enabled: bool = False
    ai_filter_base_url: str = ""
    ai_filter_api_key: str = ""
    ai_filter_model: str = ""
    ai_filter_prompt: str = ""
    ai_filter_timeout: int = 20
    ai_filter_allow_private_endpoint: bool = False
    max_image_mb: int = 5
    ignore_global_filters: bool = False


class ContentScreeningPipeline:
    """统一内容审核流水线引擎。

    封装双阶段审核（INGESTION 阶段快速文本正则过滤、DISPATCH 阶段深度多模态审核）、
    频道专属规则继承覆盖、LLM 周期调用预算以及图片尺寸限制降级。
    """

    def __init__(
        self,
        config: dict[str, Any] | Any,
        *,
        content_safety_filter: ContentSafetyFilter | None = None,
    ):
        self.config = config
        self.content_safety_filter = content_safety_filter or ContentSafetyFilter()
        self._channel_policies: dict[str, ChannelScreeningPolicy] = {}
        self._ai_calls_remaining: int = 0
        self.reset_cycle_budget()

    def reset_cycle_budget(self) -> None:
        """重置本轮 AI 审核调用预算。"""
        cfg = self._get_forward_config()
        try:
            max_calls = int(cfg.get("ai_filter_max_calls_per_cycle", 5))
        except (TypeError, ValueError):
            max_calls = 5
        self._ai_calls_remaining = max(0, max_calls)

    def get_remaining_ai_budget(self) -> int:
        """获取当前周期剩余的 AI 审核调用额度。"""
        return self._ai_calls_remaining

    def reload_config(self, new_config: dict[str, Any] | Any) -> None:
        """重新加载配置并清空已缓存的频道规则策略。"""
        self.config = new_config
        self._channel_policies.clear()
        self.reset_cycle_budget()

    def _get_forward_config(self) -> dict[str, Any]:
        if isinstance(self.config, dict):
            return self.config.get("forward_config", self.config)
        if hasattr(self.config, "get"):
            return self.config.get("forward_config", {})
        return {}

    def _normalize_channel_name(self, raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        if text.startswith("#") and len(text) > 1 and (text[1] == "-" or text[1].isdigit()):
            text = text[1:]
        text = text.lstrip("@").strip()
        lower = text.lower()
        if lower.startswith("https://t.me/") or lower.startswith("http://t.me/"):
            text = text.split("://", 1)[1]
        if text.lower().startswith("t.me/"):
            text = text.split("/", 1)[1]
        text = text.split("?", 1)[0].split("#", 1)[0].strip("/")
        if "/" in text:
            text = text.split("/", 1)[0]
        return text.lstrip("@").strip().lower()

    def _get_channel_raw_config(self, channel_name: str) -> dict[str, Any]:
        norm = self._normalize_channel_name(channel_name)
        if not norm:
            return {}
        source_channels = []
        if isinstance(self.config, dict):
            source_channels = self.config.get("source_channels", [])
        elif hasattr(self.config, "get"):
            source_channels = self.config.get("source_channels", [])

        if isinstance(source_channels, list):
            for item in source_channels:
                if isinstance(item, dict):
                    uname = (
                        item.get("channel_username")
                        or item.get("username")
                        or item.get("name")
                        or ""
                    )
                    if self._normalize_channel_name(str(uname)) == norm:
                        return item
        return {}

    def resolve_channel_policy(self, channel_name: str = "") -> ChannelScreeningPolicy:
        """解析并缓存指定频道的审核策略（合并全局与频道专属规则）。"""
        norm = self._normalize_channel_name(channel_name)
        if norm in self._channel_policies:
            return self._channel_policies[norm]

        global_cfg = self._get_forward_config()
        channel_cfg = self._get_channel_raw_config(channel_name)

        ignore_global = bool(channel_cfg.get("ignore_global_filters", False))

        # 1. 关键词解析
        g_kws = global_cfg.get("filter_keywords", [])
        c_kws = channel_cfg.get("filter_keywords", [])
        if ignore_global:
            raw_kws = list(c_kws) if isinstance(c_kws, (list, tuple)) else []
        else:
            raw_kws = (
                (list(g_kws) if isinstance(g_kws, (list, tuple)) else [])
                + (list(c_kws) if isinstance(c_kws, (list, tuple)) else [])
            )
        keywords: list[str] = []
        seen_kw: set[str] = set()
        for kw in raw_kws:
            k = str(kw or "").strip()
            if k and k.lower() not in seen_kw:
                seen_kw.add(k.lower())
                keywords.append(k)

        # 2. 正则表达式解析
        patterns: list[str] = []
        if not ignore_global:
            g_reg = str(global_cfg.get("filter_regex", "") or "").strip()
            if g_reg:
                patterns.append(g_reg)
            g_patterns = global_cfg.get("filter_regex_patterns", [])
            if isinstance(g_patterns, (list, tuple)):
                for p in g_patterns:
                    p_str = str(p or "").strip()
                    if p_str and p_str not in patterns:
                        patterns.append(p_str)

        c_reg = str(channel_cfg.get("filter_regex", "") or "").strip()
        if c_reg and c_reg not in patterns:
            patterns.append(c_reg)
        c_patterns = channel_cfg.get("filter_regex_patterns", [])
        if isinstance(c_patterns, (list, tuple)):
            for p in c_patterns:
                p_str = str(p or "").strip()
                if p_str and p_str not in patterns:
                    patterns.append(p_str)

        # 3. 二维码过滤解析
        if "qr_filter_enabled" in channel_cfg:
            qr_enabled = bool(channel_cfg["qr_filter_enabled"])
        elif "filter_qr_code_images" in channel_cfg:
            v = channel_cfg["filter_qr_code_images"]
            if v in ("开启", True):
                qr_enabled = True
            elif v in ("关闭", False):
                qr_enabled = False
            else:  # "继承全局"
                qr_enabled = bool(
                    global_cfg.get("qr_filter_enabled", False)
                    or global_cfg.get("filter_qr_code_images", False)
                )
        else:
            qr_enabled = bool(
                global_cfg.get("qr_filter_enabled", False)
                or global_cfg.get("filter_qr_code_images", False)
            )

        qr_mode = str(
            channel_cfg.get("qr_filter_mode")
            or global_cfg.get("qr_filter_mode")
            or "风险二维码"
        )
        qr_risk_kw = channel_cfg.get("qr_risk_keywords") or global_cfg.get(
            "qr_risk_keywords"
        )
        if qr_risk_kw is None:
            qr_risk_kw = DEFAULT_QR_RISK_KEYWORDS

        # 4. AI 过滤解析
        if "ai_filter_enabled" in channel_cfg:
            ai_enabled = bool(channel_cfg["ai_filter_enabled"])
        else:
            ai_enabled = bool(global_cfg.get("ai_filter_enabled", False))

        ai_base_url = str(
            channel_cfg.get("ai_filter_base_url")
            or global_cfg.get("ai_filter_base_url")
            or ""
        ).strip()
        ai_api_key = str(
            channel_cfg.get("ai_filter_api_key")
            or global_cfg.get("ai_filter_api_key")
            or ""
        )
        ai_model = str(
            channel_cfg.get("ai_filter_model")
            or global_cfg.get("ai_filter_model")
            or ""
        ).strip()
        ai_prompt = str(
            channel_cfg.get("ai_filter_prompt")
            or global_cfg.get("ai_filter_prompt")
            or ""
        )
        try:
            ai_timeout = int(
                channel_cfg.get("ai_filter_timeout")
                or global_cfg.get("ai_filter_timeout")
                or 20
            )
        except (TypeError, ValueError):
            ai_timeout = 20

        ai_private = bool(
            channel_cfg.get(
                "ai_filter_allow_private_endpoint",
                global_cfg.get("ai_filter_allow_private_endpoint", False),
            )
        )

        # 5. 图片限制解析
        try:
            max_image_mb = int(
                channel_cfg.get("content_filter_max_image_mb")
                or global_cfg.get("content_filter_max_image_mb")
                or 5
            )
        except (TypeError, ValueError):
            max_image_mb = 5
        max_image_mb = max(1, max_image_mb)

        policy = ChannelScreeningPolicy(
            channel_name=channel_name,
            keywords=tuple(keywords),
            regex_patterns=tuple(patterns),
            qr_filter_enabled=qr_enabled,
            qr_filter_mode=qr_mode,
            qr_risk_keywords=tuple(qr_risk_kw),
            ai_filter_enabled=ai_enabled,
            ai_filter_base_url=ai_base_url,
            ai_filter_api_key=ai_api_key,
            ai_filter_model=ai_model,
            ai_filter_prompt=ai_prompt,
            ai_filter_timeout=ai_timeout,
            ai_filter_allow_private_endpoint=ai_private,
            max_image_mb=max_image_mb,
            ignore_global_filters=ignore_global,
        )
        self._channel_policies[norm] = policy
        return policy

    @staticmethod
    def extract_search_text(msg: Any) -> str:
        """从消息对象中提取待检测文本（包含正文、按钮文字、说明等）。"""
        if msg is None:
            return ""
        if isinstance(msg, str):
            return msg.strip()

        text_content = getattr(msg, "text", "") or ""
        if not text_content and hasattr(msg, "message"):
            text_content = getattr(msg, "message", "") or ""
        if not text_content and hasattr(msg, "caption"):
            text_content = getattr(msg, "caption", "") or ""

        button_text = ""
        reply_markup = getattr(msg, "reply_markup", None)
        if reply_markup and hasattr(reply_markup, "rows"):
            button_parts = []
            for row in getattr(reply_markup, "rows", []):
                for btn in getattr(row, "buttons", []):
                    btn_text = getattr(btn, "text", None)
                    if btn_text:
                        button_parts.append(str(btn_text))
            button_text = " ".join(button_parts)

        return f"{text_content} {button_text}".strip()

    @staticmethod
    def _is_keyword_matched(keyword: str, text: str) -> bool:
        """判断关键词是否命中。

        ASCII 关键词采用单词边界匹配（防止 'cat' 误匹配 'catch'），
        非 ASCII 关键词采用大小写不敏感子串匹配。
        """
        if not keyword or not text:
            return False
        kw = str(keyword).strip()
        if not kw:
            return False

        if kw.isascii():
            prefix = r"(?<![a-zA-Z0-9])" if kw[0].isalnum() else ""
            suffix = r"(?![a-zA-Z0-9])" if kw[-1].isalnum() else ""
            pattern = rf"{prefix}{re.escape(kw)}{suffix}"
            return bool(re.search(pattern, text, re.IGNORECASE))

        return kw.lower() in text.lower()

    def _evaluate_text(
        self,
        search_text: str,
        policy: ChannelScreeningPolicy,
        stage: ScreeningStage,
    ) -> ScreeningVerdict | None:
        """评估文本与正则规则。若命中则返回 DROP 判定，否则返回 None。"""
        # 1. 关键词过滤
        for kw in policy.keywords:
            if self._is_keyword_matched(kw, search_text):
                return ScreeningVerdict(
                    action=VerdictAction.DROP,
                    reason=f"Filtered by keyword: {kw}",
                    stage=stage,
                    matched_policy=kw,
                )

        # 2. 正则过滤
        for pattern in policy.regex_patterns:
            if pattern:
                try:
                    if re.search(pattern, search_text):
                        return ScreeningVerdict(
                            action=VerdictAction.DROP,
                            reason=f"Filtered by regex: {pattern[:30]}",
                            stage=stage,
                            matched_policy=pattern,
                        )
                except re.error as exc:
                    logger.error(f"Invalid regex pattern: {exc}")

        return None

    def evaluate_sync(
        self,
        msg: Any,
        channel_name: str = "",
        stage: ScreeningStage = ScreeningStage.INGESTION,
    ) -> ScreeningVerdict:
        """同步评估消息（主要用于 INGESTION 快速路径及旧版 MessageFilter 门面）。"""
        policy = self.resolve_channel_policy(channel_name)
        search_text = self.extract_search_text(msg)
        text_verdict = self._evaluate_text(search_text, policy, stage)
        if text_verdict is not None:
            return text_verdict
        return ScreeningVerdict(action=VerdictAction.ALLOW, stage=stage)

    async def evaluate(
        self,
        msg: Any,
        channel_name: str = "",
        stage: ScreeningStage = ScreeningStage.INGESTION,
        image_bytes: bytes | None = None,
    ) -> ScreeningVerdict:
        """双阶段内容审核评估主入口。

        - INGESTION 阶段：执行关键词与正则表达式快速校验。
        - DISPATCH 阶段：执行文本校验、图片大小边界检查、本地二维码安全检测与 AI 多模态审核。
        """
        policy = self.resolve_channel_policy(channel_name)
        search_text = self.extract_search_text(msg)

        # 无论处于哪个阶段，均首先执行文本与正则规则检查
        text_verdict = self._evaluate_text(search_text, policy, stage)
        if text_verdict is not None:
            return text_verdict

        # 若处于入队前阶段，快速过滤已通过，直接放行
        if stage == ScreeningStage.INGESTION:
            return ScreeningVerdict(action=VerdictAction.ALLOW, stage=stage)

        # --- DISPATCH 阶段深度安全评估 ---

        # 1. 校验图片大小与降级策略
        max_bytes = policy.max_image_mb * 1024 * 1024
        declared_size = int(getattr(getattr(msg, "file", None), "size", 0) or 0)
        if declared_size > max_bytes:
            logger.info(
                f"[Screening] 消息图片声明大小超过限制 ({policy.max_image_mb}MB)，降级为仅文字评估。"
            )
            image_bytes = None
        elif image_bytes is not None and len(image_bytes) > max_bytes:
            logger.info(
                f"[Screening] 图片数据大小 ({len(image_bytes)} bytes) 超过上限 ({max_bytes} bytes)，降级为仅文字评估。"
            )
            image_bytes = None

        # 2. 本地二维码风险检测
        if policy.qr_filter_enabled and image_bytes:
            qr_config = {
                "qr_filter_enabled": True,
                "qr_filter_mode": policy.qr_filter_mode,
                "qr_risk_keywords": list(policy.qr_risk_keywords),
            }
            try:
                qr_result = await asyncio.to_thread(
                    self.content_safety_filter.check_qr, image_bytes, qr_config
                )
            except Exception as exc:
                logger.warning(
                    f"[Screening] 二维码检测异常: {type(exc).__name__}，降级放行"
                )
                qr_result = {"filter": False}

            if qr_result.get("filter"):
                msg_reason = qr_result.get("msg") or "二维码命中风险规则"
                logger.info(f"[Screening] 消息已过滤: {msg_reason}")
                return ScreeningVerdict(
                    action=VerdictAction.DROP,
                    reason=msg_reason,
                    stage=stage,
                    matched_policy="qr_filter",
                )

        # 3. AI 多模态内容审核
        if policy.ai_filter_enabled:
            if self._ai_calls_remaining <= 0:
                logger.info("[Screening] 本轮 AI 分析预算已用尽，仅保留本地过滤结果。")
                return ScreeningVerdict(
                    action=VerdictAction.ALLOW,
                    reason="AI budget exhausted; allowed by fallback",
                    stage=stage,
                )

            self._ai_calls_remaining -= 1
            ai_config = {
                "ai_filter_enabled": True,
                "ai_filter_base_url": policy.ai_filter_base_url,
                "ai_filter_api_key": policy.ai_filter_api_key,
                "ai_filter_model": policy.ai_filter_model,
                "ai_filter_prompt": policy.ai_filter_prompt,
                "ai_filter_timeout": policy.ai_filter_timeout,
                "ai_filter_allow_private_endpoint": (
                    policy.ai_filter_allow_private_endpoint
                ),
            }
            try:
                ai_result = await self.content_safety_filter.check_ai(
                    search_text, image_bytes, ai_config
                )
            except Exception as exc:
                logger.warning(
                    f"[Screening] AI 审核异常: {type(exc).__name__}，降级放行"
                )
                ai_result = {"filter": False}

            if ai_result.get("filter"):
                msg_reason = ai_result.get("msg") or "AI 审核未通过"
                logger.info(f"[Screening] 消息已过滤: {msg_reason}")
                return ScreeningVerdict(
                    action=VerdictAction.DROP,
                    reason=msg_reason,
                    stage=stage,
                    matched_policy="ai_filter",
                )

        return ScreeningVerdict(action=VerdictAction.ALLOW, stage=stage)
