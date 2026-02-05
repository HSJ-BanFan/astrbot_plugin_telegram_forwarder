from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass

from astrbot.api import logger


@dataclass
class ChannelConfig:
    """频道配置解析结果"""

    channel_name: str
    start_date: Optional[datetime]
    interval: int
    msg_limit: int
    exclude_text_on_media: str
    forward_types: list
    max_file_size: float
    filter_keywords: list
    filter_regex: str

    def __repr__(self) -> str:
        return (
            f"ChannelConfig(name={self.channel_name}, "
            f"date={self.start_date.strftime('%Y-%m-%d') if self.start_date else None}, "
            f"interval={self.interval}s, limit={self.msg_limit}, "
            f"forward_types={self.forward_types})"
        )


class ChannelConfigParser:
    """频道配置解析器 - 解析多种格式的频道配置"""

    # 默认值
    DEFAULT_INTERVAL = 0
    DEFAULT_MSG_LIMIT = 20

    @classmethod
    def parse(cls, config: Any) -> Optional[ChannelConfig]:
        """
        解析频道配置

        Args:
            config: 配置对象，目前仅支持字典格式：
                - dict: {"channel_username": "...", "start_time": "...", "check_interval": ..., "msg_limit": ..., "forward_types": [...], ...}

        Returns:
            ChannelConfig: 解析后的配置对象，如果无效则返回 None
        """
        if isinstance(config, dict):
            return cls._parse_dict(config)
        else:
            logger.warning(
                f"[Config] Unknown config type: {type(config).__name__} - {config}"
            )
            return None

    @classmethod
    def _parse_dict(cls, config: Dict[str, Any]) -> Optional[ChannelConfig]:
        """
        解析字典格式配置
        """
        channel_name = config.get("channel_username", "")
        if not channel_name:
            logger.warning(f"[Config] Missing 'channel_username' in config: {config}")
            return None

        # 解析基本参数
        start_date = None
        s_time = config.get("start_time", "")
        if s_time:
            try:
                start_date = datetime.strptime(s_time, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.error(
                    f"[Config] Invalid date format for {channel_name}: {s_time}"
                )

        return ChannelConfig(
            channel_name=channel_name,
            start_date=start_date,
            interval=config.get("check_interval", cls.DEFAULT_INTERVAL),
            msg_limit=config.get("msg_limit", cls.DEFAULT_MSG_LIMIT),
            exclude_text_on_media=config.get("exclude_text_on_media", "继承全局"),
            forward_types=config.get("forward_types", ["文字", "图片", "视频", "音频", "文件"]),
            max_file_size=config.get("max_file_size", 0.0),
            filter_keywords=config.get("filter_keywords", []),
            filter_regex=config.get("filter_regex", "")
        )
