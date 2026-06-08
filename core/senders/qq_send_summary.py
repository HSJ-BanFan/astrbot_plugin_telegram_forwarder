"""QQ 发送结果汇总与清理准备辅助函数。

该模块将发送结果汇总与临时文件收集逻辑从 `QQSender` 主类中拆出，
便于单独测试“哪些批次成功 / 失败 / 延后”以及“有哪些本地文件需要清理”。
"""

from collections.abc import Iterable
from typing import Protocol, TypeVar

from .qq_batch_builder import ProcessedBatchData

QQSendSummaryT = TypeVar("QQSendSummaryT", covariant=True)


class QQSendSummaryFactory(Protocol[QQSendSummaryT]):
    """用于构造 QQ 发送汇总对象的工厂协议。"""

    def __call__(
        self,
        *,
        acked_batch_indexes: tuple[int, ...],
        failed_batch_indexes: tuple[int, ...],
        deferred_batch_indexes: tuple[int, ...],
        error_types: dict[int, str],
    ) -> QQSendSummaryT: ...


def build_send_summary(
    summary_cls: QQSendSummaryFactory[QQSendSummaryT],
    *,
    context_target_sessions: list[str],
    target_successes: dict[int, set[str]],
    target_failures: dict[int, str],
    deferred_batch_indexes: set[int],
) -> QQSendSummaryT:
    """根据每个批次在各目标上的结果构造汇总对象。

    Args:
        summary_cls: 要实例化的汇总 dataclass 类型。
        context_target_sessions: 本次发送参与的全部目标会话。
        target_successes: 以批次索引为键的成功目标集合。
        target_failures: 以批次索引为键的失败原因标签。
        deferred_batch_indexes: 因熔断等原因被延后的批次索引集合。

    Returns:
        包含成功、失败、延后索引及错误类型的汇总实例。
    """

    target_count = len(context_target_sessions)
    acked = tuple(
        batch_index
        for batch_index, success_sessions in target_successes.items()
        if len(success_sessions) == target_count
    )
    deferred = tuple(sorted(deferred_batch_indexes))
    failed = tuple(
        batch_index
        for batch_index, success_sessions in target_successes.items()
        if len(success_sessions) != target_count
        and batch_index not in deferred_batch_indexes
    )
    return summary_cls(
        acked_batch_indexes=acked,
        failed_batch_indexes=failed,
        deferred_batch_indexes=deferred,
        error_types=target_failures,
    )


def collect_processed_batch_local_files(
    processed_batches: Iterable[ProcessedBatchData],
) -> list[str]:
    """收集所有已处理批次中的本地媒体文件路径。

    Args:
        processed_batches: 已处理批次数据字典的可迭代对象。

    Returns:
        按原始批次顺序展平后的文件路径列表。
    """

    return [
        local_file
        for batch_data in processed_batches
        for local_file in batch_data["local_files"]
    ]
