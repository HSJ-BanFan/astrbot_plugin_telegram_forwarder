from abc import ABC, abstractmethod
from typing import Optional, Tuple, List
from telethon.tl.types import Message


class MergeRule(ABC):
    """消息合并规则基类"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def can_merge(
        self, channel_name: str, msg1: Tuple[str, Message], msg2: Tuple[str, Message]
    ) -> bool:
        """
        判断两条消息是否可以合并

        Args:
            channel_name: 频道名称
            msg1: 第一条消息 (channel_name, message)
            msg2: 第二条消息 (channel_name, message)

        Returns:
            True 如果可以合并，False 否则
        """
        pass

    @abstractmethod
    def get_group_key(self, msg: Tuple[str, Message]) -> Optional[str]:
        """
        获取消息的分组 key，用于识别属于同一组的消息

        Args:
            msg: 消息元组 (channel_name, message)

        Returns:
            分组 key，如果不属于任何组则返回 None
        """
        pass

    @abstractmethod
    def apply_merge_marker(
        self, messages: List[Tuple[str, Message]], group_key: str
    ) -> None:
        """
        为一组关联消息添加合并标记（_merge_group_id）

        Args:
            messages: 属于同一组的消息列表
            group_key: 分组 key
        """
        pass
