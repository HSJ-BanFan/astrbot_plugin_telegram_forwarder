"""QQ 目标分发辅助函数。

该模块承接“已经预处理完成”的批次数据，并负责把它们发送到一个或多个 QQ 目标。
这里集中处理大合并、逐批降级、熔断跳过、失败快速停止等发送策略，
避免 `QQSender.send()` 主流程被大量分发表达式淹没。
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Node, Nodes, Record, Video

from .qq_batch_builder import ProcessedBatchData
from .qq_media import _patch_file_to_dict
from .qq_types import SendMessageFn


class SendProcessedBatchFn(Protocol):
    """发送单个已处理批次的可调用协议。"""

    def __call__(
        self,
        *,
        batch_data: ProcessedBatchData,
        unified_msg_origin: str,
        self_id: int,
        node_name: str,
        target_session: str,
        allow_forward_nodes: bool,
    ) -> Awaitable[None]: ...


class RecordTargetFailureFn(Protocol):
    """记录单个目标发送失败的可调用协议。"""

    def __call__(
        self,
        target_session: str,
        *,
        threshold: int,
        cooldown_sec: int,
        now_ts: float,
    ) -> None: ...


@dataclass
class DispatchResult:
    target_successes: dict[int, set[str]]
    target_failures: dict[int, str]
    deferred_batch_indexes: set[int]


async def dispatch_processed_batches_to_targets(
    *,
    context_target_sessions: list[str],
    real_batches: list[list[object]],
    processed_batches: list[ProcessedBatchData],
    target_successes: dict[int, set[str]],
    target_failures: dict[int, str],
    deferred_batch_indexes: set[int],
    use_big_merge: bool,
    is_mixed_big_merge: bool,
    forward_cfg: dict,
    self_id: int,
    node_name: str,
    get_lock: Callable[[str], asyncio.Lock],
    target_is_open: Callable[[str, float], bool],
    record_target_success: Callable[[str], None],
    record_target_failure: RecordTargetFailureFn,
    classify_send_error: Callable[[Exception], str],
    send_processed_batch_fn: SendProcessedBatchFn,
    send_message_fn: SendMessageFn,
    fail_fast_limit: int,
    target_circuit_fail_threshold: int,
    target_circuit_cooldown_sec: int,
    log_policy: object | None = None,
) -> DispatchResult:
    """把预处理后的批次发送到每一个 QQ 目标会话。

    这里是发送策略的核心入口：
    - 逐目标加锁，避免同一群并发写入
    - 熔断目标直接跳过并标记延后
    - 满足条件时走大合并发送，提高吞吐
    - 大合并失败后自动降级为逐批发送
    - 连续失败达到阈值后对当前目标快速止损
    """

    for target_session in context_target_sessions:
        if not target_session:
            continue
        lock = get_lock(target_session)
        async with lock:
            now_ts = time.time()
            if target_is_open(target_session, now_ts):
                logger.warning(
                    f"[QQSender] 目标 {target_session} 熔断冷却中，跳过本轮发送"
                )
                deferred_batch_indexes.update(range(len(real_batches)))
                continue

            unified_msg_origin = target_session

            if use_big_merge or is_mixed_big_merge:
                # ─── 大合并（包括混合模式） ───
                # 按批次边界拆块，避免同一组图片 / 相册 / 回复上下文被拆到不同块
                chunk_size = forward_cfg.get("qq_merge_chunk_size", 5)
                chunk_delay = forward_cfg.get("qq_merge_chunk_delay", 3)
                batch_chunks: list[list[ProcessedBatchData]] = []
                current_chunk_batches: list[ProcessedBatchData] = []
                current_chunk_nodes = 0
                for batch_data in processed_batches:
                    batch_node_count = len(batch_data["nodes_data"])
                    if (
                        current_chunk_nodes + batch_node_count > chunk_size
                        and current_chunk_batches
                    ):
                        batch_chunks.append(current_chunk_batches)
                        current_chunk_batches = []
                        current_chunk_nodes = 0
                    current_chunk_batches.append(batch_data)
                    current_chunk_nodes += batch_node_count
                if current_chunk_batches:
                    batch_chunks.append(current_chunk_batches)

                total_chunks = len(batch_chunks)
                consecutive_failures = 0
                for chunk_idx, chunk_batches in enumerate(batch_chunks, 1):
                    chunk_nodes = []
                    chunk_batch_indexes = [bd["batch_index"] for bd in chunk_batches]
                    for bd in chunk_batches:
                        chunk_nodes.extend(bd["nodes_data"])
                    chunk_has_special_media = any(
                        batch_data.get("contains_audio")
                        or any(
                            isinstance(component, (Record, File, Video))
                            for node_components in batch_data["nodes_data"]
                            for component in node_components
                        )
                        for batch_data in chunk_batches
                    )
                    try:
                        if len(chunk_batches) == 1 or chunk_has_special_media:
                            for batch_data in chunk_batches:
                                await send_processed_batch_fn(
                                    batch_data=batch_data,
                                    unified_msg_origin=unified_msg_origin,
                                    self_id=self_id,
                                    node_name=node_name,
                                    target_session=target_session,
                                    allow_forward_nodes=False,
                                )
                        elif len(chunk_nodes) > 1:
                            nodes_list = [
                                Node(uin=self_id, name=node_name, content=nc)
                                for nc in chunk_nodes
                            ]
                            message_chain = MessageChain()
                            message_chain.chain.append(Nodes(nodes_list))
                            await send_message_fn(
                                unified_msg_origin,
                                message_chain,
                                send_kind="big_merge",
                            )
                        else:
                            await send_processed_batch_fn(
                                batch_data=chunk_batches[0],
                                unified_msg_origin=unified_msg_origin,
                                self_id=self_id,
                                node_name=node_name,
                                target_session=target_session,
                                allow_forward_nodes=False,
                            )
                        for batch_index in chunk_batch_indexes:
                            target_successes[batch_index].add(target_session)
                        record_target_success(target_session)
                        consecutive_failures = 0
                        if log_policy is not None:
                            log_policy.log_merge_send(
                                node_name=node_name,
                                target=target_session,
                                label=f"{'混合' if is_mixed_big_merge else ''}大合并转发",
                                chunk_idx=chunk_idx,
                                total_chunks=total_chunks,
                                node_count=len(chunk_nodes),
                                batch_count=len(chunk_batches),
                            )
                        else:
                            logger.info(
                                f"[QQSender] {node_name} -> {target_session}: "
                                f"{'混合' if is_mixed_big_merge else ''}大合并转发 "
                                f"({chunk_idx}/{total_chunks}, 本块 {len(chunk_nodes)} 节点 / {len(chunk_batches)} 批次)"
                            )
                        if chunk_idx < total_chunks:
                            await asyncio.sleep(chunk_delay)
                    except Exception as e:
                        logger.warning(
                            f"[QQSender] 大合并转发到 {target_session} 失败 "
                            f"(块 {chunk_idx}): {e}，降级为按批次保守发送"
                        )
                        # 降级的目标是“尽可能保住可发送内容”，而不是维持原始的大合并形态。
                        # 因此后续会按批次逐个尝试，把失败影响限制在当前块内部。
                        chunk_failed = False
                        for batch_data in chunk_batches:
                            batch_index = batch_data["batch_index"]
                            if target_session in target_successes.get(batch_index, set()):
                                continue
                            try:
                                await send_processed_batch_fn(
                                    batch_data=batch_data,
                                    unified_msg_origin=unified_msg_origin,
                                    self_id=self_id,
                                    node_name=node_name,
                                    target_session=target_session,
                                    allow_forward_nodes=False,
                                )
                                target_successes[batch_index].add(target_session)
                                record_target_success(target_session)
                                await asyncio.sleep(1)
                            except Exception as e2:
                                chunk_failed = True
                                target_failures.setdefault(
                                    batch_index,
                                    classify_send_error(e2),
                                )
                                record_target_failure(
                                    target_session,
                                    threshold=target_circuit_fail_threshold,
                                    cooldown_sec=target_circuit_cooldown_sec,
                                    now_ts=time.time(),
                                )
                                logger.error(f"[QQSender] 降级批次发送失败: {e2}")
                        if chunk_failed:
                            consecutive_failures += 1
                        else:
                            consecutive_failures = 0
                        if chunk_failed and consecutive_failures >= fail_fast_limit:
                            remaining = sum(
                                len(batch_data["nodes_data"])
                                for chunk in batch_chunks[chunk_idx:]
                                for batch_data in chunk
                            )
                            logger.warning(
                                f"[QQSender] 连续 {consecutive_failures} 块失败，"
                                f"停止本目标剩余 {remaining} 个节点"
                            )
                            break
                        await asyncio.sleep(5)
            else:
                # 普通发送（逐个小相册 / 单条）
                consecutive_failures = 0
                for batch_data in processed_batches:
                    batch_index = batch_data["batch_index"]
                    try:
                        await send_processed_batch_fn(
                            batch_data=batch_data,
                            unified_msg_origin=unified_msg_origin,
                            self_id=self_id,
                            node_name=node_name,
                            target_session=target_session,
                            allow_forward_nodes=True,
                        )
                        target_successes[batch_index].add(target_session)
                        record_target_success(target_session)
                        consecutive_failures = 0
                        await asyncio.sleep(1)
                    except Exception as e:
                        consecutive_failures += 1
                        target_failures.setdefault(
                            batch_index,
                            classify_send_error(e),
                        )
                        record_target_failure(
                            target_session,
                            threshold=target_circuit_fail_threshold,
                            cooldown_sec=target_circuit_cooldown_sec,
                            now_ts=time.time(),
                        )
                        logger.error(f"[QQSender] 转发到 {target_session} 异常: {e}")
                        if consecutive_failures >= fail_fast_limit:
                            logger.warning(
                                f"[QQSender] 目标 {target_session} 连续失败 {consecutive_failures} 次，停止本目标后续批次"
                            )
                            break

    return DispatchResult(
        target_successes=target_successes,
        target_failures=target_failures,
        deferred_batch_indexes=deferred_batch_indexes,
    )


async def send_processed_batch(
    *,
    batch_data: ProcessedBatchData,
    unified_msg_origin: str,
    self_id: int,
    node_name: str,
    target_session: str,
    send_message_fn: SendMessageFn,
    map_path: Callable[[str], str],
    should_merge: Callable[[ProcessedBatchData], bool],
    allow_forward_nodes: bool = True,
    handle_file_send_failure: Callable[
        [File, Exception, ProcessedBatchData, str, str], Awaitable[bool]
    ]
    | None = None,
    log_policy: object | None = None,
) -> None:
    """把单个预处理批次发送到单个 QQ 目标。

    该函数只处理“一个批次发给一个目标”的最小发送单元。
    它会根据批次内容选择合并发送、音频拆分发送或特殊媒体拆分发送，
    以尽量兼顾 QQ 平台兼容性与消息展示完整性。
    """

    def normalize_file_payload(component: File) -> File:
        if getattr(component, "file", None):
            return component
        compat_file = getattr(component, "file_", None)
        if compat_file:
            normalized = File(file=compat_file, name=getattr(component, "name", None))
            if getattr(component, "file_", None) is not None:
                setattr(normalized, "file_", component.file_)
            for attr_name in ("_tgf_source_path",):
                attr_value = getattr(component, attr_name, None)
                if attr_value is None:
                    continue
                try:
                    object.__setattr__(normalized, attr_name, attr_value)
                except Exception:
                    normalized.__dict__[attr_name] = attr_value
            _patch_file_to_dict(normalized)
            return normalized
        return component

    def log_file_payload(component: File) -> None:
        logger.info(
            f"[QQSender] File payload -> {target_session}: "
            f"file={getattr(component, 'file', None)!r}, "
            f"file_={getattr(component, 'file_', None)!r}, "
            f"url={getattr(component, 'url', None)!r}, "
            f"name={getattr(component, 'name', None)!r}"
        )

    def safe_file_size(path: str | None) -> int | None:
        if not path:
            return None
        try:
            return os.path.getsize(path)
        except OSError:
            return None

    all_nodes_data = batch_data["nodes_data"]
    if allow_forward_nodes and should_merge(batch_data):
        message_chain = MessageChain()
        nodes_list = [
            Node(uin=self_id, name=node_name, content=nc) for nc in all_nodes_data
        ]
        message_chain.chain.append(Nodes(nodes_list))
        await send_message_fn(
            unified_msg_origin,
            message_chain,
            send_kind="album_merge",
        )
        if log_policy is not None:
            log_policy.log_merge_send(
                node_name=node_name,
                target=target_session,
                label="相册合并",
                chunk_idx=1,
                total_chunks=1,
                node_count=len(all_nodes_data),
                batch_count=1,
            )
        else:
            logger.info(
                f"[QQSender] {node_name} -> {target_session}: 相册合并 ({len(all_nodes_data)} 节点)"
            )
        return

    if batch_data.get("contains_audio"):
        async def send_common_components(components: list) -> None:
            if not components:
                return
            chain = MessageChain()
            chain.chain.extend(components)
            await send_message_fn(
                unified_msg_origin,
                chain,
                send_kind="plain",
            )

        deferred_common_nodes = []
        for node_components in all_nodes_data:
            common_components = []
            sent_audio = False
            for component in node_components:
                if isinstance(component, Record):
                    sent_audio = True
                    for deferred_components in deferred_common_nodes:
                        await send_common_components(deferred_components)
                    deferred_common_nodes.clear()
                    await send_common_components(common_components)
                    common_components.clear()

                    path = getattr(component, "path", None)
                    try:
                        chain = MessageChain([component])
                        await send_message_fn(
                            unified_msg_origin,
                            chain,
                            send_kind="audio_record",
                        )
                    except Exception as e:
                        if not path:
                            raise
                        logger.warning(
                            f"[QQSender] 语音条发送失败，继续发送源文件: target={target_session}, error_type={type(e).__name__}, error={e!r}"
                        )
                    if path:
                        mapped = map_path(path)
                        file_component = File(
                            file=mapped,
                            url="",
                            name=os.path.basename(path),
                        )
                        _patch_file_to_dict(file_component)
                        log_file_payload(file_component)
                        file_chain = MessageChain([file_component])
                        try:
                            await send_message_fn(
                                unified_msg_origin,
                                file_chain,
                                send_kind="audio_file",
                            )
                        except Exception as fallback_error:
                            logger.error(
                                f"[QQSender] 音频源文件补发失败: target={target_session}, error_type={type(fallback_error).__name__}, error={fallback_error!r}"
                            )
                            raise
                else:
                    if isinstance(component, File):
                        component = normalize_file_payload(component)
                    common_components.append(component)
            if sent_audio:
                await send_common_components(common_components)
                continue
            if common_components:
                deferred_common_nodes.append(common_components)
        for deferred_components in deferred_common_nodes:
            await send_common_components(deferred_components)
        if log_policy is not None:
            log_policy.log_audio_split(
                node_name=node_name,
                target=target_session,
            )
        else:
            logger.info(
                f"[QQSender] {node_name} -> {target_session}: 单条消息 (音频已拆分补文件)"
            )
        return

    special_types = (Record, File, Video)
    batch_has_special = False
    for node_components in all_nodes_data:
        component_types = [type(component).__name__ for component in node_components]
        logger.debug(f"[QQSender] target={target_session} node_types={component_types}")
        has_special = any(isinstance(c, special_types) for c in node_components)
        if has_special:
            batch_has_special = True
            for c in node_components:
                if isinstance(c, special_types):
                    if isinstance(c, File):
                        c = normalize_file_payload(c)
                        log_file_payload(c)
                    if isinstance(c, Video):
                        source_path = getattr(
                            c, "_tgf_source_path", getattr(c, "path", None)
                        )
                        if log_policy is not None:
                            log_policy.log_special_media_ready(
                                target=target_session,
                                batch_index=batch_data.get("batch_index"),
                                node_types=component_types,
                                source_path=source_path,
                                payload_file=getattr(c, "file", None),
                                file_size=safe_file_size(source_path),
                                has_plain_text_same_batch=any(
                                    not isinstance(x, special_types)
                                    for x in node_components
                                ),
                            )
                        else:
                            logger.info(
                                f"[QQSender] Video special media ready: "
                                f"target={target_session}, "
                                f"batch_index={batch_data.get('batch_index')}, "
                                f"node_types={component_types}, "
                                f"source_path={source_path!r}, "
                                f"payload_file={getattr(c, 'file', None)!r}, "
                                f"file_size={safe_file_size(source_path)}, "
                                f"has_plain_text_same_batch={any(not isinstance(x, special_types) for x in node_components)}"
                            )
                    chain = MessageChain([c])
                    try:
                        await send_message_fn(
                            unified_msg_origin,
                            chain,
                            send_kind="special_media",
                        )
                    except Exception as send_error:
                        logger.warning(
                            f"[QQSender] Special media send failed: "
                            f"target={target_session}, "
                            f"batch_index={batch_data.get('batch_index')}, "
                            f"node_types={component_types}, "
                            f"type={type(c).__name__}, "
                            f"file={getattr(c, 'file', None)!r}, "
                            f"file_={getattr(c, 'file_', None)!r}, "
                            f"url={getattr(c, 'url', None)!r}, "
                            f"name={getattr(c, 'name', None)!r}, "
                            f"source_path={getattr(c, '_tgf_source_path', getattr(c, 'path', None))!r}, "
                            f"error_type={type(send_error).__name__}, error={send_error!r}"
                        )
                        if (
                            isinstance(c, File)
                            and handle_file_send_failure is not None
                            and await handle_file_send_failure(
                                c,
                                send_error,
                                batch_data,
                                unified_msg_origin,
                                target_session,
                            )
                        ):
                            continue
                        if isinstance(c, Video):
                            path = getattr(
                                c, "_tgf_source_path", getattr(c, "path", None)
                            )
                            if path:
                                mapped = map_path(path)
                                logger.warning(
                                    f"[QQSender] 视频发送失败，继续发送源文件: target={target_session}, "
                                    f"source_path={path!r}, mapped_path={mapped!r}, "
                                    f"file_size={safe_file_size(path)}, "
                                    f"error_type={type(send_error).__name__}, error={send_error!r}"
                                )
                                file_component = File(
                                    file=mapped,
                                    url="",
                                    name=os.path.basename(path),
                                )
                                _patch_file_to_dict(file_component)
                                log_file_payload(file_component)
                                try:
                                    await send_message_fn(
                                        unified_msg_origin,
                                        MessageChain([file_component]),
                                        send_kind="video_file",
                                    )
                                except Exception as fallback_error:
                                    logger.error(
                                        f"[QQSender] 视频源文件补发失败: target={target_session}, "
                                        f"source_path={path!r}, mapped_path={mapped!r}, "
                                        f"file_size={safe_file_size(path)}, "
                                        f"error_type={type(fallback_error).__name__}, error={fallback_error!r}"
                                    )
                                    raise
                                continue
                        raise
            common_components = [
                c for c in node_components if not isinstance(c, special_types)
            ]
            if common_components:
                chain = MessageChain()
                chain.chain.extend(common_components)
                await send_message_fn(
                    unified_msg_origin,
                    chain,
                    send_kind="plain",
                )
            continue

        message_chain = MessageChain()
        message_chain.chain.extend(node_components)
        await send_message_fn(
            unified_msg_origin,
            message_chain,
            send_kind="plain",
        )

    if batch_has_special:
        if log_policy is not None:
            log_policy.log_target_summary(
                node_name=node_name,
                target=target_session,
                label="单条消息 (已拆分特殊媒体)",
            )
        else:
            logger.info(
                f"[QQSender] {node_name} -> {target_session}: 单条消息 (已拆分特殊媒体)"
            )
        return

    if log_policy is not None:
        log_policy.log_target_summary(
            node_name=node_name,
            target=target_session,
            label="单条普通消息",
        )
    else:
        logger.info(f"[QQSender] {node_name} -> {target_session}: 单条普通消息")
