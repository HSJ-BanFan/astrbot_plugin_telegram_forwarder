import re

from telethon.tl.types import Message, MessageMediaDocument, MessageMediaPhoto

from astrbot.api import logger

from .base import MergeRule


class SomeACGPreviewPlusOriginal(MergeRule):
    """SomeACG 频道专用合并规则：预览图说明 + 原图"""

    def can_merge(
        self, channel_name: str, msg1: tuple[str, Message], msg2: tuple[str, Message]
    ) -> bool:
        """
        判断是否为 SomeACG 的预览图+原图模式

        检查条件：
        1. msg1 是预览图说明（photo + 文本 + 包含 pixiv.net）
        2. msg2 是原图文件（document + 无文本 + 文件名匹配 pixiv ID）
        3. 时间差在配置的窗口内（默认 10 秒）
        """
        if channel_name != "SomeACG":
            return False

        _, message1 = msg1
        _, message2 = msg2

        # 检查 msg1 是否是预览图说明
        if not self._is_preview_message(message1):
            logger.debug(
                f"[SomeACG] 消息 1 不是预览图 (ID={message1.id}): "
                f"有媒体={bool(message1.media)}, "
                f"是照片={isinstance(message1.media, MessageMediaPhoto) if message1.media else False}, "
                f"有文本={bool(message1.text)}, "
                f"文本预览={message1.text[:100] if message1.text else '无'}"
            )
            return False

        # 检查 msg2 是否是原图
        if not self._is_original_message(message2):
            logger.debug(
                f"[SomeACG] 消息 2 不是原图 (ID={message2.id}): "
                f"有媒体={bool(message2.media)}, "
                f"有文本={bool(message2.text)}"
            )
            return False

        # 检查 pixiv ID 是否匹配（仅当原图是 Document 类型时）
        pixiv_id1 = self._extract_pixiv_id(message1.text)
        if not pixiv_id1:
            logger.debug(
                f"[SomeACG] 无法从消息 1 (ID={message1.id}) 提取 Pixiv ID, 文本: {message1.text[:200]}"
            )
            return False

        # 检查原图类型
        is_audio_original = False
        is_document_original = isinstance(message2.media, MessageMediaDocument)

        if not is_document_original:
            logger.debug(
                f"[SomeACG] 未知的原图媒体类型: {type(message2.media).__name__}"
            )
            return False

        # 检查是否是音频（通过 mime_type）
        if hasattr(message2.media, "document"):
            mime_type = getattr(message2.media.document, "mime_type", "")
            if mime_type.startswith("audio/") or mime_type == "application/ogg":
                is_audio_original = True

        if is_audio_original:
            # 音频类型：SomeACG 的原图通常是音频，不需要文件名匹配
            logger.debug(
                f"[SomeACG] 音频原图 (消息 ID={message2.id}), 跳过文件名匹配。"
            )
        else:
            # 文档类型：检查文件名匹配 pixiv ID
            pixiv_id2 = self._extract_pixiv_id_from_filename(message2)
            logger.debug(
                f"[SomeACG] 文档原图, 消息1 PixivID={pixiv_id1}, 消息2 PixivID={pixiv_id2}"
            )

            if not self._file_name_contains_pixiv_id(message2, pixiv_id1):
                logger.debug(
                    f"[SomeACG] Pixiv ID 不匹配: 消息1={pixiv_id1}, 消息2={pixiv_id2}"
                )
                return False

        # 检查时间差
        if message1.date is None or message2.date is None:
            logger.debug(
                f"[SomeACG] 消息时间戳缺失: msg1.date={message1.date}, msg2.date={message2.date}"
            )
            return False

        time_window = self.config.get("time_window_seconds", 10)
        time_diff = (message2.date - message1.date).total_seconds()

        if time_diff < 0 or time_diff > time_window:
            logger.debug(f"[SomeACG] 超出时间窗口: {time_diff}s > {time_window}s")
            return False

        original_type = "Audio" if is_audio_original else "Document"
        logger.info(
            f"[SomeACG] Can merge: msg{message1.id} (preview) + msg{message2.id} ({original_type}), pixiv_id={pixiv_id1}, time_diff={time_diff}s"
        )

        return True

    def get_group_key(self, msg: tuple[str, Message]) -> str | None:
        """
        获取分组 key

        对于预览图：提取 pixiv ID 作为 key
        对于原图：检查文件名中的 pixiv ID
        其他消息：返回 None
        """
        channel_name, message = msg

        if channel_name != "SomeACG":
            return None

        # 检查是否是预览图
        if self._is_preview_message(message):
            pixiv_id = self._extract_pixiv_id(message.text)
            if pixiv_id:
                return f"SomeACG_{pixiv_id}"

        # 检查是否是原图
        if self._is_original_message(message):
            pixiv_id = self._extract_pixiv_id_from_filename(message)
            if pixiv_id:
                return f"SomeACG_{pixiv_id}"

        return None

    def apply_merge_marker(
        self, messages: list[tuple[str, Message]], group_key: str
    ) -> None:
        """
        为一组关联消息添加合并标记

        设置 _merge_group_id 属性，用于后续相册分组逻辑识别
        """
        # 从 group_key 提取 hash 值作为 grouped_id
        group_hash = hash(group_key)

        for _, msg in messages:
            setattr(msg, "_merge_group_id", group_hash)

        logger.info(
            f"[SomeACG] Merged {len(messages)} messages with key={group_key}, grouped_id={group_hash}"
        )

    # ========== 私有辅助方法 ==========

    def _is_preview_message(self, msg: Message) -> bool:
        """判断是否是预览图说明消息"""
        if not msg.media:
            return False

        if not isinstance(msg.media, MessageMediaPhoto):
            return False

        if not msg.text or not msg.text.strip():
            return False

        if "pixiv.net" not in msg.text:
            return False

        return True

    def _is_original_message(self, msg: Message) -> bool:
        """判断是否是原图文件消息（支持 Document 和 Audio）"""
        if not msg.media:
            return False

        # 原图通常是 Document 类型
        if not isinstance(msg.media, MessageMediaDocument):
            return False

        # 文本为空或无文本
        if msg.text and msg.text.strip():
            return False

        # 检查是否是音频（通过 mime_type）
        is_audio = False
        if hasattr(msg.media, "document"):
            mime_type = getattr(msg.media.document, "mime_type", "")
            if mime_type.startswith("audio/") or mime_type == "application/ogg":
                is_audio = True

        if is_audio:
            # 音频类型：SomeACG 的原图通常是音频，不需要文件名匹配
            pass
        else:
            # 文档类型：检查文件名是否包含 pixiv ID
            pixiv_id = self._extract_pixiv_id_from_filename(msg)
            if not pixiv_id:
                return False

        return True

    def _extract_pixiv_id(self, text: str) -> str | None:
        """从文本中提取 pixiv ID"""
        if not text:
            return None

        match = re.search(r"pixiv\.net/artworks/(\d+)", text)
        if match:
            return match.group(1)

        return None

    def _extract_pixiv_id_from_filename(self, msg: Message) -> str | None:
        """从文件名中提取 pixiv ID"""
        if not msg.media or not isinstance(msg.media, MessageMediaDocument):
            return None

        file_name = None
        for attr in msg.media.document.attributes:
            if hasattr(attr, "file_name"):
                file_name = attr.file_name
                break

        if not file_name:
            return None

        # 匹配模式: {pixiv_id}_p0.jpg 或 {pixiv_id}_p0.png 等
        match = re.search(r"(\d+)_p0\.", file_name)
        if match:
            return match.group(1)

        return None

    def _file_name_contains_pixiv_id(self, msg: Message, pixiv_id: str) -> bool:
        """检查文件名是否包含指定的 pixiv ID"""
        file_pixiv_id = self._extract_pixiv_id_from_filename(msg)
        return file_pixiv_id == pixiv_id
