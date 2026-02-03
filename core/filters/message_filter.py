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

        Args:
            messages: (channel_name, message) 元组列表
            logger_func: 可选的日志记录函数（用于兼容不同 logger）

        Returns:
            过滤后的消息列表
        """
        filter_keywords = self.config.get("filter_keywords", [])
        filter_regex = self.config.get("filter_regex", "")
        filter_hashtags = self.config.get("filter_hashtags", [])

        if not any([filter_keywords, filter_regex, filter_hashtags]):
            return messages

        filtered_messages = []
        for channel_name, msg in messages:
            msg_text = (msg.text or "").lower()
            msg_entities = self._extract_entities(msg)

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

            # 3. Hashtag 过滤
            if filter_hashtags:
                if any(hashtag in msg_entities for hashtag in filter_hashtags):
                    if logger_func:
                        logger_func(
                            f"[Filter] Filtered by hashtag: {channel_name} - {msg_text[:50]}"
                        )
                    continue

            filtered_messages.append((channel_name, msg))

        return filtered_messages

    def _extract_entities(self, msg: Message) -> List[str]:
        """提取消息中的 hashtag 和 mention 等实体"""
        entities = []
        if msg.entities:
            for entity in msg.entities:
                if hasattr(entity, "hashtag") and entity.hashtag:
                    entities.append(f"#{entity.hashtag.lower()}")
                elif hasattr(entity, "user_id") and entity.user_id:
                    entities.append(f"@user{entity.user_id}")
        return entities
