"""QQSender 发送阶段的无状态预处理辅助函数。

该模块负责把发送前需要反复计算的配置解析逻辑收敛到一起，
例如文本处理选项、目标会话解析、熔断阈值与批次展平。
这样可以让 `QQSender.send()` 主流程更聚焦在编排，而不是夹杂大量配置细节。
"""

from collections.abc import Mapping, Sequence
from typing import TypeAlias, TypeGuard, TypeVar, cast

BatchItem = TypeVar("BatchItem")
Batch: TypeAlias = list[BatchItem]
NestedBatches: TypeAlias = list[Batch[BatchItem]]
FlattenableBatches: TypeAlias = Sequence[Batch[BatchItem] | NestedBatches[BatchItem]]


def _as_str_object_mapping(value: object) -> Mapping[str, object]:
    """当值可视为映射时返回映射视图，否则返回空映射。"""
    if isinstance(value, Mapping):
        return value
    return {}


def _is_nested_batches(
    item: Batch[BatchItem] | NestedBatches[BatchItem],
) -> TypeGuard[NestedBatches[BatchItem]]:
    """判断是否为一层嵌套的批次分组，例如 ``[[msg1], [msg2]]``。"""
    return bool(item) and all(isinstance(sub, list) for sub in item)


def resolve_text_processing_options(
    config: Mapping[str, object],
    effective_cfg: Mapping[str, object],
    involved_channels: list[str] | None,
) -> tuple[bool, bool]:
    """解析当前发送流程要使用的文本处理选项。

    Args:
        config: 插件完整配置，可能包含 ``forward_config`` 全局默认值。
        effective_cfg: 当前发送实际生效的频道级 / 任务级配置。
        involved_channels: 当前合并发送涉及的频道列表。

    Returns:
        返回 ``(strip_links, exclude_text_on_media)``。
    """
    if involved_channels and len(involved_channels) > 1:
        global_cfg = _as_str_object_mapping(config.get("forward_config"))
        strip_links = global_cfg.get("strip_markdown_links", False)
        exclude_text_on_media = global_cfg.get("exclude_text_on_media", False)
    else:
        exclude_text_on_media = effective_cfg.get("exclude_text_on_media", False)
        strip_links = effective_cfg.get("strip_markdown_links", False)
    return bool(strip_links), bool(exclude_text_on_media)


def resolve_qq_targets(
    config: Mapping[str, object], effective_cfg: Mapping[str, object]
) -> object:
    """解析当前发送应使用的 QQ 目标配置，优先使用频道级目标。

    Args:
        config: 插件完整配置。
        effective_cfg: 当前发送实际生效的配置。

    Returns:
        从配置中解析出的目标定义对象。
    """
    channel_specific_targets = effective_cfg.get("effective_target_qq_sessions", [])
    if channel_specific_targets:
        return channel_specific_targets
    return config.get("target_qq_session", [])


def normalize_qq_targets(qq_targets: object) -> list | None:
    """把 QQ 目标配置规范化为列表；若类型不支持则返回 ``None``。

    Args:
        qq_targets: 输入的目标配置。

    Returns:
        规范化后的目标列表；若类型不支持则返回 ``None``。
    """
    if isinstance(qq_targets, int):
        return [qq_targets]
    if isinstance(qq_targets, list):
        return qq_targets
    return None


def positive_int(value: object, default: int) -> int:
    """把输入转换为正整数，并保证最小值为 1。

    Args:
        value: 待转换的候选数值。
        default: 转换失败时使用的回退值。

    Returns:
        一个不小于 1 的整数。
    """
    resolved = default

    if isinstance(value, bool):
        resolved = int(value)
    elif isinstance(value, int):
        resolved = value
    elif isinstance(value, float):
        resolved = int(value)
    elif isinstance(value, str):
        try:
            resolved = int(value.strip())
        except ValueError:
            resolved = default

    if resolved < 1:
        return 1
    return resolved


def resolve_send_limits(forward_cfg: Mapping[str, object]) -> tuple[int, int, int]:
    """解析发送循环中的快速失败与熔断参数。

    Args:
        forward_cfg: 当前生效的 ``forward_config`` 配置字典。

    Returns:
        返回 ``(fail_fast_limit, circuit_fail_threshold, circuit_cooldown_sec)``。
    """
    fail_fast_limit = positive_int(
        forward_cfg.get("qq_target_fail_fast_consecutive_failures", 3), 3
    )
    target_circuit_fail_threshold = positive_int(
        forward_cfg.get("target_circuit_fail_threshold", 3), 3
    )
    target_circuit_cooldown_sec = positive_int(
        forward_cfg.get("target_circuit_cooldown_sec", 300), 300
    )
    return (
        fail_fast_limit,
        target_circuit_fail_threshold,
        target_circuit_cooldown_sec,
    )


def flatten_batches(batches: FlattenableBatches[BatchItem]) -> list[Batch[BatchItem]]:
    """把一层嵌套的批次分组展平为逻辑批次列表。

    Args:
        batches: 输入批次列表，其中元素本身也可能是一个批次列表。

    Returns:
        保持原顺序的展平后逻辑批次列表。
    """
    real_batches: list[Batch[BatchItem]] = []
    for item in batches:
        if _is_nested_batches(item):
            real_batches.extend(item)
        else:
            real_batches.append(cast(Batch[BatchItem], item))
    return real_batches
