"""QQ 发送前的批次预处理辅助函数。

该模块负责把 Telegram 批次消息整理成 QQ 可直接发送的节点结构，
包括：前缀头部、回复预览、文本清洗、媒体下载后的组件拼装，以及批次级失败记录。
这样发送器后续只需要关心“怎么发”，而不必重复处理“怎么组装”。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

from telethon.tl.types import Message

from astrbot.api import logger
from astrbot.api.message_components import (
    BaseMessageComponent,
    File,
    Plain,
    Record,
    Video,
)

from ...common.text_tools import clean_telegram_text, is_numeric_channel_id

if TYPE_CHECKING:
    from .qq import QQSender


class ProcessedBatchData(TypedDict):
    """分发阶段可直接消费的批次数据结构。

    这是 `QQSender` 各子模块之间共享的稳定中间表示：
    - `batch_index`：逻辑批次索引
    - `nodes_data`：已整理好的 QQ 节点组件列表
    - `local_files`：该批次下载产生的本地临时文件
    - `contains_audio`：是否包含需要特殊发送顺序处理的音频
    """

    batch_index: int
    nodes_data: list[list[BaseMessageComponent]]
    local_files: list[str]
    contains_audio: bool


@dataclass(frozen=True)
class ProcessedBatch:
    """单个 Telegram 批次转换后的 QQ 节点结果。

    这是 `ProcessedBatchData` 的 dataclass 形式，便于在预处理阶段先以更清晰的属性访问方式组织数据，
    最终再转换成与旧分发代码兼容的字典结构。
    """

    batch_index: int
    nodes_data: list[list[BaseMessageComponent]]
    local_files: list[str]
    contains_audio: bool

    def as_batch_data(self) -> ProcessedBatchData:
        """转换为现有 QQSender 分发流程继续使用的字典结构。"""
        return {
            "batch_index": self.batch_index,
            "nodes_data": self.nodes_data,
            "local_files": self.local_files,
            "contains_audio": self.contains_audio,
        }


@dataclass(frozen=True)
class BuildBatchesResult:
    """批次预处理阶段的返回结果。

    除了成功构造出的批次外，这里还会携带预处理阶段就已经确认的失败项，
    这样分发阶段无需再次区分“构造失败”与“发送失败”。
    """

    processed_batches: list[ProcessedBatchData]
    target_failures: dict[int, str] = field(default_factory=dict)


def message_expects_downloadable_media(msg: Message) -> bool:
    """消息是否携带应下载的图片/视频/音频/文件媒体。

    下载失败时不应退化为只发 caption；贴纸等 QQ 无法展示的类型不在此列。
    """
    if not getattr(msg, "media", None):
        return False
    if getattr(msg, "sticker", None):
        return False
    skip_attr_names = {
        "DocumentAttributeAnimated",
        "DocumentAttributeCustomEmoji",
    }
    document = getattr(getattr(msg, "media", None), "document", None)
    if document is not None and any(
        getattr(attr, "type", None) == "animated"
        or type(attr).__name__ in skip_attr_names
        for attr in getattr(document, "attributes", []) or []
    ):
        return False
    return bool(
        getattr(msg, "photo", None)
        or getattr(msg, "video", None)
        or getattr(msg, "audio", None)
        or getattr(msg, "voice", None)
        or getattr(msg, "document", None)
        or getattr(msg, "file", None)
    )


async def build_processed_batches(
    *,
    sender: "QQSender",
    real_batches: list[list[Message]],
    src_channel: str,
    display_name: str | None,
    involved_channels: list[str] | None,
    strip_links: bool,
    exclude_text_on_media: bool,
) -> BuildBatchesResult:
    """把 Telegram 逻辑批次转换为 QQ 可分发的批次数据。

    这里的职责是把“消息内容组织”一次性做完：
    文本清洗、引用预览、头部拼接、媒体下载与组件转换都发生在这里。
    经过该函数后，分发器只需要按既定策略发送，不再关心单条消息如何组装。
    """
    if involved_channels and len(involved_channels) > 1:
        formatted = [
            ch if is_numeric_channel_id(ch) else f"@{ch.lstrip('@')}"
            for ch in sorted(involved_channels)
        ]
        if len(formatted) <= 4:
            channels_str = " ".join(formatted)
        else:
            channels_str = " ".join(formatted[:4]) + f" 等{len(formatted) - 4}个频道"
        header = f"From {channels_str}:"
        logger.debug(f"[QQSender] 混合大合并 From: {header}")
    else:
        header_name = display_name or src_channel
        if not header_name.startswith("@") and not is_numeric_channel_id(
            header_name.lstrip("@")
        ):
            header_name = f"@{header_name}"
        header = f"From {header_name}:"

    processed_batches = []
    target_failures: dict[int, str] = {}
    header_added = False

    for batch_index, msgs in enumerate(real_batches):
        all_local_files = []
        all_nodes_data = []
        media_download_failed = False
        batch_header_added = False
        try:
            reply_preview_cache = await sender._prefetch_reply_previews(
                msgs, src_channel, strip_links=strip_links
            )
            for i, msg in enumerate(msgs):
                current_node_components = []
                text_parts = []
                if msg.text:
                    cleaned = clean_telegram_text(msg.text, strip_links=strip_links)
                    if cleaned:
                        text_parts.append(cleaned)

                media_components = []
                has_any_attachment = False
                msg_max_size = getattr(msg, "_max_file_size", 0)
                expects_media = message_expects_downloadable_media(msg)
                files = await sender.downloader.download_media(
                    msg, max_size_mb=msg_max_size
                )
                if expects_media and not files:
                    media_download_failed = True
                    logger.warning(
                        f"[QQSender] 消息 {getattr(msg, 'id', '?')} 媒体下载失败，"
                        "跳过整条（不发送纯文字 caption）"
                    )
                    continue
                for fpath in files:
                    all_local_files.append(fpath)
                    has_any_attachment = True
                    media_components.extend(sender._dispatch_media_file(fpath))

                should_exclude_text = exclude_text_on_media and has_any_attachment

                reply_header = getattr(msg, "reply_to", None)
                reply_id = getattr(reply_header, "reply_to_msg_id", None)
                reply_preview = None
                if reply_id is not None:
                    reply_preview = reply_preview_cache.get(reply_id)
                if reply_preview and not should_exclude_text:
                    text_parts.insert(0, reply_preview)

                # 头部只在“第一条真正发出的消息”前加一次。
                # 不能用原始 i==0：首条若因媒体下载失败被跳过，后续成功消息仍需 From 头。
                add_header_this_time = False
                if not should_exclude_text:
                    if involved_channels and len(involved_channels) > 1:
                        if not header_added:
                            add_header_this_time = True
                            header_added = True
                            batch_header_added = True
                    elif not batch_header_added:
                        add_header_this_time = True
                        batch_header_added = True

                if add_header_this_time:
                    if text_parts:
                        text_parts[0] = f"{header}\n​{text_parts[0]}"
                    else:
                        current_node_components.append(Plain(f"{header}\n​"))

                if not should_exclude_text:
                    for t in text_parts:
                        current_node_components.append(Plain(t + "\n"))

                # 部分特殊媒体（视频、语音、文件）在 QQ 侧和纯文本混发时表现不稳定，
                # 因此这里会把“文本节点”和“特殊媒体节点”拆开，降低发送失败概率。
                _node_special_media = [
                    c for c in media_components if isinstance(c, (Video, Record, File))
                ]
                if not should_exclude_text and text_parts and _node_special_media:
                    if current_node_components:
                        all_nodes_data.append(current_node_components)
                    current_node_components = []

                current_node_components.extend(media_components)

                if current_node_components:
                    is_only_header = (
                        len(current_node_components) == 1
                        and isinstance(current_node_components[0], Plain)
                        and current_node_components[0].text.strip("​\n") in [header, ""]
                    )
                    if not is_only_header:
                        all_nodes_data.append(current_node_components)

            if all_nodes_data:
                processed_batches.append(
                    ProcessedBatch(
                        batch_index=batch_index,
                        nodes_data=all_nodes_data,
                        local_files=all_local_files,
                        contains_audio=sender._batch_contains_audio(all_nodes_data),
                    ).as_batch_data()
                )
            elif media_download_failed:
                target_failures.setdefault(batch_index, "media_download_failed")
            else:
                target_failures.setdefault(batch_index, "preprocess_empty")
        except Exception as e:
            logger.error(f"[QQSender] 预处理消息批次异常: {e}")
            target_failures.setdefault(batch_index, sender._classify_send_error(e))
            sender._cleanup_files(all_local_files)

    return BuildBatchesResult(
        processed_batches=processed_batches,
        target_failures=target_failures,
    )
