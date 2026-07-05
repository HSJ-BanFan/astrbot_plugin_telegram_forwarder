"""QQ 文件发送失败后的兜底策略辅助函数。"""

import posixpath
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Plain

from .qq_batch_builder import ProcessedBatchData
from .qq_media import _patch_file_to_dict
from .qq_types import SendMessageFn

APK_FALLBACK_MODE_ALIASES = {
    "": "off",
    "off": "off",
    "disabled": "off",
    "close": "off",
    "关闭": "off",
    "禁用": "off",
    "zip": "zip",
    "archive": "zip",
    "压缩包": "zip",
    "压缩": "zip",
    "link": "link",
    "direct_link": "link",
    "url": "link",
    "直链": "link",
    "link_or_zip": "link_or_zip",
    "direct_link_or_zip": "link_or_zip",
    "link-first": "link_or_zip",
    "直链优先，失败转压缩包": "link_or_zip",
    "直链优先失败转压缩包": "link_or_zip",
    "直链或压缩包": "link_or_zip",
}
APK_FALLBACK_EXTENSIONS = {".apk", ".xapk", ".apkm", ".apks"}


@dataclass(frozen=True)
class ApkFallbackPolicy:
    mode: str
    direct_link_base_url: str


def normalize_apk_fallback_mode(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"off", "zip", "link", "link_or_zip"}:
        return raw
    return APK_FALLBACK_MODE_ALIASES.get(raw, "off")


def resolve_apk_fallback_policy(forward_cfg: Mapping[str, object]) -> ApkFallbackPolicy:
    return ApkFallbackPolicy(
        mode=normalize_apk_fallback_mode(
            forward_cfg.get("apk_fallback_mode", "link_or_zip")
        ),
        direct_link_base_url=str(
            forward_cfg.get("apk_direct_link_base_url", "") or ""
        ).strip(),
    )


def _get_file_component_name(component: File) -> str:
    for attr_name in ("name",):
        value = getattr(component, attr_name, None)
        if value:
            return Path(str(value)).name
    for attr_name in ("_tgf_source_path", "file", "file_"):
        value = getattr(component, attr_name, None)
        if value:
            return Path(str(value)).name
    return "download.apk"


def _get_file_component_source_path(component: File) -> str:
    source_path = getattr(component, "_tgf_source_path", None)
    if source_path:
        return str(source_path)
    for attr_name in ("file", "file_"):
        value = getattr(component, attr_name, None)
        if isinstance(value, str) and value and "://" not in value:
            return value
    return ""


def _is_apk_component(component: File) -> bool:
    name = _get_file_component_name(component).lower()
    ext = Path(name).suffix
    if ext in APK_FALLBACK_EXTENSIONS:
        return True
    source_path = _get_file_component_source_path(component).lower()
    source_ext = Path(source_path).suffix
    return source_ext in APK_FALLBACK_EXTENSIONS


def _is_rich_media_failure(
    error: Exception, classify_send_error: Callable[[Exception], str]
) -> bool:
    message = str(error).lower()
    return (
        "rich media transfer failed" in message
        or "retcode=1200" in message
        or classify_send_error(error) == "retcode_1200"
    )


def _is_rich_media_upload_reject(error: Exception) -> bool:
    """判定 QQ 端已读到文件但富媒体上传被拒（风控/服务端限制）。

    这类失败与路径拼写无关（ENOENT 类错误不会出现该 errMsg），
    换路径重发同一份字节必然同样被拒。
    """
    return "rich media transfer failed" in str(error).lower()


def _is_apk_rich_media_failure(
    error: Exception, classify_send_error: Callable[[Exception], str]
) -> bool:
    return _is_rich_media_failure(error, classify_send_error)


def _build_direct_link(base_url: str, filename: str) -> str:
    parts = urlsplit(base_url)
    base_path = parts.path or "/"
    if not base_path.endswith("/"):
        base_path += "/"
    full_path = posixpath.join(base_path, quote(filename))
    return urlunsplit(
        (parts.scheme, parts.netloc, full_path, parts.query, parts.fragment)
    )


def _build_unmapped_file_component(local_path: str, display_name: str) -> File:
    component = File(
        file=local_path,
        url="",
        name=display_name,
    )
    try:
        object.__setattr__(component, "_tgf_source_path", local_path)
    except Exception:
        component.__dict__["_tgf_source_path"] = local_path
    return component


def _build_file_component(
    local_path: str,
    display_name: str,
    *,
    map_path: Callable[[str], str],
) -> File:
    mapped = map_path(local_path)
    component = _patch_file_to_dict(
        File(
            file=mapped,
            url="",
            name=display_name,
        )
    )
    try:
        object.__setattr__(component, "_tgf_source_path", local_path)
    except Exception:
        component.__dict__["_tgf_source_path"] = local_path
    return component


def _create_apk_zip_archive(
    source_path: str, original_name: str, *, plugin_data_dir: Path | None
) -> tuple[str, str]:
    source = Path(source_path)
    target_dir = plugin_data_dir if plugin_data_dir else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{original_name}.zip"
    archive_path = target_dir / archive_name
    suffix = 1
    while archive_path.exists():
        archive_name = f"{original_name}.{suffix}.zip"
        archive_path = target_dir / archive_name
        suffix += 1
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.write(source, arcname=original_name)
    return str(archive_path), archive_name


async def _try_non_apk_file_fallback(
    *,
    forward_cfg: Mapping[str, object],
    component: File,
    error: Exception,
    unified_msg_origin: str,
    target_session: str,
    send_message_fn: SendMessageFn,
    map_path: Callable[[str], str],
) -> bool:
    original_name = _get_file_component_name(component)
    direct_link_base_url = str(
        forward_cfg.get("file_direct_link_base_url", "") or ""
    ).strip()
    if direct_link_base_url:
        direct_link = _build_direct_link(direct_link_base_url, original_name)
        try:
            await send_message_fn(
                unified_msg_origin,
                MessageChain(
                    [
                        Plain(
                            f"原文件 {original_name} 因 QQ 文件上传失败，改发直链：{direct_link}"
                        )
                    ]
                ),
                send_kind="fallback_link",
            )
        except Exception as link_error:
            logger.warning(
                f"[QQSender] 文件直链降级发送失败，继续尝试源文件路径兜底: "
                f"target={target_session}, file={original_name}, "
                f"original_error={error!r}, fallback_error={link_error!r}"
            )
        else:
            logger.warning(
                f"[QQSender] 文件发送失败，已降级为直链: "
                f"target={target_session}, file={original_name}"
            )
            return True

    source_path = _get_file_component_source_path(component)
    if not source_path or not Path(source_path).is_file():
        logger.warning(
            f"[QQSender] 文件发送失败且缺少可访问源文件，无法源文件兜底: "
            f"target={target_session}, file={original_name}, source={source_path!r}"
        )
        return False

    # 富媒体上传被拒说明 QQ 端已成功读到文件字节，换路径拼写重发无济于事；
    # 直接交还 pending 队列按周期重试（瞬时风控可自愈）。
    if _is_rich_media_upload_reject(error):
        logger.warning(
            f"[QQSender] QQ 已读取文件但富媒体上传被拒（疑似风控/服务端限制），"
            f"跳过源文件路径兜底，交还队列重试: "
            f"target={target_session}, file={original_name}, source={source_path!r}, "
            f"error={error!r}"
        )
        return False

    # 源路径与主发送载荷完全相同（未配置映射或映射未命中）时，原样重发必然同结果。
    if map_path(source_path) == source_path:
        logger.warning(
            f"[QQSender] 源文件路径与主发送载荷一致，重发无意义，跳过源文件路径兜底"
            f"（若 QQ 端与插件不同环境，请检查 platform_settings.path_mapping）: "
            f"target={target_session}, file={original_name}, source={source_path!r}"
        )
        return False

    source_component = _build_unmapped_file_component(source_path, original_name)
    try:
        await send_message_fn(
            unified_msg_origin,
            MessageChain([source_component]),
            send_kind="fallback_file_source",
        )
    except Exception as source_error:
        logger.warning(
            f"[QQSender] 源文件路径兜底发送失败: target={target_session}, "
            f"file={original_name}, source={source_path!r}, "
            f"original_error={error!r}, fallback_error={source_error!r}"
        )
        return False

    logger.warning(
        f"[QQSender] 文件发送失败，已使用源文件路径兜底重试: "
        f"target={target_session}, file={original_name}, source={source_path!r}"
    )
    return True


async def handle_apk_file_send_failure(
    *,
    policy: ApkFallbackPolicy,
    component: File,
    error: Exception,
    batch_data: ProcessedBatchData,
    unified_msg_origin: str,
    target_session: str,
    send_message_fn: SendMessageFn,
    map_path: Callable[[str], str],
    classify_send_error: Callable[[Exception], str],
    plugin_data_dir: Path | None,
) -> bool:
    if policy.mode == "off" or not _is_apk_component(component):
        return False
    if not _is_apk_rich_media_failure(error, classify_send_error):
        return False

    original_name = _get_file_component_name(component)

    if policy.mode in {"link", "link_or_zip"} and policy.direct_link_base_url:
        direct_link = _build_direct_link(policy.direct_link_base_url, original_name)
        try:
            await send_message_fn(
                unified_msg_origin,
                MessageChain(
                    [
                        Plain(
                            f"原文件 {original_name} 因 QQ 文件限制无法直传，改发直链：{direct_link}"
                        )
                    ]
                ),
                send_kind="fallback_link",
            )
        except Exception as link_error:
            if policy.mode == "link":
                raise
            logger.warning(
                f"[QQSender] APK 文件直链降级发送失败，继续尝试压缩包兜底: target={target_session}, file={original_name}, error={link_error}"
            )
        else:
            logger.warning(
                f"[QQSender] APK 文件发送失败，已降级为直链: target={target_session}, file={original_name}"
            )
            return True

    if policy.mode not in {"zip", "link_or_zip"}:
        return False

    source_path = _get_file_component_source_path(component)
    if not source_path or not Path(source_path).is_file():
        logger.warning(
            f"[QQSender] APK 文件发送失败且缺少可访问源文件，无法压缩兜底: target={target_session}, file={original_name}, source={source_path!r}"
        )
        return False

    archive_path, archive_name = _create_apk_zip_archive(
        source_path, original_name, plugin_data_dir=plugin_data_dir
    )
    batch_data.setdefault("local_files", []).append(archive_path)
    archive_component = _build_file_component(
        archive_path, archive_name, map_path=map_path
    )
    await send_message_fn(
        unified_msg_origin,
        MessageChain([archive_component]),
        send_kind="fallback_zip",
    )
    logger.warning(
        f"[QQSender] APK 文件发送失败，已降级为压缩包: target={target_session}, file={original_name}, archive={archive_name}"
    )
    return True


async def handle_file_send_failure(
    *,
    forward_cfg: Mapping[str, object],
    apk_policy: ApkFallbackPolicy,
    component: File,
    error: Exception,
    batch_data: ProcessedBatchData,
    unified_msg_origin: str,
    target_session: str,
    send_message_fn: SendMessageFn,
    map_path: Callable[[str], str],
    classify_send_error: Callable[[Exception], str],
    plugin_data_dir: Path | None,
) -> bool:
    if not _is_rich_media_failure(error, classify_send_error):
        return False
    if _is_apk_component(component):
        return await handle_apk_file_send_failure(
            policy=apk_policy,
            component=component,
            error=error,
            batch_data=batch_data,
            unified_msg_origin=unified_msg_origin,
            target_session=target_session,
            send_message_fn=send_message_fn,
            map_path=map_path,
            classify_send_error=classify_send_error,
            plugin_data_dir=plugin_data_dir,
        )
    return await _try_non_apk_file_fallback(
        forward_cfg=forward_cfg,
        component=component,
        error=error,
        unified_msg_origin=unified_msg_origin,
        target_session=target_session,
        send_message_fn=send_message_fn,
        map_path=map_path,
    )
