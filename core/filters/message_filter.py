import re
from typing import List, Tuple, Callable
from telethon.tl.types import Message

from astrbot.api import logger


class MessageFilter:
    """消息过滤器 - 处理关键词、正则表达式、hashtag 等过滤逻辑"""

    def __init__(self, config: dict):
        self.config = config

    def filter_messages(
        self, messages: List[Tuple[str, Message]], logger_func: Callable = None
    ) -> List[Tuple[str, Message]]:
        """
        应用过滤规则，返回过滤后的消息列表
        """
        # 获取全局配置
        forward_config = self.config.get("forward_config", {})
        filter_keywords = forward_config.get("filter_keywords", [])
        filter_regex = forward_config.get("filter_regex", "")

        if not any([filter_keywords, filter_regex]):
            return messages

        filtered_messages = []
        for channel_name, msg in messages:
            msg_text = (msg.text or "").lower()

            # 1. 关键词过滤
            if filter_keywords:
                if any(keyword.lower() in msg_text for keyword in filter_keywords):
                    if logger_func:
                        logger_func(
                            f"[Filter] Filtered by keyword: {channel_name} - {msg_text[:50]}"
                        )
                    continue

            # 2. 正则过滤
            if filter_regex:
                try:
                    if re.search(filter_regex, msg.text or ""):
                        if logger_func:
                            logger_func(
                                f"[Filter] Filtered by regex: {channel_name} - {msg_text[:50]}"
                            )
                        continue
                except re.error as e:
                    logger.error(f"Invalid regex pattern: {e}")

            filtered_messages.append((channel_name, msg))

        return filtered_messages
