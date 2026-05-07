"""QQ 发送所需的媒体与路径辅助函数。

负责两类事情：
1. 按运行环境把本地文件路径映射到 AstrBot / NapCat 实际可访问的路径。
2. 按文件后缀把媒体文件转换成对应的 QQ 消息组件。
"""

import os
from collections.abc import Callable
from types import MethodType
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import File, Image, Plain, Record, Video

from .qq_batch_builder import ProcessedBatchData

_ = Plain


def _set_component_attr(component: object, name: str, value: object) -> None:
    """尽力向消息组件附加元数据，兼容宽松/严格两种模型。"""
    try:
        object.__setattr__(component, name, value)
    except Exception:
        try:
            setattr(component, name, value)
        except Exception:
            if hasattr(component, "__dict__"):
                component.__dict__[name] = value


def _as_file_uri(path: str) -> str:
    if "://" in path:
        return path
    if path.startswith("/"):
        return f"file://{path}"
    return f"file:///{path}"


def _safe_file_size(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def map_path_with_config(
    *, fpath: str, context: Any, path_mapping: Callable | None
) -> str:
    """按 AstrBot 的路径映射配置转换本地文件路径。

    当 AstrBot 与 QQ 平台运行在不同宿主环境时，下载得到的本地路径未必能被目标平台直接访问。
    这里统一处理宿主机路径到容器路径的映射，避免具体发送逻辑分散关心环境差异。
    """
    try:
        if path_mapping is None:
            return fpath
        core_config = getattr(context, "_config", None)
        if core_config:
            mappings = core_config.get("platform_settings", {}).get("path_mapping", [])
            if mappings:
                return path_mapping(mappings, fpath)
    except Exception:
        pass
    return fpath


def dispatch_media_file(
    fpath: str,
    *,
    map_path: Callable[[str], str],
    audio_mode: str = "record",
    log_policy: object | None = None,
) -> list[object]:
    """把本地媒体文件路径转换为 AstrBot QQ 消息组件。

    不同媒体类型在 QQ 端支持能力不同，因此这里按后缀做保守映射：
    图片优先转图片组件，音频按配置决定走语音还是文件，视频和普通文件则尽量保留可访问路径。
    """
    ext = os.path.splitext(fpath)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        return [Image.fromFileSystem(fpath)]
    if ext in (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"):
        if audio_mode == "file_only":
            mapped = map_path(fpath)
            component = _patch_file_to_dict(
                File(
                    file=mapped,
                    url="",
                    name=os.path.basename(fpath),
                )
            )
            _set_component_attr(component, "_tgf_source_path", fpath)
            return [component]
        record = Record.fromFileSystem(fpath)
        if getattr(record, "path", None) is None:
            setattr(record, "path", fpath)
        return [record]
    if ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        mapped = map_path(fpath)
        if mapped != fpath and not os.path.isfile(mapped) and os.path.isfile(fpath):
            logger.warning(
                f"[QQSender] Mapped video path not found, fallback to source path: "
                f"source_path={fpath!r}, mapped_path={mapped!r}"
            )
            mapped = fpath
        if mapped != fpath:
            video = Video(file=_as_file_uri(mapped))
        else:
            video = Video.fromFileSystem(fpath)
        _set_component_attr(video, "_tgf_source_path", fpath)
        if log_policy is not None:
            log_policy.log_video_dispatch_prepared(
                source_path=fpath,
                mapped_path=mapped,
                file=getattr(video, "file", None),
                ext=ext,
                file_size=_safe_file_size(fpath),
                mapped_changed=mapped != fpath,
            )
        else:
            logger.info(
                f"[QQSender] Video dispatch prepared: source_path={fpath!r}, "
                f"mapped_path={mapped!r}, file={getattr(video, 'file', None)!r}, "
                f"ext={ext!r}, file_size={_safe_file_size(fpath)}, "
                f"mapped_changed={mapped != fpath}"
            )
        return [video]
    mapped = map_path(fpath)
    if mapped != fpath and not os.path.isfile(mapped) and os.path.isfile(fpath):
        logger.warning(
            f"[QQSender] Mapped file path not found, fallback to source path: "
            f"source_path={fpath!r}, mapped_path={mapped!r}"
        )
        mapped = fpath
    component = _patch_file_to_dict(
        File(
            file=mapped,
            url="",
            name=os.path.basename(fpath),
        )
    )
    _set_component_attr(component, "_tgf_source_path", fpath)
    return [component]


def _patch_file_to_dict(f: File) -> File:
    """修复 AstrBot File.toDict() 输出 file_ 而非 file 的核心 bug。

    AstrBot 的 BaseMessageComponent.toDict() 遍历 __dict__，而 File 类的 file
    是 property（不在 __dict__ 中），导致序列化输出 file_ key。
    OneBot 协议和 aiocqhttp 适配器期望 file key，否则 NapCat 报"文件消息缺少参数"。

    通过直接操作 __dict__ 绕过 Pydantic v1 的 __setattr__ 限制。
    """
    file_value = getattr(f, "file_", "") or ""
    if file_value:
        f.__dict__["file"] = file_value

        # 跨环境路径映射（如 Docker 容器路径 /plugin_data/...）在当前宿主机上
        # 可能就是不可达的。AstrBot 的 File.get_file() 因 os.path.exists() 为
        # false 会丢弃这些路径，导致后续发给 OneBot 的文件载荷为空。
        if "://" not in file_value and not os.path.exists(file_value):

            async def _plugin_to_dict(self: File) -> dict:
                return {
                    "type": "file",
                    "data": {
                        "name": getattr(self, "name", ""),
                        "file": file_value,
                    },
                }

            bound_to_dict = MethodType(_plugin_to_dict, f)
            try:
                object.__setattr__(f, "to_dict", bound_to_dict)
            except Exception:
                f.__dict__["to_dict"] = bound_to_dict
    return f


def batch_contains_audio(nodes_data: list[list[object]]) -> bool:
    """判断批次节点中是否包含语音组件。"""
    return any(
        isinstance(component, Record) for node in nodes_data for component in node
    )


def should_merge_batch_nodes(batch_data: ProcessedBatchData) -> bool:
    """判断一个批次是否适合按合并转发节点发送。

    只有在节点数大于 1，且不包含音频 / 文件 / 视频等兼容性更敏感的媒体时，
    才适合走合并转发，以减少 QQ 侧发送失败或展示异常。
    """
    special_types = (Record, File, Video)
    has_special_media = any(
        isinstance(component, special_types)
        for node in batch_data.get("nodes_data", [])
        for component in node
    )
    return (
        len(batch_data.get("nodes_data", [])) > 1
        and not batch_data.get("contains_audio", False)
        and not has_special_media
    )
