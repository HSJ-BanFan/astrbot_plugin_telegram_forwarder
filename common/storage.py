"""
数据持久化存储模块

提供 JSON 格式的数据持久化功能，用于保存每个频道的最后处理消息ID。
这样在插件重启后可以从上次的位置继续处理，避免重复转发消息。

数据结构：
{
    "channels": {
        "channel_name": {
            "last_post_id": 12345
        }
    }
}
"""

import json
import os
from astrbot.api import logger


class Storage:
    """
    数据持久化管理类

    负责加载、保存和查询频道的消息ID状态信息。
    """

    def __init__(self, data_file: str):
        """
        初始化存储管理器

        Args:
            data_file: 数据文件路径，通常为 data.json

        行为：
            - 自动从文件加载已有数据
            - 如果文件不存在或损坏，使用默认数据
        """
        self.data_file = data_file
        self.persistence = self._load()

    def _load(self) -> dict:
        """
        从文件加载持久化数据

        Returns:
            dict: 加载的数据字典，如果失败则返回默认空结构

        异常处理：
            - 文件不存在：返回默认数据
            - JSON 解析错误：记录警告并返回默认数据
            - IO 错误：记录警告并返回默认数据

        默认结构：
            {"channels": {}}
        """
        default_data = {"channels": {}}

        # 检查文件是否存在
        if os.path.exists(self.data_file):
            try:
                # 尝试读取并解析 JSON 文件
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                # 捕获特定异常类型，避免隐藏其他错误
                logger.warning(f"Failed to load data file: {e}, using defaults")
                return default_data

        # 文件不存在时返回默认数据
        return default_data

    def save(self):
        """
        保存当前数据到文件

        异常处理：
            - 捕获 IO 错误并记录日志，避免程序崩溃

        Note:
            每次更新频道状态后都应调用此方法确保持久化
        """
        try:
            # 使用缩进格式化 JSON，方便人类阅读和调试
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.persistence, f, indent=2)
        except IOError as e:
            # 保存失败时记录错误日志，但不中断程序
            logger.error(f"Failed to save data: {e}")

    def get_channel_data(self, channel_name: str) -> dict:
        """
        获取频道的持久化数据

        Args:
            channel_name: 频道名称或ID

        Returns:
            dict: 包含该频道数据的字典，至少包含 {"last_post_id": 0}

        行为：
            - 如果频道不存在，自动创建并初始化
            - 返回的是字典引用，修改会直接影响内存中的数据
        """
        # 如果频道不存在于存储中，初始化为空状态
        if channel_name not in self.persistence["channels"]:
            self.persistence["channels"][channel_name] = {"last_post_id": 0}
        return self.persistence["channels"][channel_name]

    def update_last_id(self, channel_name: str, last_id: int):
        """
        更新频道的最后处理消息ID

        Args:
            channel_name: 频道名称或ID
            last_id: 最后处理的消息ID

        行为：
            - 立即保存到文件，确保持久化
            - 如果频道不存在，自动创建
        """
        # 确保频道存在
        if channel_name not in self.persistence["channels"]:
            self.persistence["channels"][channel_name] = {}

        # 更新最后消息ID
        self.persistence["channels"][channel_name]["last_post_id"] = last_id

        # 立即保存到文件
        self.save()
