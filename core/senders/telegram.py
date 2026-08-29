from astrbot.api import AstrBotConfig, logger
from telethon.tl.types import Message

from ..recall import (
    RecallContext,
    RecallRegistry,
    SendReceipt,
    TerminalRecallError,
    classify_recall_error,
    extract_message_ids,
    schedule_recall_receipts,
)


class TelegramSender:
    """
    负责将消息转发到 Telegram 目标频道
    """

    def __init__(
        self,
        client,
        config: AstrBotConfig,
        recall_registry: RecallRegistry | None = None,
    ):
        self.client = client
        self.config = config
        self.recall_registry = (
            recall_registry
            if recall_registry is not None
            else RecallRegistry.from_config(config)
        )

    async def _recall_telegram_receipt(
        self, entity: object, receipt: SendReceipt
    ) -> None:
        try:
            message_id = int(receipt.message_id)
        except (TypeError, ValueError) as exc:
            raise TerminalRecallError("invalid Telegram message ID") from exc
        try:
            await self.client.delete_messages(entity, [message_id], revoke=True)
        except Exception as exc:
            classified = classify_recall_error(exc)
            if classified is not exc:
                raise classified from exc
            raise

    def _schedule_recall(
        self,
        message_ids: list[str],
        entity: object,
        target_session: str,
        source_channel: str,
    ) -> None:
        async def recall(receipt: SendReceipt) -> None:
            await self._recall_telegram_receipt(entity, receipt)

        schedule_recall_receipts(
            self.recall_registry,
            self.config,
            message_ids,
            context=RecallContext(
                platform="telegram",
                target_session=target_session,
                source_channel=source_channel,
                kind="forward",
            ),
            enabled_key="telegram_enabled",
            delay_key="telegram_delay_seconds",
            recall_fn=recall,
        )

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
                if isinstance(target, str) and (
                    target.isdigit()
                    or (target.startswith("-") and target[1:].isdigit())
                ):
                    target = int(target)
                # 获取目标实体
                target_entity = await self.client.get_entity(target)

                # 遍历所有批次进行转发
                for msgs in batches:
                    if not msgs:
                        continue
                    forwarded = await self.client.forward_messages(target_entity, msgs)
                    self._schedule_recall(
                        extract_message_ids(forwarded),
                        target_entity,
                        str(tg_target),
                        src_channel,
                    )
                    logger.debug(
                        f"[TGSender] 已转发批次 ({len(msgs)} 条消息) 从 {src_channel} 到 Telegram 目标频道"
                    )
            except Exception as e:  # noqa: BLE001
                logger.error(f"[TGSender] Telegram 转发错误: {e}")
