from typing import List
from telethon.tl.types import Message
from astrbot.api import logger, AstrBotConfig


class TelegramSender:
    """
    负责将消息转发到 Telegram 目标频道
    """

    def __init__(self, client, config: AstrBotConfig):
        self.client = client
        self.config = config

    async def send(self, batches: List[List[Message]], src_channel: str):
        """
        转发消息到 Telegram 目标频道

        Args:
            batches: 消息批次列表 (List[List[Message]])
            src_channel: 源频道名称（用于日志）
        """
        tg_target = self.config.get("target_channel")
        bot_token = self.config.get("bot_token")
        enable_tg = self.config.get("enable_forward_to_tg", False)

        if not batches:
            return

        # 只有配置了目标频道和 bot_token 且启用开关时才转发
        if tg_target and bot_token and enable_tg:
            try:
                # ========== 解析目标频道 ==========
                target = tg_target
                if isinstance(target, str):
                    if target.startswith("-") or target.isdigit():
                        try:
                            target = int(target)
                        except:
                            pass
                
                # 获取目标实体
                target_entity = await self.client.get_entity(target)

                # 遍历所有批次进行转发
                for msgs in batches:
                    if not msgs:
                        continue
                    await self.client.forward_messages(target_entity, msgs)
                    logger.info(f"Forwarded batch ({len(msgs)} msgs) from {src_channel} to TG")
            except Exception as e:
                logger.error(f"TG Forward Error: {e}")
