"""QQ 转发场景下的回复预览辅助函数。

用于把 Telegram 的回复上下文压缩成适合 QQ 展示的简短预览文本，
避免在发送主流程中混入过多“回复引用”相关的细节处理。

QQ / OneBot 11 原生没有 Telegram 那种跨消息引用气泡，因此这里统一
把被回复内容折叠为内联文本前缀（例如 `↩ 回复 Alice:\\n...`）拼进本条消息。
"""

from telethon.tl.types import Message

from astrbot.api import logger

from ...common.text_tools import clean_telegram_text, to_telethon_entity

# 抓取失败 / 原消息已删除时的明确降级文案，保证回复类消息仍带引用语义。
REPLY_UNAVAILABLE_PREVIEW = "↩ 回复:\n[原消息不可用]"
REPLY_PREVIEW_MAX_LEN = 100


def get_sender_display_name(msg: Message) -> str:
    post_author = getattr(msg, "post_author", None)
    if post_author:
        return str(post_author)
    sender = getattr(msg, "sender", None)
    if not sender:
        return ""
    for attr in ("first_name", "title", "username"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)
    return ""


def reply_media_label(msg: Message) -> str:
    if getattr(msg, "photo", None):
        return "[图片]"
    if getattr(msg, "video", None):
        return "[视频]"
    if getattr(msg, "audio", None) or getattr(msg, "voice", None):
        return "[音频]"
    if getattr(msg, "file", None) or getattr(msg, "document", None):
        return "[文件]"
    return "[消息]"


def _truncate_preview(preview: str) -> str:
    preview = " ".join(part for part in preview.splitlines() if part).strip()
    if len(preview) > REPLY_PREVIEW_MAX_LEN:
        return preview[:REPLY_PREVIEW_MAX_LEN].rstrip() + "..."
    return preview


def build_reply_preview(reply_msg: Message, strip_links: bool = False) -> str:
    """由完整 Telethon Message 构造 QQ 侧内联引用预览。"""
    sender_name = get_sender_display_name(reply_msg)
    if getattr(reply_msg, "text", None):
        preview = clean_telegram_text(reply_msg.text, strip_links=strip_links)
        preview = _truncate_preview(preview)
    else:
        preview = reply_media_label(reply_msg)
    if not preview:
        preview = reply_media_label(reply_msg)
    if sender_name:
        return f"↩ 回复 {sender_name}:\n{preview}"
    return f"↩ 回复:\n{preview}"


def build_reply_preview_from_quote(quote_text: str, strip_links: bool = False) -> str:
    """用 MessageReplyHeader.quote_text 构造降级预览（无需再抓原消息）。"""
    preview = clean_telegram_text(quote_text or "", strip_links=strip_links)
    preview = _truncate_preview(preview)
    if not preview:
        return REPLY_UNAVAILABLE_PREVIEW
    return f"↩ 回复:\n{preview}"


def _collect_reply_targets(
    msgs: list[Message],
) -> tuple[dict[int, Message], list[tuple[int, object]]]:
    """收集本批消息索引，以及 (reply_id, reply_header) 列表（按出现顺序去重）。"""
    by_id: dict[int, Message] = {}
    for msg in msgs:
        msg_id = getattr(msg, "id", None)
        if msg_id is not None:
            by_id[msg_id] = msg

    targets: list[tuple[int, object]] = []
    seen: set[int] = set()
    for msg in msgs:
        reply_header = getattr(msg, "reply_to", None)
        reply_id = getattr(reply_header, "reply_to_msg_id", None)
        if not reply_id or reply_id in seen:
            continue
        seen.add(reply_id)
        targets.append((reply_id, reply_header))
    return by_id, targets


async def prefetch_reply_previews(
    *,
    msgs: list[Message],
    src_channel: str,
    client,
    strip_links: bool = False,
) -> dict[int, str]:
    """为批次内所有回复消息预构造引用预览。

    解析顺序（由强到弱）：
    1. 本批次已有的被回复消息（同批回复不再跳过，直接本地构造）
    2. 通过 Telethon `get_messages` 拉取历史被回复消息
    3. `MessageReplyHeader.quote_text`（部分客户端会带片段）
    4. 明确降级文案 `[原消息不可用]` + 日志
    """
    by_id, targets = _collect_reply_targets(msgs)
    if not targets:
        return {}

    preview_cache: dict[int, str] = {}
    missing_ids: list[int] = []

    for reply_id, _header in targets:
        local_msg = by_id.get(reply_id)
        if local_msg is not None:
            preview_cache[reply_id] = build_reply_preview(
                local_msg, strip_links=strip_links
            )
        else:
            missing_ids.append(reply_id)

    if missing_ids and client is not None:
        try:
            reply_msgs = await client.get_messages(
                to_telethon_entity(src_channel), ids=missing_ids
            )
        except Exception as e:
            logger.warning(f"[QQSender] 获取 reply 预览失败: {e}")
            reply_msgs = None

        if reply_msgs:
            if not isinstance(reply_msgs, list):
                reply_msgs = [reply_msgs]
            for reply_msg in reply_msgs:
                if not reply_msg or getattr(reply_msg, "id", None) is None:
                    continue
                preview_cache[reply_msg.id] = build_reply_preview(
                    reply_msg, strip_links=strip_links
                )
    elif missing_ids and client is None:
        logger.warning(
            f"[QQSender] 无法获取 reply 预览: client 不可用, missing={missing_ids}"
        )

    # 仍缺失的 reply_id：quote_text 降级，或明确“不可用”占位
    for reply_id, reply_header in targets:
        if reply_id in preview_cache:
            continue
        quote_text = getattr(reply_header, "quote_text", None) if reply_header else None
        if quote_text:
            preview_cache[reply_id] = build_reply_preview_from_quote(
                quote_text, strip_links=strip_links
            )
            logger.info(f"[QQSender] reply 预览降级为 quote_text: reply_to={reply_id}")
        else:
            preview_cache[reply_id] = REPLY_UNAVAILABLE_PREVIEW
            logger.info(
                f"[QQSender] reply 预览降级为占位: reply_to={reply_id} "
                f"(原消息不可用或抓取失败)"
            )

    return preview_cache
