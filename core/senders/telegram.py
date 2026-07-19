from collections.abc import Iterable

from telethon.tl.types import Message

from astrbot.api import AstrBotConfig, logger

from .auto_recall import AutoRecallManager, extract_message_ids


class TelegramSender:
    """
    负责将消息转发到 Telegram 目标频道
    """

    def __init__(
        self,
        client,
        config: AstrBotConfig,
        auto_recall: AutoRecallManager | None = None,
    ):
        self.client = client
        self.config = config
        self.auto_recall = auto_recall

    @staticmethod
    def _collect_message_ids(result: object) -> list[int]:
        ids = extract_message_ids(result)
        if ids:
            return ids

        candidates: list[object]
        if result is None:
            return []
        if isinstance(result, Message):
            candidates = [result]
        elif isinstance(result, Iterable) and not isinstance(
            result, (str, bytes, bytearray)
        ):
            candidates = list(result)
        else:
            candidates = [result]

        collected: list[int] = []
        for item in candidates:
            message_id = getattr(item, "id", None)
            if message_id is None:
                continue
            try:
                value = int(message_id)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in collected:
                collected.append(value)
        return collected

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
        _ = effective_cfg
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
                    result = await self.client.forward_messages(target_entity, msgs)
                    logger.debug(
                        f"[TGSender] 已转发批次 ({len(msgs)} 条消息) 从 {src_channel} 到 Telegram 目标频道"
                    )
                    if self.auto_recall is not None and self.auto_recall.is_enabled():
                        message_ids = self._collect_message_ids(result)
                        if message_ids:
                            self.auto_recall.schedule(
                                platform="telegram",
                                message_ids=message_ids,
                                target=str(tg_target),
                                source_channel=str(src_channel or ""),
                                note="tg_forward",
                            )
            except Exception as e:
                logger.error(f"[TGSender] Telegram 转发错误: {e}")
