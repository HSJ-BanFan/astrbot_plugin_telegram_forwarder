from telethon.tl.types import Message

from astrbot.api import AstrBotConfig, logger


class TelegramSender:
    """
    负责将消息转发到 Telegram 目标频道
    """

    def __init__(self, client, config: AstrBotConfig):
        self.client = client
        self.config = config

    async def send(
        self,
        batches: list[list[Message]],
        src_channel: str,
        effective_cfg: dict | None = None,
    ):
        """
        转发消息到 Telegram 目标频道

        Args:
            batches: 消息批次列表 (List[List[Message]])
            src_channel: 源频道名称（用于日志）
            effective_cfg: 合并后的配置项
        """
        tg_target = self.config.get("target_channel")

        if not batches:
            return

        # 只要配置了目标频道，就启用 TG 转发
        if tg_target:
            try:
                # ========== 解析目标频道 ==========
                target = tg_target
                if isinstance(target, str):
                    if target.startswith("-") or target.isdigit():
                        try:
                            target = int(target)
                        except ValueError:
                            pass

                # 获取目标实体
                target_entity = await self.client.get_entity(target)

                # 遍历所有批次进行转发
                for msgs in batches:
                    if not msgs:
                        continue
                    await self.client.forward_messages(target_entity, msgs)
                    logger.debug(
                        f"[TGSender] 已转发批次 ({len(msgs)} 条消息) 从 {src_channel} 到 Telegram 目标频道"
                    )
            except Exception as e:
                logger.error(f"[TGSender] Telegram 转发错误: {e}")
