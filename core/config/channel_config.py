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

    def __repr__(self) -> str:
        return (
            f"ChannelConfig(name={self.channel_name}, "
            f"date={self.start_date.strftime('%Y-%m-%d') if self.start_date else None}, "
            f"interval={self.interval}s, limit={self.msg_limit})"
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
            config: 配置对象，支持三种格式：
                - dict: 新格式 {"channel_username": "...", "start_time": "...", "check_interval": ..., "msg_limit": ..., "config_preset": "..."}
                - str: 旧格式 "name|date|interval|limit"
                - 其他类型: 返回 None

        Returns:
            ChannelConfig: 解析后的配置对象，如果无效则返回 None
        """
        if isinstance(config, dict):
            return cls._parse_dict(config)
        elif isinstance(config, str):
            return cls._parse_string(config)
        else:
            logger.warning(
                f"[Config] Unknown config type: {type(config).__name__} - {config}"
            )
            return None

    @classmethod
    def _parse_dict(cls, config: Dict[str, Any]) -> Optional[ChannelConfig]:
        """
        解析字典格式配置

        格式：
        {
            "channel_username": "SomeACG",
            "start_time": "2025-01-01",
            "check_interval": 60,
            "msg_limit": 20,
            "config_preset": "2025-01-01|60|5"
        }
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

        interval = config.get("check_interval", cls.DEFAULT_INTERVAL)
        msg_limit = config.get("msg_limit", cls.DEFAULT_MSG_LIMIT)

        # 处理 config_preset（覆盖上述参数）
        preset = config.get("config_preset", "")
        if preset:
            preset_config = cls._parse_preset_string(preset, channel_name)
            if preset_config:
                if preset_config.start_date is not None:
                    start_date = preset_config.start_date
                if preset_config.interval != cls.DEFAULT_INTERVAL:
                    interval = preset_config.interval
                if preset_config.msg_limit != cls.DEFAULT_MSG_LIMIT:
                    msg_limit = preset_config.msg_limit

                logger.info(
                    f"[Config] Applied preset '{preset}' for {channel_name}: "
                    f"date={start_date}, interval={interval}s, limit={msg_limit}"
                )

        return ChannelConfig(
            channel_name=channel_name,
            start_date=start_date,
            interval=interval,
            msg_limit=msg_limit,
        )

    @classmethod
    def _parse_string(cls, config: str) -> Optional[ChannelConfig]:
        """
        解析字符串格式配置（旧格式）

        格式：name|date|interval|limit
        示例：xiaoshuwu|2025-01-01|60|5
        """
        parts = [p.strip() for p in config.split("|")]
        if not parts:
            logger.warning(f"[Config] Invalid string config: {config}")
            return None

        channel_name = parts[0]
        if not channel_name:
            logger.warning(f"[Config] Missing channel name in string config: {config}")
            return None

        # 解析剩余部分
        start_date = None
        ints_found = []

        for part in parts[1:]:
            if not part:
                continue

            # 尝试解析为日期
            if "-" in part and not start_date:
                try:
                    start_date = datetime.strptime(part, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    continue
                except ValueError:
                    pass

            # 尝试解析为数字（interval 或 msg_limit）
            if part.isdigit():
                ints_found.append(int(part))

        # 分配数字参数
        interval = cls.DEFAULT_INTERVAL
        msg_limit = cls.DEFAULT_MSG_LIMIT

        if len(ints_found) >= 1:
            interval = ints_found[0]
        if len(ints_found) >= 2:
            msg_limit = ints_found[1]

        return ChannelConfig(
            channel_name=channel_name,
            start_date=start_date,
            interval=interval,
            msg_limit=msg_limit,
        )

    @classmethod
    def _parse_preset_string(
        cls, preset: str, channel_name: str
    ) -> Optional[ChannelConfig]:
        """
        解析预设字符串

        格式：date|interval|limit
        示例：2025-01-01|60|5
        """
        try:
            p_parts = [p.strip() for p in preset.split("|")]

            start_date = None
            interval = cls.DEFAULT_INTERVAL
            msg_limit = cls.DEFAULT_MSG_LIMIT

            if len(p_parts) >= 1 and p_parts[0]:
                try:
                    start_date = datetime.strptime(p_parts[0], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    logger.warning(
                        f"[Config] Invalid date in preset '{preset}' for {channel_name}"
                    )

            if len(p_parts) >= 2 and p_parts[1].isdigit():
                interval = int(p_parts[1])

            if len(p_parts) >= 3 and p_parts[2].isdigit():
                msg_limit = int(p_parts[2])

            return ChannelConfig(
                channel_name=channel_name,  # 预设不覆盖频道名
                start_date=start_date,
                interval=interval,
                msg_limit=msg_limit,
            )
        except Exception as e:
            logger.error(
                f"[Config] Error parsing preset '{preset}' for {channel_name}: {e}"
            )
            return None
