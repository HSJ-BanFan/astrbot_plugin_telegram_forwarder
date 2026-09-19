from __future__ import annotations

from collections.abc import Callable
from typing import Any

from telethon.tl.types import Message  # type: ignore

from astrbot.api import logger

try:
    from .screening_pipeline import (
        ContentScreeningPipeline,
        ScreeningStage,
        ScreeningVerdict,
        VerdictAction,
    )
except (ImportError, ValueError):
    try:
        from core.filters.screening_pipeline import (
            ContentScreeningPipeline,
            ScreeningStage,
            ScreeningVerdict,
            VerdictAction,
        )
    except (ImportError, ValueError):
        import sys
        from pathlib import Path

        _cur = Path(__file__).resolve().parent
        if str(_cur) not in sys.path:
            sys.path.insert(0, str(_cur))
        from screening_pipeline import (  # type: ignore
            ContentScreeningPipeline,
            ScreeningStage,
            ScreeningVerdict,
            VerdictAction,
        )


class MessageFilter:
    """消息过滤器 - 处理关键词、正则表达式等过滤逻辑（向后兼容门面）。

    底层基于 ContentScreeningPipeline 驱动，保留原 filter_messages 接口与黑名单过滤语义。
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.pipeline = ContentScreeningPipeline(config)

    def filter_messages(
        self, messages: list[tuple[str, Message]], logger_func: Callable | None = None
    ) -> list[tuple[str, Message]]:
        """应用过滤规则，返回过滤后的消息列表。"""
        forward_config = (
            self.config.get("forward_config", {})
            if isinstance(self.config, dict)
            else {}
        )
        filter_keywords = forward_config.get("filter_keywords", [])
        filter_regex = forward_config.get("filter_regex", "")
        filter_regex_patterns = forward_config.get("filter_regex_patterns", [])
        source_channels = (
            self.config.get("source_channels", [])
            if isinstance(self.config, dict)
            else []
        )

        if (
            not filter_keywords
            and not filter_regex
            and not filter_regex_patterns
            and not source_channels
        ):
            return messages

        filtered_messages = []
        for channel_name, msg in messages:
            verdict = self.pipeline.evaluate_sync(
                msg, channel_name, stage=ScreeningStage.INGESTION
            )
            if verdict.is_dropped:
                if logger_func:
                    msg_text = (getattr(msg, "text", "") or "")[:50]
                    if "regex" in verdict.reason.lower() or "正则" in verdict.reason:
                        logger_func(
                            f"[Filter] Filtered by regex: {channel_name} - {msg_text}"
                        )
                    else:
                        logger_func(
                            f"[Filter] Filtered by keyword: {channel_name} - {msg_text}"
                        )
                continue
            filtered_messages.append((channel_name, msg))
        return filtered_messages
