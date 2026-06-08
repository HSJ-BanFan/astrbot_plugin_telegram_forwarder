"""QQ 发送器共享类型定义。"""

from typing import Literal, Protocol

from astrbot.api.event import MessageChain

SendKind = Literal[
    "big_merge",
    "album_merge",
    "plain",
    "audio_record",
    "audio_file",
    "video_file",
    "special_media",
    "fallback_link",
    "fallback_zip",
]


class SendMessageFn(Protocol):
    """统一的 QQ 发送封装可调用对象。"""

    async def __call__(
        self,
        unified_msg_origin: str,
        message_chain: MessageChain,
        *,
        send_kind: SendKind,
    ) -> None: ...
