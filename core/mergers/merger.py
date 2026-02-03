from typing import List, Tuple, Dict, Optional
from telethon.tl.types import Message

from astrbot.api import logger
from .base import MergeRule
from .someacg import SomeACGPreviewPlusOriginal


class MessageMerger:
    """消息合并引擎 - 管理和执行消息合并规则"""

    # 可用的规则类注册表
    RULE_CLASSES = {
        "SomeACGPreviewPlusOriginal": SomeACGPreviewPlusOriginal,
    }

    def __init__(self, config: dict):
        """
        初始化合并引擎

        Args:
            config: 配置字典，包含 merge_rules 配置
        """
        self.config = config
        self.merge_rules = self._load_merge_rules()
        logger.info(
            f"[Merge] Initialized with {len(self.merge_rules)} rules: {list(self.merge_rules.keys())}"
        )

    def _load_merge_rules(self) -> Dict[str, MergeRule]:
        """
        从配置加载合并规则

        配置格式：
        {
            "SomeACG": {
                "rule_class": "SomeACGPreviewPlusOriginal",
                "params": {
                    "time_window_seconds": 10,
                    ...
                }
            }
        }

        Returns:
            频道名称 -> MergeRule 实例的映射
        """
        rules_config = self.config.get("merge_rules", [])
        if not rules_config:
            return {}

        merge_rules = {}

        for rule_config in rules_config:
            channel = rule_config.get("channel")
            rule_class_name = rule_config.get("rule_class", "")
            params = rule_config.get("params", {})

            if not channel or not rule_class_name:
                logger.warning(f"[Merge] Invalid merge rule config: {rule_config}")
                continue

            if rule_class_name not in self.RULE_CLASSES:
                logger.warning(f"[Merge] Unknown rule class: {rule_class_name}")
                continue

            rule_class = self.RULE_CLASSES[rule_class_name]
            merge_rules[channel] = rule_class(params)

            logger.info(
                f"[Merge] Loaded rule '{rule_class_name}' for channel '{channel}'"
            )

        return merge_rules

    def merge_messages(
        self, messages: List[Tuple[str, Message]]
    ) -> List[Tuple[str, Message]]:
        """
        对消息应用合并规则

        Args:
            messages: (channel_name, message) 元组列表

        Returns:
            合并后的消息列表（可能添加了 _merge_group_id 标记）
        """
        if not self.merge_rules:
            logger.info("[Merge] No merge rules configured")
            return messages

        logger.debug(
            f"[Merge] Processing {len(messages)} messages, rules: {list(self.merge_rules.keys())}"
        )

        merged_messages = []
        used_indices = set()

        for i, msg1 in enumerate(messages):
            if i in used_indices:
                continue

            channel_name, message1 = msg1

            # 检查此频道是否配置了合并规则
            rule = self.merge_rules.get(channel_name)
            if not rule:
                merged_messages.append(msg1)
                used_indices.add(i)
                continue

            logger.debug(
                f"[Merge] Found rule for channel '{channel_name}', checking message {i}"
            )

            # 尝试查找可合并的消息
            group_result = self._find_group(
                i, messages, channel_name, rule, used_indices
            )
            group_msgs = group_result["messages"]
            group_indices_list = group_result["indices"]

            if len(group_msgs) > 1:
                # 找到可合并的组，应用合并标记
                group_key = rule.get_group_key(group_msgs[0])
                if group_key:
                    rule.apply_merge_marker(group_msgs, group_key)
                    logger.debug(
                        f"[Merge] Merged group for channel '{channel_name}' with {len(group_msgs)} messages, key={group_key}"
                    )

                merged_messages.extend(group_msgs)
                for idx in group_indices_list:
                    used_indices.add(idx)
            else:
                # 没有找到可合并的消息，保持原样
                merged_messages.append(msg1)
                used_indices.add(i)

        logger.info(
            f"[Merge] Before: {len(messages)} messages, After: {len(merged_messages)} messages"
        )

        return merged_messages

    def _find_group(
        self,
        start_index: int,
        messages: List[Tuple[str, Message]],
        channel_name: str,
        rule: MergeRule,
        used_indices: set,
    ) -> dict:
        """
        查找与起始消息可合并的所有消息

        Args:
            start_index: 起始消息索引
            messages: 所有消息列表
            channel_name: 频道名称
            rule: 合并规则实例
            used_indices: 已使用的索引集合

        Returns:
            dict: {"messages": List[Tuple[str, Message]], "indices": List[int]}
        """
        start_msg = messages[start_index]
        group_messages = [start_msg]
        group_indices = [start_index]

        # 向前搜索可合并的消息
        for i in range(start_index + 1, len(messages)):
            if i in used_indices:
                continue

            candidate_msg = messages[i]
            candidate_channel, _ = candidate_msg

            # 只合并同一频道的消息
            if candidate_channel != channel_name:
                continue

            # 检查是否可以合并
            if rule.can_merge(channel_name, start_msg, candidate_msg):
                group_messages.append(candidate_msg)
                group_indices.append(i)

        # 向后搜索可合并的消息（处理预览图在原图之后的情况）
        for i in range(start_index - 1, -1, -1):
            if i in used_indices:
                continue

            candidate_msg = messages[i]
            candidate_channel, _ = candidate_msg

            # 只合并同一频道的消息
            if candidate_channel != channel_name:
                continue

            # 检查是否可以合并（注意：candidate_msg 是预览图，start_msg 是原图）
            if rule.can_merge(channel_name, candidate_msg, start_msg):
                group_messages.insert(0, candidate_msg)  # 插入到最前面
                group_indices.insert(0, i)

        return {"messages": group_messages, "indices": group_indices}
