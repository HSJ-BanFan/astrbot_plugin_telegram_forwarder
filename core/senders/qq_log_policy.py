"""QQ 发送日志策略。

将 QQ 发送路径中原本散落的 `logger.info(...)` 集中管理，
并通过 debug 开关控制是否输出诊断性日志，避免正常运行时日志过于冗长。
"""

from collections.abc import Callable

from astrbot.api import logger


class QQLogPolicy:
    """根据 debug 开关决定是否输出发送路径诊断日志。

    Args:
        debug_enabled: 返回当前是否启用 debug 模式的可调用对象。
    """

    def __init__(self, debug_enabled: Callable[[], bool]) -> None:
        self._debug_enabled = debug_enabled

    def log_send_success(
        self,
        *,
        send_kind: str,
        target: str,
        component_types: list[str],
        payload_file: object,
        source_path: object,
        duration: float,
    ) -> None:
        """发送成功的详细日志（debug 模式下才输出）。

        Args:
            send_kind: 发送类型标识。
            target: 目标会话标识。
            component_types: 消息组件类型名列表。
            payload_file: 消息组件的 file 属性。
            source_path: 媒体源文件路径。
            duration: 发送耗时（秒）。
        """
        if not self._debug_enabled():
            return
        logger.info(
            f"[QQSender] send kind={send_kind} target={target} "
            f"component_types={component_types} payload_file={payload_file!r} "
            f"source_path={source_path!r} duration={duration:.3f}s"
        )

    def log_video_dispatch_prepared(
        self,
        *,
        source_path: str,
        mapped_path: str,
        file: object,
        ext: str,
        file_size: int | None,
        mapped_changed: bool,
    ) -> None:
        """视频分发准备完成的详细日志（debug 模式下才输出）。

        Args:
            source_path: 原始视频文件路径。
            mapped_path: 映射后的文件路径。
            file: Video 组件的 file 属性。
            ext: 文件扩展名。
            file_size: 文件大小（字节）。
            mapped_changed: 映射路径是否与原始路径不同。
        """
        if not self._debug_enabled():
            return
        logger.info(
            f"[QQSender] Video dispatch prepared: source_path={source_path!r}, "
            f"mapped_path={mapped_path!r}, file={file!r}, "
            f"ext={ext!r}, file_size={file_size}, "
            f"mapped_changed={mapped_changed}"
        )

    def log_special_media_ready(
        self,
        *,
        target: str,
        batch_index: object,
        node_types: list[str],
        source_path: object,
        payload_file: object,
        file_size: int | None,
        has_plain_text_same_batch: bool,
    ) -> None:
        """特殊媒体准备就绪的详细日志（debug 模式下才输出）。

        Args:
            target: 目标会话标识。
            batch_index: 批次索引。
            node_types: 节点组件类型名列表。
            source_path: 媒体源文件路径。
            payload_file: 媒体组件的 file 属性。
            file_size: 文件大小（字节）。
            has_plain_text_same_batch: 同批次是否包含纯文本。
        """
        if not self._debug_enabled():
            return
        logger.info(
            f"[QQSender] Video special media ready: "
            f"target={target}, "
            f"batch_index={batch_index}, "
            f"node_types={node_types}, "
            f"source_path={source_path!r}, "
            f"payload_file={payload_file!r}, "
            f"file_size={file_size}, "
            f"has_plain_text_same_batch={has_plain_text_same_batch}"
        )

    def log_merge_send(
        self,
        *,
        node_name: str,
        target: str,
        label: str,
        chunk_idx: int,
        total_chunks: int,
        node_count: int,
        batch_count: int,
    ) -> None:
        """合并转发成功日志（debug 模式下才输出）。

        Args:
            node_name: bot 昵称。
            target: 目标会话标识。
            label: 合并类型完整标签（如 "大合并转发"、"混合大合并转发"、"相册合并"）。
            chunk_idx: 当前块索引。
            total_chunks: 总块数。
            node_count: 本块节点数。
            batch_count: 本块批次数。
        """
        if not self._debug_enabled():
            return
        logger.info(
            f"[QQSender] {node_name} -> {target}: {label} "
            f"({chunk_idx}/{total_chunks}, 本块 {node_count} 节点 / {batch_count} 批次)"
        )

    def log_audio_split(
        self,
        *,
        node_name: str,
        target: str,
    ) -> None:
        """音频拆分发送成功日志（debug 模式下才输出）。

        Args:
            node_name: bot 昵称。
            target: 目标会话标识。
        """
        if not self._debug_enabled():
            return
        logger.info(f"[QQSender] {node_name} -> {target}: 单条消息 (音频已拆分补文件)")

    def log_target_summary(
        self,
        *,
        node_name: str,
        target: str,
        label: str,
    ) -> None:
        """目标发送摘要日志（始终输出）。

        Args:
            node_name: bot 昵称。
            target: 目标会话标识。
            label: 发送结果描述。
        """
        logger.info(f"[QQSender] {node_name} -> {target}: {label}")
